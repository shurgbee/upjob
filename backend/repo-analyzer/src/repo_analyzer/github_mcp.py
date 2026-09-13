"""Connection lifecycle for the official remote GitHub MCP server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from .config import Settings

READ_ONLY_GITHUB_TOOLS = [
    "search_repositories",
    "get_file_contents",
    "search_code",
    "list_commits",
]


@asynccontextmanager
async def github_mcp_session(settings: Settings) -> AsyncIterator[ClientSession]:
    """Open an authenticated, server-enforced read-only GitHub MCP session."""

    headers = {
        "Authorization": f"Bearer {settings.github_pat}",
        "X-MCP-Readonly": "true",
        "X-MCP-Tools": ",".join(READ_ONLY_GITHUB_TOOLS),
    }
    http_client = create_mcp_http_client(headers=headers)
    async with http_client, streamable_http_client(
        settings.github_mcp_url, http_client=http_client
    ) as (read_stream, write_stream), ClientSession(
        read_stream, write_stream, read_timeout_seconds=60
    ) as session:
        await session.initialize()
        yield session
