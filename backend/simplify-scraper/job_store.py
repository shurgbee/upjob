"""Store job specs in TigerData (PostgreSQL + pgvector) with architecture embeddings.

Requires:
- A TigerData instance with the ``vector`` extension enabled:
    CREATE EXTENSION IF NOT EXISTS vector;
- Environment variables (or .env):
    TIGER_DB_URL=postgresql://user:pass@host:port/dbname
    GEMINI_API_KEY=...
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import asyncpg
from google import genai

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768

INIT_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS job_specs (
    id            SERIAL PRIMARY KEY,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL UNIQUE,
    company       TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT '',
    employment_type TEXT,
    description   TEXT NOT NULL DEFAULT '',
    requirements  TEXT[] NOT NULL DEFAULT '{}',
    technologies  TEXT[] NOT NULL DEFAULT '{}',
    architecture  TEXT[] NOT NULL DEFAULT '{}',
    yoe           INTEGER NOT NULL DEFAULT 0,
    publish_date  TIMESTAMPTZ,
    spec_created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    spec_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    arch_embedding vector(768)
);

CREATE INDEX IF NOT EXISTS idx_job_specs_technologies
    ON job_specs USING GIN (technologies);

CREATE INDEX IF NOT EXISTS idx_job_specs_arch_embedding
    ON job_specs USING ivfflat (arch_embedding vector_cosine_ops)
    WITH (lists = 100);
"""


def _arch_text(architecture: list[str]) -> str:
    """Join architecture items into a single string for embedding."""
    return ", ".join(str(item) for item in architecture)


def _as_text(value: Any, fallback: str = "") -> str:
    """Coerce a spec field to a plain string so it never hits a TEXT column as an object."""
    return value if isinstance(value, str) else fallback


def _string_or_none(value: Any) -> str | None:
    """Coerce a nullable TEXT field: keep real strings, otherwise NULL."""
    return value if isinstance(value, str) else None


def _is_writable_spec(spec: dict[str, Any]) -> bool:
    """A spec is safe to persist only if it carries no error marker and has a real URL."""
    return "error" not in spec and bool(_as_text(spec.get("url")).strip())


async def _embed(client: genai.Client, texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using Gemini embedding model."""
    response = await client.aio.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config={"output_dimensionality": EMBEDDING_DIMENSIONS},
    )
    return [e.values for e in response.embeddings]


async def _embed_single(client: genai.Client, text: str) -> list[float]:
    results = await _embed(client, [text])
    return results[0]


async def connect(db_url: str | None = None) -> asyncpg.Connection:
    # The rest of Upjob uses POSTGRES_URL. Keep TIGER_DB_URL as an optional
    # explicit override for a separate TigerData instance.
    url = db_url or os.environ.get("TIGER_DB_URL") or os.environ.get("POSTGRES_URL")
    if not url:
        raise RuntimeError("TIGER_DB_URL or POSTGRES_URL is not set")
    conn = await asyncpg.connect(url)
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    await conn.execute(
        "SET LOCAL maintenance_work_mem = '256MB';"
    )
    return conn


async def init_db(conn: asyncpg.Connection) -> None:
    await conn.execute(INIT_SQL)


async def upsert_job_specs(
    conn: asyncpg.Connection,
    job_specs: Sequence[dict[str, Any]],
    gemini_api_key: str | None = None,
) -> int:
    """Embed architecture fields and upsert job specs into the database.

    Returns the number of rows upserted.
    """
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
    api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    client = genai.Client(api_key=api_key)

    # Last line of defence: never persist error-shaped or url-less specs.
    job_specs = [s for s in job_specs if _is_writable_spec(s)]

    specs_with_arch = [s for s in job_specs if s.get("architecture")]
    arch_texts = [_arch_text(s["architecture"]) for s in specs_with_arch]

    embeddings: dict[str, list[float]] = {}
    batch_size = 100
    for i in range(0, len(arch_texts), batch_size):
        batch = arch_texts[i : i + batch_size]
        batch_embeddings = await _embed(client, batch)
        for spec, emb in zip(specs_with_arch[i : i + batch_size], batch_embeddings):
            embeddings[spec["url"]] = emb

    count = 0
    for spec in job_specs:
        embedding = embeddings.get(spec["url"])
        embedding_str = json.dumps(embedding) if embedding else None

        await conn.execute(
            """
            INSERT INTO job_specs (title, url, company, category, employment_type,
                                   description, requirements, technologies, architecture,
                                   yoe, publish_date, spec_created_at, spec_updated_at,
                                   arch_embedding)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text[], $9::text[], $10, $11, $12, $13, $14::vector)
            ON CONFLICT (url) DO UPDATE SET
                title = EXCLUDED.title,
                company = EXCLUDED.company,
                category = EXCLUDED.category,
                employment_type = EXCLUDED.employment_type,
                description = EXCLUDED.description,
                requirements = EXCLUDED.requirements,
                technologies = EXCLUDED.technologies,
                architecture = EXCLUDED.architecture,
                yoe = EXCLUDED.yoe,
                publish_date = EXCLUDED.publish_date,
                spec_updated_at = EXCLUDED.spec_updated_at,
                arch_embedding = EXCLUDED.arch_embedding
            """,
            _as_text(spec.get("title")),
            _as_text(spec["url"]),
            _as_text(spec.get("company")),
            _as_text(spec.get("category")),
            _string_or_none(spec.get("employment_type")),
            _as_text(spec.get("description")),
            json.dumps(spec.get("requirements", [])),
            [str(t) for t in spec.get("technologies", [])],
            [str(a) for a in spec.get("architecture", [])],
            spec.get("yoe", 0),
            _parse_ts(spec.get("publish_date")),
            _parse_ts(spec.get("spec_created_at")) or datetime.now(timezone.utc),
            datetime.now(timezone.utc),
            embedding_str,
        )
        count += 1

    return count


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


async def match_jobs(
    conn: asyncpg.Connection,
    skills: list[str],
    architecture: list[str],
    gemini_api_key: str | None = None,
    skill_limit: int = 10,
    final_limit: int = 4,
) -> list[dict[str, Any]]:
    """Two-phase match: filter by skills (top N), then rank by architecture similarity.

    1. Filter job_specs where technologies overlap with ``skills`` → top ``skill_limit``
    2. Embed ``architecture``, run cosine similarity against the filtered set → top ``final_limit``
    """
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
    api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    client = genai.Client(api_key=api_key)

    skill_rows = await conn.fetch(
        """
        SELECT id, title, url, technologies, architecture, yoe,
               publish_date, arch_embedding
        FROM job_specs
        WHERE technologies && $1
        ORDER BY array_length(
            ARRAY(SELECT unnest(technologies) INTERSECT SELECT unnest($1::text[])),
            1
        ) DESC NULLS LAST
        LIMIT $2
        """,
        skills,
        skill_limit,
    )

    if not skill_rows:
        return []

    if not architecture:
        return [_row_to_dict(r) for r in skill_rows[:final_limit]]

    query_embedding = await _embed_single(client, _arch_text(architecture))
    query_vec_str = json.dumps(query_embedding)

    filtered_ids = [r["id"] for r in skill_rows]

    results = await conn.fetch(
        """
        SELECT id, title, url, technologies, architecture, yoe, publish_date,
               1 - (arch_embedding <=> $1::vector) AS similarity
        FROM job_specs
        WHERE id = ANY($2)
          AND arch_embedding IS NOT NULL
        ORDER BY arch_embedding <=> $1::vector
        LIMIT $3
        """,
        query_vec_str,
        filtered_ids,
        final_limit,
    )

    return [_row_to_dict(r, similarity=r["similarity"]) for r in results]


def _row_to_dict(row: asyncpg.Record, similarity: float | None = None) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "technologies": list(row["technologies"]),
        "architecture": list(row["architecture"]),
        "yoe": row["yoe"],
        "publish_date": row["publish_date"].isoformat() if row["publish_date"] else None,
    }
    if similarity is not None:
        result["similarity"] = round(similarity, 4)
    return result
