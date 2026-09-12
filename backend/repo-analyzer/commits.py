"""Deterministic commit-date resolution via the GitHub commits API.

The project timeline is never inferred by a model: ``start_time`` and
``end_time`` come from two API requests here.  GitHub returns commits newest
first, so the most recent commit is on page 1 and the earliest is the last item
of the last page -- which the ``Link: ... rel="last"`` header hands us directly,
with no pagination crawl.

``httpx`` is imported lazily inside the one async function that needs it so the
pure header/payload parsers stay unit testable without it installed.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

GITHUB_API_ROOT = "https://api.github.com"
_LINK_RE = re.compile(r'<(?P<url>[^>]+)>\s*;\s*rel="(?P<rel>[^"]+)"')
_PAGE_RE = re.compile(r"[?&]page=(\d+)")


def github_headers(token: str | None = None) -> dict[str, str]:
    """Standard GitHub REST headers, authenticated when a token is supplied.

    A token raises the rate limit from 60 to 5,000 requests/hour, which matters
    because ingestion shares the budget with the tarball download.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_last_page(link_header: str | None) -> int | None:
    """Extract the ``page`` number of the ``rel="last"`` link, if present.

    Returns ``None`` when the header is absent or has no ``last`` relation --
    which is itself meaningful: with ``per_page=1`` it means the repository has
    exactly one commit, so earliest and latest are the same commit.
    """
    if not link_header:
        return None
    for match in _LINK_RE.finditer(link_header):
        if match.group("rel").strip().lower() != "last":
            continue
        page_match = _PAGE_RE.search(match.group("url"))
        if page_match:
            try:
                return int(page_match.group(1))
            except ValueError:
                return None
    return None


def extract_commit_date(payload: Any) -> str | None:
    """Pull an ISO-8601 timestamp out of a commits-API payload.

    Accepts either a single commit object or a list of them (taking the first).
    Prefers the committer date over the author date: rebases and cherry-picks
    rewrite the former, which better reflects when the work landed.
    """
    commit = payload
    if isinstance(commit, list):
        if not commit:
            return None
        commit = commit[0]
    if not isinstance(commit, dict):
        return None
    inner = commit.get("commit")
    if not isinstance(inner, dict):
        return None
    for role in ("committer", "author"):
        actor = inner.get(role)
        if isinstance(actor, dict):
            date = actor.get("date")
            if isinstance(date, str) and date.strip():
                return date.strip()
    return None


def _last_commit_date(payload: Any) -> str | None:
    """Date of the *oldest* commit in a page (the last entry GitHub returned)."""
    if isinstance(payload, list) and payload:
        return extract_commit_date(payload[-1])
    return extract_commit_date(payload)


async def fetch_commit_bounds(
    owner: str,
    repo: str,
    *,
    ref: str | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> tuple[str | None, str | None]:
    """Return ``(start_time, end_time)`` as ISO-8601 strings, or ``None`` each.

    Two requests at most.  Failure is non-fatal by design: the caller can still
    produce a useful specification without a timeline, so a network or
    permission problem yields ``(None, None)`` rather than aborting the run.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without httpx
        raise RuntimeError(
            "httpx is required to fetch commit dates: pip install -r requirements.txt"
        ) from exc

    url = f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/commits"
    params: dict[str, str] = {"per_page": "1"}
    if ref:
        params["sha"] = ref

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=github_headers(token)
    ) as client:
        first = await client.get(url, params=params)
        if first.status_code != 200:
            return (None, None)
        end_time = extract_commit_date(first.json())

        last_page = parse_last_page(first.headers.get("Link"))
        if last_page is None or last_page <= 1:
            # A single page at per_page=1 means a single commit.
            return (end_time, end_time)

        oldest = await client.get(url, params={**params, "page": str(last_page)})
        if oldest.status_code != 200:
            return (None, end_time)
        return (_last_commit_date(oldest.json()), end_time)
