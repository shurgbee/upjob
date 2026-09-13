"""Gemini analysis layer: filtered codebase in, structured specification out.

Two paths, chosen by size rather than configuration:

*single pass*
    The whole filtered codebase fits the character budget, so one long-context
    call produces the final object directly.  This is the common case.

*map-reduce*
    The codebase does not fit.  It is chunked along module boundaries, each
    chunk analysed in parallel into a JSON fragment, the fragments merged by
    deterministic set-union in Python (:func:`schemas.merge_fragments` -- no
    model involved in the merge), and one final reduce call over the compact
    merged object writes the summary and collapses near-duplicate names.

Map-reduce rather than sequential refinement: refinement resends a growing state
on every step (quadratic in chunk count), serialises what could run in parallel,
and lets late chunks overwrite what early ones found.  Set-union weights every
chunk equally and loses nothing.

``google.genai`` is imported lazily so prompt building and budget arithmetic stay
unit testable without the SDK installed.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pathlib

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # backend/

import asyncio
import json
from typing import Any, Sequence

from ingestion import (
    DEFAULT_CHAR_BUDGET,
    PRIORITY_FILENAMES,
    RepoFile,
    build_global_header,
    chunk_files,
    pack_files,
    sort_files,
)
from schemas import (
    ANALYSIS_SCHEMA,
    DEFAULT_GEMINI_MODEL,
    DETAILS_LIST_KEYS,
    FRAGMENT_SCHEMA,
    FRAGMENT_SPEC_KEYS,
    merge_fragments,
)
from common.gemini import generate_json as _generate_json, is_retryable_error  # noqa: F401

#: Upper bound on map-phase calls, so a pathological monorepo cannot fan out
#: into hundreds of requests.  Chunks beyond this are reported as omitted.
MAX_CHUNKS = 24
DEFAULT_MAX_CONCURRENCY = 4
MAX_HEADER_PRIORITY_FILES = 4

_SHARED_RULES = """
Rules:
- Ground every statement in the code you were given. Do not guess at systems,
  tools, or scale that the code does not evidence.
- Technologies must be things actually used, not things merely mentioned in prose
  or listed as alternatives in documentation.
- Architectures are concrete systems the code implements ("ETL Pipeline",
  "Reverse Proxy", "Job Queue Worker"), never vague qualities ("scalable design").
- Metrics must be numbers that appear in the code, its documentation, or the
  supplied user context. Never invent or estimate a number.
- Prefer specific names over generic ones: "PostgreSQL" over "a database".
- Return an empty array rather than padding a section with filler.
"""

SINGLE_PASS_INSTRUCTION = f"""You are an automated repository analysis agent.

You are given the full filtered source of one repository. Read it and produce a
structured engineering profile of the project: what it is, what was built, and
what skills building it demonstrates.

Categorise your findings into:
- Hard skills: concrete tools, languages, libraries, databases, frameworks.
- Architectural components: the distinct functional systems and subsystems built.
{_SHARED_RULES}
Adhere strictly to the required output schema."""

FRAGMENT_INSTRUCTION = f"""You are an automated repository analysis agent
examining ONE SLICE of a larger repository.

The header describes the whole repository; the file blocks after it are only your
slice. Report what YOUR SLICE evidences -- another agent is reading the other
slices, and your findings will be merged with theirs.
{_SHARED_RULES}
Report nothing about parts of the repository you were not shown."""

REDUCE_INSTRUCTION = f"""You are consolidating the analysis of one repository.

Several agents each read one slice of the codebase and reported findings. Those
findings have already been merged by exact set-union, so the lists you are given
are complete but unpolished: they contain near-duplicates, overlapping
granularity, and no overall summary.

Your job:
- Write the project name, description, and Summary from the repository header.
- Collapse near-duplicate entries that mean the same thing ("REST API" and
  "HTTP API layer" -> one entry), keeping the more specific spelling.
- Order each list most significant first.
- Drop entries too trivial to be worth listing.
{_SHARED_RULES}
Do not add findings that are absent from the merged input -- you have not seen
the code yourself."""


def build_user_context_block(user_context: Any) -> str:
    """Render caller-supplied context as a prompt block, or ``""`` if absent.

    This is the one source of truth the model may cite numbers from that are not
    in the code: the user's own impact metrics, goals, and challenges solved.
    """
    if not isinstance(user_context, dict) or not user_context:
        return ""
    lines: list[str] = []
    for key, value in user_context.items():
        label = str(key).replace("_", " ").strip()
        if not label:
            continue
        if isinstance(value, (list, tuple, set)):
            items = [" ".join(str(v).split()) for v in value if str(v).strip()]
            if not items:
                continue
            lines.append(f"{label}:")
            lines.extend(f"  - {item}" for item in items)
        elif isinstance(value, dict):
            if not value:
                continue
            lines.append(f"{label}:")
            lines.extend(
                f"  - {str(k).replace('_', ' ')}: {' '.join(str(v).split())}"
                for k, v in value.items()
            )
        else:
            text = " ".join(str(value).split())
            if text:
                lines.append(f"{label}: {text}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return (
        "<user_supplied_context>\n"
        "Context from the project's author. Treat these claims as true and fold "
        "them into Actions and Metrics. They are the only figures you may report "
        "that do not appear in the code itself.\n"
        f"{body}\n"
        "</user_supplied_context>\n"
    )


def _timeline_block(start_time: str | None, end_time: str | None) -> str:
    """Commit dates as established facts, so the model never computes them."""
    if not (start_time or end_time):
        return ""
    return (
        "<repository_timeline>\n"
        f"earliest commit: {start_time or 'unknown'}\n"
        f"latest commit: {end_time or 'unknown'}\n"
        "</repository_timeline>\n"
    )


def select_header_files(files: Sequence[RepoFile]) -> list[RepoFile]:
    """Pick the few files worth repeating in every chunk's header.

    README and the primary manifest give a chunk the global context it needs to
    interpret its own slice; repeating more than a handful wastes budget on every
    single call.
    """
    chosen: list[RepoFile] = []
    for file in files:
        basename = file.path.rsplit("/", 1)[-1].lower()
        if basename in PRIORITY_FILENAMES:
            chosen.append(file)
            if len(chosen) >= MAX_HEADER_PRIORITY_FILES:
                break
    return chosen


def _fragment_payload(merged: dict[str, Any]) -> str:
    """Compact JSON for the reduce call -- merged findings only, never raw code."""
    payload = {key: merged.get(key, []) for key in FRAGMENT_SPEC_KEYS}
    payload.update({key: merged.get(key, []) for key in DETAILS_LIST_KEYS})
    return json.dumps(payload, indent=1, ensure_ascii=False)


async def analyze_files(
    files: Sequence[RepoFile],
    *,
    owner: str,
    repo: str,
    api_key: str,
    ref: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    user_context: Any = None,
    model: str = DEFAULT_GEMINI_MODEL,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> dict[str, Any]:
    """Analyse a filtered codebase and return the raw model output plus metadata.

    Returns ``{"project_specification": ..., "details": ..., "ingestion": ...}``
    where the first two are unvalidated model output (the caller shapes them via
    :mod:`schemas`) and ``ingestion`` records how the analysis was performed --
    mode, call count, and any omitted paths, so a truncated result is always
    visible rather than silently partial.
    """
    if not files:
        raise ValueError("no files to analyze: the repository filtered down to nothing")

    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover - exercised only without the SDK
        raise RuntimeError(
            "google-genai is required to analyze a repository: "
            "pip install -r requirements.txt"
        ) from exc

    ordered = sort_files(files)
    header = build_global_header(
        owner=owner,
        repo=repo,
        ref=ref,
        paths=[f.path for f in ordered],
        priority_files=select_header_files(ordered),
    )
    context_block = build_user_context_block(user_context)
    timeline = _timeline_block(start_time, end_time)
    preamble = timeline + context_block

    bundle, included, omitted = pack_files(ordered, budget=char_budget, header=header)

    client = genai.Client(api_key=api_key)
    ai_client = client.aio
    ingestion: dict[str, Any] = {
        "file_count": len(ordered),
        "analyzed_file_count": len(included),
        "omitted_paths": omitted,
        "truncated": False,
    }

    try:
        if not omitted:
            # Fast path: the entire filtered codebase fits one call.
            result = await _generate_json(
                ai_client,
                model=model,
                prompt=f"{preamble}<repository>\n{bundle}\n</repository>",
                schema=ANALYSIS_SCHEMA,
                system_instruction=SINGLE_PASS_INSTRUCTION,
            )
            ingestion.update({"mode": "single_pass", "model_calls": 1, "chunks": 1})
            return {
                "project_specification": result.get("project_specification"),
                "details": result.get("details"),
                "ingestion": ingestion,
            }

        chunks = chunk_files(ordered, budget=char_budget)
        if len(chunks) > MAX_CHUNKS:
            dropped = [f.path for chunk in chunks[MAX_CHUNKS:] for f in chunk]
            chunks = chunks[:MAX_CHUNKS]
            ingestion["truncated"] = True
            ingestion["omitted_paths"] = dropped
        else:
            # Chunking covers every file, so nothing is actually omitted.
            ingestion["omitted_paths"] = []

        analyzed = [f.path for chunk in chunks for f in chunk]
        ingestion["analyzed_file_count"] = len(analyzed)

        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def run_chunk(index: int, chunk: Sequence[RepoFile]) -> dict[str, Any]:
            chunk_bundle, _, _ = pack_files(
                chunk, budget=char_budget, header=f"{header}\n"
            )
            prompt = (
                f"{preamble}"
                f'<slice index="{index + 1}" of="{len(chunks)}">\n'
                f"{chunk_bundle}\n</slice>"
            )
            async with semaphore:
                return await _generate_json(
                    ai_client,
                    model=model,
                    prompt=prompt,
                    schema=FRAGMENT_SCHEMA,
                    system_instruction=FRAGMENT_INSTRUCTION,
                )

        fragments = await asyncio.gather(
            *(run_chunk(i, chunk) for i, chunk in enumerate(chunks))
        )
        merged = merge_fragments([f for f in fragments if isinstance(f, dict)])

        reduced = await _generate_json(
            ai_client,
            model=model,
            prompt=(
                f"{preamble}<repository_header>\n{header}\n</repository_header>\n"
                f"<merged_findings>\n{_fragment_payload(merged)}\n</merged_findings>"
            ),
            schema=ANALYSIS_SCHEMA,
            system_instruction=REDUCE_INSTRUCTION,
        )
        ingestion.update(
            {
                "mode": "map_reduce",
                "chunks": len(chunks),
                "model_calls": len(chunks) + 1,
            }
        )
        return {
            "project_specification": reduced.get("project_specification"),
            "details": reduced.get("details"),
            "ingestion": ingestion,
        }
    finally:
        aclose = getattr(ai_client, "aclose", None)
        if aclose is not None:
            await aclose()
