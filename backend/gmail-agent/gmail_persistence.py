"""Database layer for the Gmail agent over the live ``job_applications`` table.

The table already exists in the TigerCloud instance (created out-of-band) and has
no primary key, so rows are addressed by their physical ``ctid`` within a single
transaction. Columns (live schema):

    user_id uuid, job_id uuid, aplied_date timestamptz (sic), metadata jsonb,
    is_completed boolean, thread_id text  (retyped from uuid by migration 003)

Follows the repo convention: lazy asyncpg import via ``common.db.connect`` and an
``ensure_schema`` that globs ``migrations/*.sql``.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # backend/

import json
import logging
from pathlib import Path
from typing import Any

from common.db import connect, normalize_dsn, ssl_argument  # noqa: F401

logger = logging.getLogger(__name__)


async def ensure_schema(conn: Any, migrations_dir: Path | None = None) -> None:
    """Apply all ``migrations/*.sql`` in sorted order (idempotent migrations)."""
    if migrations_dir is None:
        migrations_dir = Path(__file__).parent / "migrations"
    for migration_file in sorted(migrations_dir.glob("*.sql")):
        await conn.execute(migration_file.read_text())
        logger.info("Schema migration applied: %s", migration_file)


async def resolve_default_user_id(conn: Any) -> str | None:
    """Return the sole ``app_users`` row id, used when no user_id is supplied.

    Ambiguous if multiple users exist; the agent is single-tenant for now.
    """
    row = await conn.fetchrow("SELECT user_id FROM app_users ORDER BY created_at LIMIT 1")
    return str(row["user_id"]) if row else None


async def thread_exists(conn: Any, thread_id: str) -> bool:
    """True if any application row is already linked to this Gmail thread.

    This is the idempotency guard: a thread seen on a previous run is skipped so
    re-invoking the sync endpoint never touches extra rows.
    """
    row = await conn.fetchrow(
        "SELECT 1 FROM job_applications WHERE thread_id = $1 LIMIT 1", thread_id
    )
    return row is not None


async def fetch_open_candidates(conn: Any, user_id: str | None) -> list[dict]:
    """Open application rows (no thread_id yet), with context for matching.

    Returns one dict per row with its ``ctid`` (stable within the enclosing
    transaction) and company/role pulled from ``metadata`` first, then from a
    joined ``jobs`` row when ``job_id`` resolves. Scoped to ``user_id`` when given.
    """
    where_user = "AND ja.user_id = $1::uuid" if user_id else ""
    params = [user_id] if user_id else []
    rows = await conn.fetch(
        f"""
        SELECT ja.ctid::text AS ctid,
               ja.user_id::text AS user_id,
               ja.job_id::text AS job_id,
               ja.metadata AS metadata,
               j.title AS job_title,
               j.url AS job_url
        FROM job_applications ja
        LEFT JOIN jobs j ON j.job_id = ja.job_id
        WHERE ja.thread_id IS NULL {where_user}
        """,
        *params,
    )
    candidates: list[dict] = []
    for i, row in enumerate(rows):
        metadata = _load_json(row["metadata"])
        company = _first_str(metadata, ("company", "employer", "organization"))
        role = _first_str(metadata, ("role", "title", "position")) or (row["job_title"] or "")
        candidates.append(
            {
                "index": i,
                "ctid": row["ctid"],
                "user_id": row["user_id"],
                "company": company,
                "role": role,
                "metadata": metadata,
            }
        )
    return candidates


async def attach_thread_to_row(conn: Any, ctid: str, thread_id: str) -> bool:
    """Link a thread to one still-open row and mark it completed.

    Guarded by ``thread_id IS NULL`` so two emails can't both claim one row;
    returns False (0 rows updated) when the row was already taken, letting the
    caller fall back to inserting a new row.
    """
    status = await conn.execute(
        """
        UPDATE job_applications
        SET thread_id = $2, is_completed = true
        WHERE ctid = $1::tid AND thread_id IS NULL
        """,
        ctid,
        thread_id,
    )
    # asyncpg returns e.g. "UPDATE 1"
    return status.rsplit(" ", 1)[-1] != "0"


async def insert_new_row(
    conn: Any, user_id: str | None, metadata: dict, thread_id: str
) -> None:
    """Insert a completed application row for an email with no matching record.

    ``job_id`` is left NULL (there may be no corresponding ``jobs`` row; the table
    has no FK constraint), with all extracted detail preserved in ``metadata``.
    """
    await conn.execute(
        """
        INSERT INTO job_applications (user_id, job_id, aplied_date, metadata, is_completed, thread_id)
        VALUES ($1::uuid, NULL, now(), $2::jsonb, true, $3)
        """,
        user_id,
        json.dumps(metadata),
        thread_id,
    )


def _load_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _first_str(metadata: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
