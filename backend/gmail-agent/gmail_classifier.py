"""Gemini classification: keep only job-application completion emails.

One structured call over a batch of threads, reusing ``common.gemini.generate_json``
and the shared ``gemini-3.5-flash-lite`` model.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # backend/

from typing import Any

from common.gemini import generate_json
from common.models import DEFAULT_GEMINI_MODEL

from gmail_schemas import (
    CLASSIFY_SCHEMA,
    CLASSIFY_SYSTEM_INSTRUCTION,
    build_classify_prompt,
)


async def classify_threads(
    ai_client: Any,
    threads: list[dict],
    *,
    model: str = DEFAULT_GEMINI_MODEL,
    min_confidence: float = 0.0,
) -> list[dict]:
    """Return the subset of ``threads`` that are completion emails, enriched.

    Each returned dict carries the original thread fields plus the model's
    extracted ``company``/``role``/``status``. Threads the model does not mark
    ``is_completion`` (or below ``min_confidence``) are dropped. An empty input
    short-circuits without an API call.
    """
    if not threads:
        return []

    result = await generate_json(
        ai_client,
        model=model,
        prompt=build_classify_prompt(threads),
        schema=CLASSIFY_SCHEMA,
        system_instruction=CLASSIFY_SYSTEM_INSTRUCTION,
    )

    by_id = {t.get("thread_id", ""): t for t in threads}
    qualifying: list[dict] = []
    for entry in result.get("results", []):
        if not entry.get("is_completion"):
            continue
        if float(entry.get("confidence", 1.0)) < min_confidence:
            continue
        thread_id = entry.get("thread_id", "")
        source = by_id.get(thread_id)
        if source is None:
            continue
        qualifying.append(
            {
                **source,
                "company": (entry.get("company") or "").strip(),
                "role": (entry.get("role") or "").strip(),
                "status": (entry.get("status") or "").strip(),
                "confidence": float(entry.get("confidence", 1.0)),
            }
        )
    return qualifying
