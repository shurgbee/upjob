"""Two-stage retrieval of projects from repo-analyzer via overlap filter + cosine similarity.

Stage 2B: PostgreSQL hard-skill overlap filter.
Stage 2C: In-memory cosine similarity ranking (embeddings are L2-normalized, so
cosine similarity equals the dot product).
"""

from __future__ import annotations

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # resume-tailor (for schemas)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend (for common)

from typing import Sequence

from schemas import JobSpecification, SelectedProject

__all__ = [
    "DEFAULT_OVERLAP_THRESHOLD",
    "DEFAULT_FILTER_LIMIT",
    "DEFAULT_MIN_CANDIDATES",
    "DEFAULT_TOP_K",
    "OVERLAP_SQL",
    "parse_vector",
    "cosine_similarity",
    "select_candidates",
    "rank_by_similarity",
    "embed_job_architecture",
    "fetch_overlap_candidates",
    "select_projects",
]


# Module constants
DEFAULT_OVERLAP_THRESHOLD = 0.8
DEFAULT_FILTER_LIMIT = 10
DEFAULT_MIN_CANDIDATES = 3
DEFAULT_TOP_K = 4

# PostgreSQL hard-skill overlap filter (stage 2B)
OVERLAP_SQL = """WITH job AS (
  SELECT ARRAY(
    SELECT DISTINCT lower(btrim(t)) FROM unnest($1::text[]) AS t WHERE btrim(t) <> ''
  ) AS tech
)
SELECT
  p.project_id AS id,
  p.name,
  p.technologies,
  p.architecture AS architectures,
  p.description AS details_markdown,
  p.arch_embeddings::text AS embedding_text,
  (
    SELECT count(DISTINCT lower(btrim(pt)))
    FROM unnest(p.technologies) AS pt
    WHERE lower(btrim(pt)) = ANY (job.tech)
  )::float / NULLIF(cardinality(job.tech), 0) AS overlap_ratio
FROM projects p
CROSS JOIN job
WHERE ($2::text IS NULL OR p.user_id::text = $2)
ORDER BY overlap_ratio DESC NULLS LAST, p.project_id
LIMIT $3"""


# Pure helpers
def parse_vector(text: str) -> list[float]:
    """Parse a pgvector text literal like "[0.1,0.2,-0.3]" into list[float].

    Strips surrounding brackets and whitespace; empty input returns [].
    Robust to interior spaces.
    """
    text = text.strip()
    if not text or text == "[]":
        return []

    # Strip brackets
    if text.startswith("["):
        text = text[1:]
    if text.endswith("]"):
        text = text[:-1]

    text = text.strip()
    if not text:
        return []

    # Split by comma and convert each element
    try:
        return [float(x.strip()) for x in text.split(",")]
    except ValueError:
        return []


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine similarity as dot product of equal-length vectors.

    Since embeddings are pre-normalized (L2-normalized), cosine similarity
    equals the dot product. Returns 0.0 if lengths differ or either is empty.
    """
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def select_candidates(
    rows: Sequence[dict],
    *,
    threshold: float,
    min_candidates: int,
) -> list[dict]:
    """Stage-2B: threshold filter with fallback (pure).

    Keeps rows whose overlap_ratio is not None and >= threshold. If fewer than
    min_candidates qualify, falls back to returning all input rows unchanged
    (best-by-overlap). This ensures the pipeline always has candidates even when
    real projects rarely cover 80% of a job's tech list.
    """
    qualified = [
        row for row in rows
        if row.get("overlap_ratio") is not None and row.get("overlap_ratio") >= threshold
    ]

    if len(qualified) >= min_candidates:
        return qualified

    # Fallback: return all rows (already ordered by overlap_ratio DESC)
    return list(rows)


def rank_by_similarity(
    rows: Sequence[dict],
    job_vector: Sequence[float],
    *,
    top_k: int,
) -> list[SelectedProject]:
    """Rank candidates by cosine similarity and return top-k SelectedProject (pure).

    For each row: parses embedding_text to a vector, computes cosine_similarity
    vs job_vector, builds a SelectedProject with overlap_ratio and computed
    similarity. Sorts by similarity descending and returns up to top_k.
    """
    ranked = []

    for row in rows:
        embedding_text = row.get("embedding_text", "")
        vector = parse_vector(embedding_text)

        similarity = cosine_similarity(vector, job_vector)

        project = SelectedProject(
            project_id=row.get("id") if row.get("id") is not None else row.get("project_id"),
            name=row.get("name", ""),
            technologies=row.get("technologies", []),
            architectures=row.get("architectures") if row.get("architectures") is not None else row.get("architecture", []),
            details_markdown=row.get("details_markdown") if row.get("details_markdown") is not None else row.get("description", ""),
            overlap_ratio=row.get("overlap_ratio") or 0.0,
            similarity=similarity,
        )
        ranked.append(project)

    # Sort by similarity descending
    ranked.sort(key=lambda p: p.similarity, reverse=True)

    # Return top-k
    return ranked[:top_k]


# Async functions
async def embed_job_architecture(
    job: JobSpecification,
    *,
    api_key: str,
    model: str | None = None,
) -> list[float]:
    """Embed the job's architecture for cosine similarity ranking (stage 2C).

    Lazily imports from common.embeddings. Raises ValueError if job.architecture
    is empty. The returned vector is held in memory, never stored.

    Args:
        job: Target job specification
        api_key: Google GenAI API key
        model: Optional Gemini embedding model (passed only if not None)

    Returns:
        L2-normalized embedding vector (768 dimensions)

    Raises:
        ValueError: If job has no architecture to embed
    """
    from common.embeddings import embed_text, architecture_document

    if not job.architecture:
        raise ValueError("job has no architecture to embed")

    document = architecture_document(job.architecture)
    if not document:
        raise ValueError("job has no architecture to embed")

    if model is None:
        return await embed_text(document, api_key=api_key)
    else:
        return await embed_text(document, api_key=api_key, model=model)


async def fetch_overlap_candidates(
    conn,
    technologies: Sequence[str],
    user_id: str | None,
    *,
    limit: int = DEFAULT_FILTER_LIMIT,
) -> list[dict]:
    """Fetch projects from PostgreSQL via the hard-skill overlap filter (stage 2B).

    Executes OVERLAP_SQL with technologies list, user_id, and limit. Converts
    rows to plain dicts with keys: id, name, technologies, architectures,
    details_markdown, embedding_text, overlap_ratio.

    Args:
        conn: asyncpg connection (or compatible mock)
        technologies: List of hard skills from the job
        user_id: User ID for filtering, or None for no filter
        limit: Maximum number of projects to fetch (default: 10)

    Returns:
        List of project dicts ordered by overlap_ratio descending
    """
    rows = await conn.fetch(OVERLAP_SQL, list(technologies), user_id, limit)
    return [dict(r) for r in rows]


async def select_projects(
    conn,
    job: JobSpecification,
    user_id: str | None,
    *,
    api_key: str,
    threshold: float = DEFAULT_OVERLAP_THRESHOLD,
    filter_limit: int = DEFAULT_FILTER_LIMIT,
    min_candidates: int = DEFAULT_MIN_CANDIDATES,
    top_k: int = DEFAULT_TOP_K,
    model: str | None = None,
) -> list[SelectedProject]:
    """Orchestrate two-stage retrieval: overlap filter + cosine similarity ranking.

    Stages:
      1. Embed job architecture for cosine similarity (in-memory)
      2. Fetch overlap candidates from PostgreSQL (stage 2B)
      3. Apply threshold filter with fallback (stage 2B cont'd)
      4. Rank by cosine similarity and return top-k (stage 2C)

    Args:
        conn: asyncpg connection
        job: Target job specification
        user_id: User ID for filtering, or None
        api_key: Google GenAI API key
        threshold: Overlap ratio threshold (default: 0.8)
        filter_limit: Max projects to fetch before threshold (default: 10)
        min_candidates: Min to qualify for threshold; fallback to all if fewer
        top_k: Final selection count (default: 4)
        model: Optional embedding model

    Returns:
        List of up to top_k SelectedProject, ranked by similarity descending
    """
    # Stage 2A: Embed job architecture
    job_vector = await embed_job_architecture(job, api_key=api_key, model=model)

    # Stage 2B: Fetch and filter by hard-skill overlap
    rows = await fetch_overlap_candidates(
        conn,
        job.technologies,
        user_id,
        limit=filter_limit,
    )

    candidates = select_candidates(
        rows,
        threshold=threshold,
        min_candidates=min_candidates,
    )

    # Stage 2C: Rank by cosine similarity
    return rank_by_similarity(candidates, job_vector, top_k=top_k)
