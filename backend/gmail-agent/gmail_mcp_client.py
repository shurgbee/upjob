"""Gmail access: fetch recent threads via the Gmail MCP server (or REST fallback).

Primary transport is the official Google Gmail MCP server
(https://gmailmcp.googleapis.com/mcp/v1, remote streamable-HTTP, OAuth 2.0), whose
``search_threads`` tool takes a Gmail ``q`` query and returns threads with ids and
message metadata (subject, snippet, sender). The REST fallback hits the Gmail API
directly through google-api-python-client with the same OAuth scope, producing an
identical normalized shape so the rest of the pipeline is transport-agnostic.

Both produce a list of dicts:
    {"thread_id": str, "subject": str, "sender": str, "snippet": str}

Select the transport with ``GMAIL_TRANSPORT`` (``mcp`` default, or ``rest``).

Time window: Gmail's relative ``newer_than:`` only supports day/month/year units,
so for a sub-day window we use ``newer:<epoch-seconds>`` (the absolute "newer"
operator), which both transports accept.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GMAIL_MCP_URL = os.getenv("GMAIL_MCP_URL", "https://gmailmcp.googleapis.com/mcp/v1")
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_BASE_DIR = Path(__file__).parent
REST_TOKEN_PATH = Path(os.getenv("GMAIL_TOKEN_PATH", _BASE_DIR / ".gmail_token.json"))
REST_CLIENT_SECRET_PATH = Path(
    os.getenv("GMAIL_CLIENT_SECRET_PATH", _BASE_DIR / ".gmail_client_secret.json")
)


def gmail_query(hours: int) -> str:
    """Gmail search query selecting threads newer than ``hours`` ago."""
    epoch = int(time.time() - hours * 3600)
    return f"newer:{epoch}"


async def search_recent_threads(hours: int = 2) -> list[dict]:
    """Return normalized recent threads via the configured transport."""
    transport = os.getenv("GMAIL_TRANSPORT", "mcp").strip().lower()
    query = gmail_query(hours)
    if transport == "rest":
        return await asyncio.to_thread(_rest_search_threads, query)
    return await _mcp_search_threads(query)


def auth_status() -> dict:
    """Report whether usable OAuth credentials are present for the transport."""
    transport = os.getenv("GMAIL_TRANSPORT", "mcp").strip().lower()
    if transport == "rest":
        return {"transport": "rest", "authorized": REST_TOKEN_PATH.exists()}
    mcp_token = Path(
        os.getenv("GMAIL_MCP_TOKEN_PATH", _BASE_DIR / ".gmail_mcp_token.json")
    )
    return {"transport": "mcp", "authorized": mcp_token.exists()}


# ---------------------------------------------------------------------------
# MCP transport (primary)
# ---------------------------------------------------------------------------

async def _mcp_search_threads(query: str) -> list[dict]:
    """Call ``search_threads`` on the Gmail MCP server and normalize the result.

    Uses the ``mcp`` Python SDK's streamable-HTTP client with an OAuth provider.
    First use opens an interactive browser consent; the token is persisted and
    refreshed thereafter. Requires ``GMAIL_OAUTH_CLIENT_ID`` /
    ``GMAIL_OAUTH_CLIENT_SECRET`` for the registered Google OAuth client.
    """
    try:
        import httpx2
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:  # pragma: no cover - exercised only live
        raise RuntimeError(
            "The 'mcp' package is required for GMAIL_TRANSPORT=mcp. "
            "Install it or set GMAIL_TRANSPORT=rest."
        ) from exc

    # The OAuthClientProvider is an httpx auth flow; this mcp version attaches it
    # to the HTTP client rather than accepting an auth= argument.
    auth = _build_mcp_oauth_provider()
    async with httpx2.AsyncClient(auth=auth) as http_client:
        async with streamable_http_client(GMAIL_MCP_URL, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "search_threads",
                    {"query": query, "view": "THREAD_VIEW_MINIMAL"},
                )
    return _normalize_mcp_result(result)


def _build_mcp_oauth_provider() -> Any:
    """Construct the MCP OAuth client provider with on-disk token storage.

    Google requires a pre-registered OAuth client, so the fixed client_id/secret
    are seeded into storage as ``client_info`` (see ``_FileTokenStorage``) rather
    than obtained via dynamic client registration.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    if not os.getenv("GMAIL_OAUTH_CLIENT_ID"):
        raise RuntimeError(
            "GMAIL_OAUTH_CLIENT_ID is required for the Gmail MCP transport. "
            "Register a Google Cloud OAuth client or set GMAIL_TRANSPORT=rest."
        )
    redirect_port = int(os.getenv("GMAIL_OAUTH_REDIRECT_PORT", "8765"))
    redirect_uri = f"http://localhost:{redirect_port}/callback"

    metadata = OAuthClientMetadata(
        client_name="upjob-gmail-agent",
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=" ".join(GMAIL_SCOPES),
        token_endpoint_auth_method="client_secret_post",
    )
    storage = _FileTokenStorage(
        Path(os.getenv("GMAIL_MCP_TOKEN_PATH", _BASE_DIR / ".gmail_mcp_token.json")),
        redirect_uri,
    )
    return OAuthClientProvider(
        server_url=GMAIL_MCP_URL,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=_open_browser,
        callback_handler=lambda: _wait_for_oauth_callback(redirect_port),
    )


async def _open_browser(url: str) -> None:  # pragma: no cover - interactive
    import webbrowser

    logger.info("Opening browser for Gmail authorization: %s", url)
    webbrowser.open(url)


async def _wait_for_oauth_callback(port: int) -> Any:  # pragma: no cover
    """Run a one-shot local HTTP server to capture the OAuth redirect."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    from mcp.shared.auth import AuthorizationCodeResult

    captured: dict[str, str | None] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            params = parse_qs(urlparse(self.path).query)
            captured["code"] = params.get("code", [None])[0]
            captured["state"] = params.get("state", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization complete. You can close this tab.")

        def log_message(self, *_args):  # silence default logging
            pass

    server = HTTPServer(("localhost", port), _Handler)
    await asyncio.to_thread(server.handle_request)
    server.server_close()
    return AuthorizationCodeResult(
        code=captured.get("code") or "", state=captured.get("state")
    )


class _FileTokenStorage:  # pragma: no cover - exercised only live
    """Minimal ``mcp.client.auth.TokenStorage`` backed by a JSON file.

    ``get_client_info`` falls back to the fixed Google OAuth client from the
    environment when nothing is stored, so the pre-registered client_id/secret are
    used instead of dynamic client registration (which Google does not support).
    """

    def __init__(self, path: Path, redirect_uri: str) -> None:
        self._path = path
        self._redirect_uri = redirect_uri

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text()).get("tokens")
        return OAuthToken(**data) if data else None

    async def set_tokens(self, tokens) -> None:
        payload = json.loads(self._path.read_text()) if self._path.exists() else {}
        payload["tokens"] = tokens.model_dump(exclude_none=True)
        self._path.write_text(json.dumps(payload))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        if self._path.exists():
            data = json.loads(self._path.read_text()).get("client_info")
            if data:
                return OAuthClientInformationFull(**data)
        client_id = os.getenv("GMAIL_OAUTH_CLIENT_ID")
        if not client_id:
            return None
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=os.getenv("GMAIL_OAUTH_CLIENT_SECRET"),
            redirect_uris=[self._redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(GMAIL_SCOPES),
            token_endpoint_auth_method="client_secret_post",
        )

    async def set_client_info(self, client_info) -> None:
        payload = json.loads(self._path.read_text()) if self._path.exists() else {}
        payload["client_info"] = client_info.model_dump(exclude_none=True)
        self._path.write_text(json.dumps(payload))


def _normalize_mcp_result(result: Any) -> list[dict]:
    """Coerce an MCP ``CallToolResult`` into the normalized thread list.

    Tolerates shape variation: reads ``structuredContent`` when present, else
    parses the first text content block as JSON. Each thread's first related
    message supplies subject/sender/snippet.
    """
    payload: Any = None
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        payload = structured
    else:
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    payload = json.loads(text)
                    break
                except (ValueError, TypeError):
                    continue
    if payload is None:
        return []

    threads = payload.get("threads", payload) if isinstance(payload, dict) else payload
    normalized: list[dict] = []
    for thread in threads or []:
        if not isinstance(thread, dict):
            continue
        messages = thread.get("messages") or thread.get("relatedMessages") or []
        first = messages[0] if messages else {}
        normalized.append(
            {
                "thread_id": str(thread.get("id") or thread.get("threadId") or ""),
                "subject": first.get("subject", "") or thread.get("subject", ""),
                "sender": first.get("sender", "") or first.get("from", ""),
                "snippet": first.get("snippet", "") or thread.get("snippet", ""),
            }
        )
    return [t for t in normalized if t["thread_id"]]


# ---------------------------------------------------------------------------
# REST transport (fallback)
# ---------------------------------------------------------------------------

def _rest_search_threads(query: str) -> list[dict]:  # pragma: no cover - exercised only live
    """Fetch + normalize threads via the Gmail REST API (blocking)."""
    service = _build_gmail_service()
    listing = service.users().threads().list(userId="me", q=query).execute()
    normalized: list[dict] = []
    for thread in listing.get("threads", []):
        thread_id = thread.get("id", "")
        detail = (
            service.users()
            .threads()
            .get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["Subject", "From"],
            )
            .execute()
        )
        messages = detail.get("messages", [])
        first = messages[0] if messages else {}
        headers = {
            h["name"].lower(): h["value"]
            for h in first.get("payload", {}).get("headers", [])
        }
        normalized.append(
            {
                "thread_id": thread_id,
                "subject": headers.get("subject", ""),
                "sender": headers.get("from", ""),
                "snippet": first.get("snippet", thread.get("snippet", "")),
            }
        )
    return normalized


def _build_gmail_service():  # pragma: no cover - exercised only live
    """Build an authorized Gmail API client, running OAuth consent if needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if REST_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(REST_TOKEN_PATH), GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not REST_CLIENT_SECRET_PATH.exists():
                raise RuntimeError(
                    f"Missing OAuth client secret at {REST_CLIENT_SECRET_PATH}. "
                    "Download it from Google Cloud Console (Desktop app client)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(REST_CLIENT_SECRET_PATH), GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        REST_TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)
