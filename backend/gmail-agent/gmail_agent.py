"""Gmail agent: reconcile recent job-application completion emails to the DB.

Public async entry point ``sync_recent_threads`` is called both by the 2-hour
scheduler (``scheduler.py``) and by the ``POST /api/gmail/sync`` route, and wraps
the CLI. It returns only JSON-serializable data, matching the repo convention.

Pipeline (see backend/gmail-agent/DOCUMENTATION.md):
  1. fetch recent threads via the Gmail MCP server (mcp_client)
  2. classify -> keep only job-application completion emails (classifier)
  3. for each, skip if its thread is already linked (idempotency guard)
  4. match against open rows (thread_id IS NULL) -> best row or none (matcher)
  5. attach thread + mark completed, or insert a new row (persistence)
"""

from __future__ import annotations

import sys as _sys, pathlib as _pathlib

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # backend/

import argparse
import asyncio
import json
import os
from typing import Any

from common.db import connect
from common.models import DEFAULT_GEMINI_MODEL

from gmail_classifier import classify_threads
from gmail_matcher import match_emails
from gmail_mcp_client import auth_status, search_recent_threads
from gmail_persistence import (
    attach_thread_to_row,
    ensure_schema,
    fetch_open_candidates,
    insert_new_row,
    resolve_default_user_id,
    thread_exists,
)


def _load_env() -> None:
    """Load gmail-agent/.env then backend/.env (without overriding real env).

    The umbrella app loads these in main.py; the CLI must do it itself so
    ``python gmail_agent.py ...`` sees DATABASE_URL/POSTGRES_URL/GEMINI_API_KEY
    and the GMAIL_* settings.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = _pathlib.Path(__file__).resolve().parent
    for dotenv_path in (here / ".env", here.parent / ".env"):  # gmail-agent/, backend/
        load_dotenv(dotenv_path=dotenv_path, override=False)


def _resolve_dsn(dsn: str | None) -> str:
    resolved = dsn or os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if not resolved:
        raise RuntimeError(
            "No database DSN: set DATABASE_URL or POSTGRES_URL, or pass dsn."
        )
    return resolved


def _build_metadata(email: dict) -> dict:
    """Shape an email's extracted fields into the row's ``metadata`` jsonb."""
    return {
        "source": "gmail-agent",
        "company": email.get("company", ""),
        "role": email.get("role", ""),
        "status": email.get("status", ""),
        "subject": email.get("subject", ""),
        "sender": email.get("sender", ""),
        "snippet": email.get("snippet", ""),
        "thread_id": email.get("thread_id", ""),
    }


async def sync_recent_threads(
    hours: int = 24,
    user_id: str | None = None,
    *,
    dsn: str | None = None,
    gemini_api_key: str | None = None,
    model: str = DEFAULT_GEMINI_MODEL,
) -> dict:
    """Scan the last ``hours`` of Gmail and reconcile completion emails to rows.

    Returns a summary: ``{scanned, qualified, skipped_existing, matched,
    created, errors}``. Idempotent — threads already linked to a row are skipped,
    so calling it repeatedly over the same window changes nothing new.
    """
    from google import genai

    dsn = _resolve_dsn(dsn)
    gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        raise RuntimeError("No GEMINI_API_KEY configured.")

    summary = {
        "scanned": 0,
        "qualified": 0,
        "skipped_existing": 0,
        "matched": 0,
        "created": 0,
        "errors": [],
    }

    threads = await search_recent_threads(hours=hours)
    summary["scanned"] = len(threads)

    ai_client = genai.Client(api_key=gemini_api_key).aio
    qualifying = await classify_threads(ai_client, threads, model=model)
    summary["qualified"] = len(qualifying)
    if not qualifying:
        return summary

    conn = await connect(dsn)
    try:
        await ensure_schema(conn)
        uid = (
            user_id or os.getenv("UPJOB_USER_ID") or await resolve_default_user_id(conn)
        )
        async with conn.transaction():
            candidates = await fetch_open_candidates(conn, uid)
            decisions = await match_emails(
                ai_client, qualifying, candidates, model=model
            )
            for email in qualifying:
                thread_id = email["thread_id"]
                try:
                    if await thread_exists(conn, thread_id):
                        summary["skipped_existing"] += 1
                        continue
                    ctid = decisions.get(thread_id)
                    attached = False
                    if ctid is not None:
                        attached = await attach_thread_to_row(conn, ctid, thread_id)
                    if attached:
                        summary["matched"] += 1
                    else:
                        await insert_new_row(
                            conn, uid, _build_metadata(email), thread_id
                        )
                        summary["created"] += 1
                except Exception as exc:  # noqa: BLE001 - one bad email must not abort the batch
                    summary["errors"].append(
                        {"thread_id": thread_id, "error": str(exc)}
                    )
    finally:
        await conn.close()
    return summary


async def authorize() -> dict:
    """Trigger/verify Gmail OAuth by performing a minimal thread search."""
    await search_recent_threads(hours=1)
    return auth_status()


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

try:  # Router-only dependency; keep it optional so the CLI and tests run without it.
    from pydantic import BaseModel as _BaseModel

    class SyncBody(_BaseModel):
        """Request body for ``POST /api/gmail/sync``.

        Defined at module scope (not inside ``create_fastapi_router``) on
        purpose: this module uses ``from __future__ import annotations``, so
        FastAPI resolves the route's ``body: SyncBody`` hint via
        ``get_type_hints`` against the module globals. A function-local model is
        invisible there, so FastAPI falls back to treating ``body`` as a query
        parameter and returns 422 ("Field required") for *every* request.
        """

        hours: int = 24
        user_id: str | None = None
except ImportError:  # pragma: no cover - only in minimal environments without pydantic
    SyncBody = None  # type: ignore[assignment,misc]


def create_fastapi_router(
    dsn: str | None = None, gemini_api_key: str | None = None
) -> Any:
    """APIRouter exposing the sync + auth-status routes under ``/api``."""
    try:
        from fastapi import APIRouter, HTTPException
    except ImportError as exc:
        raise RuntimeError(
            "FastAPI and Pydantic must be installed to create router."
        ) from exc
    if SyncBody is None:
        raise RuntimeError("FastAPI and Pydantic must be installed to create router.")

    router = APIRouter(prefix="/api", tags=["gmail"])

    @router.post("/gmail/sync")
    async def sync(body: SyncBody):
        try:
            return await sync_recent_threads(
                hours=body.hours,
                user_id=body.user_id,
                dsn=dsn,
                gemini_api_key=gemini_api_key,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/gmail/auth/status")
    async def status():
        return auth_status()

    return router


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmail_agent",
        description="Reconcile recent Gmail job-application completion emails to the DB.",
    )
    parser.add_argument("--dsn", default=None, help="PostgreSQL connection string")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_sync = subparsers.add_parser("sync", help="Scan the last N hours and reconcile")
    p_sync.add_argument(
        "--hours", type=int, default=24, help="Look-back window in hours"
    )
    p_sync.add_argument(
        "--user-id", default=None, help="Application owner user_id (uuid)"
    )

    subparsers.add_parser("auth", help="Run/verify the Gmail OAuth authorization")
    return parser


async def _amain(args: argparse.Namespace) -> int:
    if args.subcommand == "auth":
        result = await authorize()
    else:
        result = await sync_recent_threads(
            hours=args.hours, user_id=args.user_id, dsn=args.dsn
        )
    print(json.dumps(result, indent=2, default=str))
    return 0


def main() -> int:
    _load_env()
    args = _build_parser().parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
