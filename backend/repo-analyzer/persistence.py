"""Database persistence layer for repository analysis.

This module provides async functions for connecting to and querying a PostgreSQL
database with pgvector support. It follows a lazy import pattern for asyncpg so
that unit tests can run without asyncpg installed.

All SQL operations use parameterized queries and proper transaction handling.
Async context managers ensure resources are properly released.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # backend/

import json
import logging
from pathlib import Path
from typing import Any, Sequence

from common.db import connect, format_vector_literal, normalize_dsn, parse_timestamp, ssl_argument  # noqa: F401

logger = logging.getLogger(__name__)


async def ensure_schema(conn: Any, migrations_dir: Path | None = None) -> None:
    """Execute all schema migrations on the connection.

    Reads all *.sql files in migrations directory in sorted filename order
    and executes each one with conn.execute.

    Args:
        conn: An asyncpg connection
        migrations_dir: Directory containing migration files. If None, uses
                       migrations/ relative to this file.
    """
    if migrations_dir is None:
        migrations_dir = Path(__file__).parent / "migrations"

    migration_files = sorted(migrations_dir.glob("*.sql"))
    for migration_file in migration_files:
        sql = migration_file.read_text()
        await conn.execute(sql)
        logger.info("Schema migration applied: %s", migration_file)


async def upsert_project(
    conn: Any,
    specification: dict[str, Any],
    details: dict[str, Any],
    details_markdown: str,
    user_id: str | None = None,
) -> int:
    """Insert or update a project specification.

    Updates if a project with the same github_repo_url already exists;
    otherwise inserts a new row. All other fields are updated, but
    spec_created_at is never changed.

    Args:
        conn: An asyncpg connection
        specification: ProjectSpecification.to_dict() with keys:
                      name, description, github_repo_url, start_time, end_time,
                      technologies, architectures
        details: Details dict to serialize as JSONB
        details_markdown: Markdown representation of details
        user_id: Optional user ID identifying the owning user

    Returns:
        The project id (either newly inserted or existing)
    """
    # Parse timestamps to timezone-aware datetimes
    start_time_dt = parse_timestamp(specification.get("start_time"))
    end_time_dt = parse_timestamp(specification.get("end_time"))

    # Serialize details to JSON
    details_json = json.dumps(details)

    query = """
        INSERT INTO projects (
            name, description, github_repo_url, start_time, end_time,
            technologies, architectures, details, details_markdown,
            user_id, spec_created_at, spec_updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, now(), now()
        )
        ON CONFLICT (github_repo_url) DO UPDATE SET
            name = $1,
            description = $2,
            start_time = $4,
            end_time = $5,
            technologies = $6,
            architectures = $7,
            details = $8::jsonb,
            details_markdown = $9,
            user_id = $10,
            spec_updated_at = now()
        RETURNING id
    """

    row = await conn.fetchrow(
        query,
        specification["name"],
        specification["description"],
        specification["github_repo_url"],
        start_time_dt,
        end_time_dt,
        specification.get("technologies", []),
        specification.get("architectures", []),
        details_json,
        details_markdown,
        user_id,
    )
    return row["id"]


async def replace_architecture_embedding(
    conn: Any,
    project_id: int,
    content: str,
    embedding: Sequence[float],
) -> None:
    """Replace the architecture embedding for a project.

    Deletes any existing embeddings for the project and inserts a new one,
    in a single transaction so the project never transiently has zero embeddings.

    The vector is passed as a text literal and explicitly cast to ::vector because
    asyncpg does not have a native vector codec registered. Format the embedding
    using format_vector_literal() to ensure correct syntax.

    Args:
        conn: An asyncpg connection
        project_id: The project's id
        content: The combined architecture string that was embedded
        embedding: Sequence of floats (length must be EMBEDDING_DIMENSIONS)

    Raises:
        ValueError: If embedding length or values are invalid (from format_vector_literal)
    """
    vector_literal = format_vector_literal(embedding)

    async with conn.transaction():
        # Delete any existing embeddings for this project
        await conn.execute(
            "DELETE FROM project_architectures WHERE project_id = $1", project_id
        )

        # Insert the new embedding (explicit ::vector cast for text literal)
        await conn.execute(
            """
            INSERT INTO project_architectures (project_id, content, embedding, created_at)
            VALUES ($1, $2, $3::vector, now())
            """,
            project_id,
            content,
            vector_literal,
        )
