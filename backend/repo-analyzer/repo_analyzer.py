#!/usr/bin/env python3
"""Ingest a GitHub repository and profile the skills it demonstrates.

The public entry point is ``analyze_repository``.  It is asynchronous and returns
only JSON-serializable data, so it can be called directly from a FastAPI route
without blocking the event loop.

Pipeline:

1. Parse the repository URL into ``owner``/``repo``/``ref``.
2. Fetch the tarball snapshot and the commit bounds concurrently (:mod:`ingestion`,
   :mod:`commits`) -- one request for the whole tree, two for the timeline.
3. Analyse the filtered codebase with Gemini (:mod:`analyzer`), single-pass or
   map-reduce depending on size.
4. Shape the result (:mod:`schemas`) and render ``DETAILS.md`` (:mod:`details_md`).
5. Persist the specification, the document, and an embedding of the architectures
   (:mod:`persistence`, :mod:`embeddings`).

A failure in analysis or persistence degrades rather than aborts: the envelope
keeps its shape, ``analysis_status`` becomes ``"error"``, and the fields that were
resolved deterministically (repository identity and commit dates) are still
present.  The CLI and the FastAPI-callable function are thin wrappers over the
same ``analyze_repository`` call -- keep new options plumbed through both.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from analyzer import DEFAULT_MAX_CONCURRENCY, analyze_files
from commits import fetch_commit_bounds
from details_md import render_details_md
from ingestion import DEFAULT_CHAR_BUDGET, canonical_repo_url, parse_repo_url
from schemas import (
    DEFAULT_GEMINI_MODEL,
    ProjectSpecification,
    analysis_result,
    build_details,
    build_specification,
    empty_details,
)


async def analyze_repository(
    github_repo_url: str,
    *,
    user_context: dict[str, Any] | None = None,
    github_token: str | None = None,
    gemini_api_key: str | None = None,
    database_url: str | None = None,
    model: str = DEFAULT_GEMINI_MODEL,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    persist: bool = True,
    embed: bool = True,
    details_output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Analyse one GitHub repository and optionally persist the result.

    ``github_repo_url`` may be any form :func:`ingestion.parse_repo_url` accepts,
    including a ``/tree/<branch>`` URL.  ``user_context`` is the author's own
    account of metrics, impact, and challenges solved; it is the only source of
    figures the model may report that are not evidenced by the code.

    Returns the envelope from :func:`schemas.analysis_result`, plus ``project_id``
    when the record was persisted.
    """
    owner, repo, ref = parse_repo_url(github_repo_url)
    # Identity is the canonical URL, never the spelling the caller passed in,
    # so "owner/repo" and ".../tree/main" resolve to the same project row.
    canonical_url = canonical_repo_url(owner, repo)
    token = github_token or os.getenv("GITHUB_PAT")
    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
    dsn = database_url or os.getenv("DATABASE_URL")

    start_time: str | None = None
    end_time: str | None = None
    specification = ProjectSpecification(
        name=repo, description="", github_repo_url=canonical_url
    )

    def failure(message: str) -> dict[str, Any]:
        """Error envelope that keeps every deterministically-resolved field."""
        specification.start_time = start_time
        specification.end_time = end_time
        return analysis_result(
            specification=specification,
            details=empty_details(),
            details_markdown="",
            status="error",
            error=message,
        )

    if not api_key:
        return failure("GEMINI_API_KEY is not set")

    # One tarball request and two commit requests, overlapped.
    from ingestion import fetch_repository_files

    try:
        files, bounds = await asyncio.gather(
            fetch_repository_files(owner, repo, ref=ref, token=token),
            fetch_commit_bounds(owner, repo, ref=ref, token=token),
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as status
        return failure(f"repository fetch failed: {exc}")
    start_time, end_time = bounds

    try:
        analysis = await analyze_files(
            files,
            owner=owner,
            repo=repo,
            ref=ref,
            api_key=api_key,
            start_time=start_time,
            end_time=end_time,
            user_context=user_context,
            model=model,
            char_budget=char_budget,
            max_concurrency=max_concurrency,
        )
    except Exception as exc:  # noqa: BLE001 - one repo's failure is a status, not a crash
        return failure(f"analysis failed: {exc}")

    specification = build_specification(
        analysis.get("project_specification"),
        github_repo_url=canonical_url,
        fallback_name=repo,
        start_time=start_time,
        end_time=end_time,
    )
    details = build_details(
        analysis.get("details"), fallback_summary=specification.description
    )
    details_markdown = render_details_md(details, title=specification.name)

    if details_output_path:
        path = Path(details_output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(details_markdown, encoding="utf-8")

    result = analysis_result(
        specification=specification,
        details=details,
        details_markdown=details_markdown,
        ingestion=analysis.get("ingestion"),
    )

    if persist and dsn:
        try:
            result["project_id"] = await _persist(
                dsn=dsn,
                specification=specification,
                details=details,
                details_markdown=details_markdown,
                api_key=api_key,
                embed=embed,
            )
        except Exception as exc:  # noqa: BLE001 - analysis is still worth returning
            result["analysis_status"] = "partial"
            result["error"] = f"persistence failed: {exc}"
    elif persist and not dsn:
        result["analysis_status"] = "partial"
        result["error"] = "DATABASE_URL is not set; result was not persisted"

    return result


async def _persist(
    *,
    dsn: str,
    specification: ProjectSpecification,
    details: dict[str, Any],
    details_markdown: str,
    api_key: str,
    embed: bool,
) -> int:
    """Write the project record and its architecture embedding; return the id."""
    import persistence

    conn = await persistence.connect(dsn)
    try:
        await persistence.ensure_schema(conn)
        project_id = await persistence.upsert_project(
            conn, specification.to_dict(), details, details_markdown
        )
        if embed and specification.architectures:
            from embeddings import architecture_document, embed_architectures, is_zero_vector

            document = architecture_document(specification.architectures)
            vector = await embed_architectures(
                specification.architectures, api_key=api_key
            )
            if not is_zero_vector(vector):
                await persistence.replace_architecture_embedding(
                    conn, project_id, document, vector
                )
        return project_id
    finally:
        await conn.close()


def _load_user_context(raw: str | None) -> dict[str, Any] | None:
    """Parse ``--user-context`` as either inline JSON or a path to a JSON file."""
    if not raw:
        return None
    candidate = Path(raw)
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else raw
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--user-context is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("--user-context must be a JSON object")
    return loaded


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse a GitHub repository into a project specification, "
        "a DETAILS.md skill document, and an architecture embedding."
    )
    parser.add_argument("repo_url", help="GitHub repository URL or owner/repo")
    parser.add_argument(
        "--user-context",
        help="Inline JSON object, or a path to a JSON file, of author-supplied "
        "metrics, impact, and challenges solved",
    )
    parser.add_argument(
        "--details-out",
        help="Write the rendered DETAILS.md to this path",
    )
    parser.add_argument(
        "--output",
        help="Write the full JSON result to this path instead of stdout",
    )
    parser.add_argument("--model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument(
        "--char-budget",
        type=int,
        default=DEFAULT_CHAR_BUDGET,
        help="Characters per model call before the pipeline switches to "
        f"map-reduce chunking (default {DEFAULT_CHAR_BUDGET})",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=DEFAULT_MAX_CONCURRENCY,
        help="Parallel model calls during the map phase",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip PostgreSQL entirely and only print the result",
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Persist the project record but skip the architecture embedding",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv()

    try:
        user_context = _load_user_context(args.user_context)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = asyncio.run(
        analyze_repository(
            args.repo_url,
            user_context=user_context,
            model=args.model,
            char_budget=args.char_budget,
            max_concurrency=args.max_concurrency,
            persist=not args.no_persist,
            embed=not args.no_embed,
            details_output_path=args.details_out,
        )
    )

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)

    if result.get("analysis_status") == "error":
        print(f"error: {result.get('error')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
