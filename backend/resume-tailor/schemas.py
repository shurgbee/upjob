"""Schemas and shaping for the resume tailor.

Dependency-free (stdlib only) so shaping, retrieval SQL building, bullet
handling and LaTeX injection are all unit testable without asyncpg or
google-genai installed.  The Gemini model identifiers live in ``common.models``;
re-exported here for convenience.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from common.models import DEFAULT_GEMINI_MODEL, EMBEDDING_MODEL  # noqa: E402

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "EMBEDDING_MODEL",
    "BULLETS_PER_PROJECT",
    "JobSpecification",
    "SelectedProject",
    "BULLETS_SCHEMA",
    "build_job_specification",
    "clean_bullets",
]

#: Number of bullet points generated per selected project (spec section 3).
BULLETS_PER_PROJECT = 3


# ---------------------------------------------------------------------------
# Gemini response schema
# ---------------------------------------------------------------------------

#: Both LLM passes (generation and review) return a flat JSON array of strings.
#: The object wrapper (`{"bullets": [...]}`) is used because the Gemini
#: structured-output API requires an object at the top level; callers unwrap it.
BULLETS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Resume bullet points, one string each.",
        }
    },
    "required": ["bullets"],
}


# ---------------------------------------------------------------------------
# Job specification (pipeline input)
# ---------------------------------------------------------------------------


@dataclass
class JobSpecification:
    """The target job, per ResumeTailor.md section 1.

    ``technologies`` (hard skills) drives the PostgreSQL overlap filter;
    ``architecture`` (tangible components) is embedded for the vector search.
    ``yoe`` and the dates are accepted but currently unused by retrieval.
    """

    title: str
    url: str = ""
    technologies: list[str] = field(default_factory=list)
    architecture: list[str] = field(default_factory=list)
    yoe: int | None = None
    publish_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "technologies": list(self.technologies),
            "architecture": list(self.architecture),
            "yoe": self.yoe,
            "publish_date": self.publish_date,
        }


# ---------------------------------------------------------------------------
# Selected project (retrieval output -> generation input)
# ---------------------------------------------------------------------------


@dataclass
class SelectedProject:
    """A project chosen by retrieval, carried through the generation chain."""

    project_id: int
    name: str
    technologies: list[str] = field(default_factory=list)
    architectures: list[str] = field(default_factory=list)
    details_markdown: str = ""
    overlap_ratio: float = 0.0
    similarity: float = 0.0
    bullets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "technologies": list(self.technologies),
            "architectures": list(self.architectures),
            "overlap_ratio": self.overlap_ratio,
            "similarity": self.similarity,
            "bullets": list(self.bullets),
        }


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _clean_str(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    return ""


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        cleaned = _clean_str(item)
        if cleaned:
            out.append(cleaned)
    return out


def build_job_specification(payload: Any) -> JobSpecification:
    """Coerce the spec's job JSON (any casing of keys) into a JobSpecification.

    Accepts the capitalised keys from ResumeTailor.md section 1 (``Title``,
    ``Technologies``, ``Architecture``, ``YOE``, ``Publish_Date``, ``Url``) and
    their lowercase equivalents.  Missing fields become empty; ``YOE`` that is
    not an int becomes ``None``.  Raises ``ValueError`` if there is no title.
    """
    if not isinstance(payload, dict):
        raise ValueError("job specification must be a JSON object")

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    title = _clean_str(pick("Title", "title"))
    if not title:
        raise ValueError("job specification is missing a title")

    yoe_raw = pick("YOE", "yoe")
    yoe = yoe_raw if isinstance(yoe_raw, int) and not isinstance(yoe_raw, bool) else None

    return JobSpecification(
        title=title,
        url=_clean_str(pick("Url", "url")),
        technologies=_clean_str_list(pick("Technologies", "technologies")),
        architecture=_clean_str_list(pick("Architecture", "architecture")),
        yoe=yoe,
        publish_date=_clean_str(pick("Publish_Date", "publish_date")) or None,
    )


def clean_bullets(value: Any) -> list[str]:
    """Extract a clean list of bullet strings from a model response.

    Accepts either the ``{"bullets": [...]}`` object shape or a bare list.
    Drops non-strings and blanks and collapses internal whitespace so a bullet
    can never break LaTeX line structure downstream.
    """
    if isinstance(value, dict):
        value = value.get("bullets")
    return _clean_str_list(value)
