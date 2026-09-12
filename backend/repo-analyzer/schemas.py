"""Schemas and shaping helpers for GitHub repository ingestion.

This module is deliberately dependency-free (stdlib only) so every pure piece of
the pipeline -- filtering, packing, merging, Markdown rendering -- can be unit
tested without ``google-genai``, ``httpx`` or ``asyncpg`` installed.

Two output shapes travel through the pipeline:

``project_specification``
    Relational data, persisted to the PostgreSQL ``projects`` table.  Modelled
    as a dataclass because its fields are fixed and typed.

``details``
    Document data, rendered to ``DETAILS.md``.  Kept as a plain ``dict`` whose
    keys are exactly the Markdown section names (``Summary``,
    ``Architectural_Components``, ...) so the renderer is a direct key mapping
    with no translation layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
EMBEDDING_MODEL = "gemini-embedding-001"

#: ``gemini-embedding-001`` defaults to 3072 dimensions and supports truncation
#: to 1536 or 768.  768 is ample for the short architecture strings we embed and
#: keeps the pgvector index small -- but note that only the full 3072-dim output
#: arrives pre-normalized, so reduced vectors must be L2-normalized by hand (see
#: ``embeddings.normalize_vector``).
EMBEDDING_DIMENSIONS = 768

# ---------------------------------------------------------------------------
# DETAILS.md structure
# ---------------------------------------------------------------------------

#: The ``Summary`` section renders as a paragraph; every other section renders
#: as an unordered list.  Order here is the order of sections in DETAILS.md.
DETAILS_SUMMARY_KEY = "Summary"
DETAILS_LIST_KEYS: tuple[str, ...] = (
    "Architectural_Components",
    "Core_Competencies",
    "Technologies",
    "Actions",
    "Metrics",
)
DETAILS_SECTION_ORDER: tuple[str, ...] = (DETAILS_SUMMARY_KEY,) + DETAILS_LIST_KEYS


def empty_details() -> dict[str, Any]:
    """A ``details`` object with every section present but empty."""
    details: dict[str, Any] = {DETAILS_SUMMARY_KEY: ""}
    for key in DETAILS_LIST_KEYS:
        details[key] = []
    return details


# ---------------------------------------------------------------------------
# Gemini response schemas
# ---------------------------------------------------------------------------


def _string_array(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": description,
    }


PROJECT_SPECIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Human-readable project name.",
        },
        "description": {
            "type": "string",
            "description": "Concise summary of what the project is and does.",
        },
        "technologies": _string_array(
            "Raw tools, languages, libraries, databases and frameworks used."
        ),
        "architectures": _string_array(
            "Tangible systems built, e.g. 'ETL Pipeline', 'Reverse Proxy'."
        ),
    },
    "required": ["name", "description", "technologies", "architectures"],
}

DETAILS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        DETAILS_SUMMARY_KEY: {
            "type": "string",
            "description": "One to two sentences on the project's engineering purpose.",
        },
        "Architectural_Components": _string_array(
            "Distinct functional systems and subsystems built."
        ),
        "Core_Competencies": _string_array(
            "Applied methodologies, e.g. 'Process Isolation', 'Stream Processing'."
        ),
        "Technologies": _string_array(
            "Specific tools, cloud resources and environments used."
        ),
        "Actions": _string_array(
            "Action-driven statements describing technical implementation steps."
        ),
        "Metrics": _string_array(
            "Quantifiable figures: throughput, latency, coverage, scale. "
            "Only numbers present in the code, docs or supplied user context."
        ),
    },
    "required": list(DETAILS_SECTION_ORDER),
}

#: Single-pass schema used on the fast path and on the final reduce call.
ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_specification": PROJECT_SPECIFICATION_SCHEMA,
        "details": DETAILS_SCHEMA,
    },
    "required": ["project_specification", "details"],
}

#: Schema for one chunk of a large repository during the map phase.  Dates and
#: the final name/description are resolved outside the map phase, so a fragment
#: only reports what its own slice of the codebase evidences.
FRAGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "technologies": _string_array("Technologies evidenced by this slice."),
        "architectures": _string_array("Systems evidenced by this slice."),
        "Architectural_Components": _string_array(
            "Functional components implemented in this slice."
        ),
        "Core_Competencies": _string_array("Methodologies applied in this slice."),
        "Technologies": _string_array("Specific tools and environments in this slice."),
        "Actions": _string_array("Implementation steps evidenced by this slice."),
        "Metrics": _string_array("Quantifiable figures found in this slice."),
    },
    "required": ["technologies", "architectures"],
}

#: Keys of a fragment that feed ``project_specification``.
FRAGMENT_SPEC_KEYS: tuple[str, ...] = ("technologies", "architectures")


# ---------------------------------------------------------------------------
# Project specification
# ---------------------------------------------------------------------------


@dataclass
class ProjectSpecification:
    """Relational record persisted to the ``projects`` table."""

    name: str
    description: str
    github_repo_url: str
    start_time: str | None = None
    end_time: str | None = None
    technologies: list[str] = field(default_factory=list)
    architectures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "github_repo_url": self.github_repo_url,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "technologies": list(self.technologies),
            "architectures": list(self.architectures),
        }


# ---------------------------------------------------------------------------
# Normalisation and merging
# ---------------------------------------------------------------------------

_LABEL_STRIP = " \t\r\n.,;:-_*/\\'\"()[]"


def normalize_label(value: str) -> str:
    """Fold a label to a comparison key for dedupe.

    Case, surrounding punctuation and internal whitespace runs are ignored, so
    ``"REST API "`` and ``"rest  api"`` collapse to the same key.  The returned
    value is for comparison only -- never for display.
    """
    collapsed = " ".join(str(value).split())
    return collapsed.strip(_LABEL_STRIP).casefold()


def merge_string_lists(*lists: Iterable[Any]) -> list[str]:
    """Union of string lists, preserving first-seen order and casing.

    Non-strings and blanks are dropped.  Entries differing only in case or
    surrounding punctuation collapse together, and the longer spelling wins
    because it usually carries the better casing (``"PostgreSQL"`` over
    ``"postgresql."``).  Entries with different words -- ``"PostgreSQL"`` and
    ``"PostgreSQL 16"`` -- are distinct keys and both survive; collapsing those
    is the reduce call's job, not this function's.
    """
    chosen: dict[str, str] = {}
    order: list[str] = []
    for items in lists:
        if not items:
            continue
        for item in items:
            if not isinstance(item, str):
                continue
            display = " ".join(item.split())
            if not display:
                continue
            key = normalize_label(display)
            if not key:
                continue
            if key not in chosen:
                chosen[key] = display
                order.append(key)
            elif len(display) > len(chosen[key]):
                chosen[key] = display
    return [chosen[key] for key in order]


def merge_fragments(fragments: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Reduce map-phase fragments into one merged object, in Python.

    Every list-valued key is a set-union via :func:`merge_string_lists`; nothing
    here is left to a model, so the merge is deterministic and lossless.  The
    result carries ``details`` sections plus the two specification lists.
    """
    merged: dict[str, Any] = {
        key: merge_string_lists(*(f.get(key) or [] for f in fragments))
        for key in FRAGMENT_SPEC_KEYS
    }
    for key in DETAILS_LIST_KEYS:
        merged[key] = merge_string_lists(*(f.get(key) or [] for f in fragments))
    return merged


# ---------------------------------------------------------------------------
# Result shaping
# ---------------------------------------------------------------------------


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return merge_string_lists(value)


def _clean_str(value: Any, fallback: str = "") -> str:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return fallback


def build_details(extracted: Any, fallback_summary: str = "") -> dict[str, Any]:
    """Coerce a model-supplied ``details`` object into the full section shape.

    Missing or malformed sections become empty rather than absent, so the
    Markdown renderer and every consumer can rely on all keys being present.
    """
    details = empty_details()
    if not isinstance(extracted, dict):
        details[DETAILS_SUMMARY_KEY] = fallback_summary
        return details
    details[DETAILS_SUMMARY_KEY] = _clean_str(
        extracted.get(DETAILS_SUMMARY_KEY), fallback_summary
    )
    for key in DETAILS_LIST_KEYS:
        details[key] = _clean_str_list(extracted.get(key))
    return details


def build_specification(
    extracted: Any,
    *,
    github_repo_url: str,
    fallback_name: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> ProjectSpecification:
    """Coerce a model-supplied ``project_specification`` into the dataclass.

    Commit timestamps are supplied by the caller rather than taken from the
    model: they are resolved deterministically from the GitHub API, so the model
    is never trusted with them even if it volunteers a value.
    """
    source = extracted if isinstance(extracted, dict) else {}
    return ProjectSpecification(
        name=_clean_str(source.get("name"), fallback_name),
        description=_clean_str(source.get("description")),
        github_repo_url=github_repo_url,
        start_time=start_time,
        end_time=end_time,
        technologies=_clean_str_list(source.get("technologies")),
        architectures=_clean_str_list(source.get("architectures")),
    )


def analysis_result(
    *,
    specification: ProjectSpecification,
    details: dict[str, Any],
    details_markdown: str,
    status: str = "ok",
    error: str | None = None,
    ingestion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The JSON-serializable envelope returned by the public entry point.

    Shape is identical for success and failure so a FastAPI route never has to
    branch on status to read the feed-derived fields.
    """
    return {
        "analysis_status": status,
        "error": error,
        "project_specification": specification.to_dict(),
        "details": details,
        "details_markdown": details_markdown,
        "ingestion": ingestion or {},
    }
