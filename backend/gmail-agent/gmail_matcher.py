"""Gemini matching: link each completion email to an open application row.

Given the SQL-prefiltered candidate rows (``persistence.fetch_open_candidates``)
and the qualifying emails, one structured Gemini call picks the best candidate
index per email, or null. This module only decides; the caller performs the
DB writes.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # backend/

from typing import Any

from common.gemini import generate_json
from common.models import DEFAULT_GEMINI_MODEL

from gmail_schemas import MATCH_SCHEMA, MATCH_SYSTEM_INSTRUCTION, build_match_prompt


async def match_emails(
    ai_client: Any,
    emails: list[dict],
    candidates: list[dict],
    *,
    model: str = DEFAULT_GEMINI_MODEL,
    min_confidence: float = 0.5,
) -> dict[str, str | None]:
    """Map each email's ``thread_id`` to a candidate ``ctid`` or None.

    Returns ``{thread_id: ctid | None}``. With no candidates every email maps to
    None (all will become new rows) and no API call is made. A match below
    ``min_confidence``, or an out-of-range index, is treated as no match.
    """
    if not emails:
        return {}
    if not candidates:
        return {e["thread_id"]: None for e in emails}

    result = await generate_json(
        ai_client,
        model=model,
        prompt=build_match_prompt(emails, candidates),
        schema=MATCH_SCHEMA,
        system_instruction=MATCH_SYSTEM_INSTRUCTION,
    )

    ctid_by_index = {c["index"]: c["ctid"] for c in candidates}
    decisions: dict[str, str | None] = {e["thread_id"]: None for e in emails}
    for match in result.get("matches", []):
        thread_id = match.get("thread_id")
        if thread_id not in decisions:
            continue
        index = match.get("candidate_index")
        if index is None or index not in ctid_by_index:
            continue
        if float(match.get("confidence", 1.0)) < min_confidence:
            continue
        decisions[thread_id] = ctid_by_index[index]
    return decisions
