"""Database persistence layer for repository analysis.

This module provides async functions for connecting to and querying a PostgreSQL
database with pgvector support. It follows a lazy import pattern for asyncpg so
that unit tests can run without asyncpg installed.

All SQL operations use parameterized queries and proper transaction handling.
Async context managers ensure resources are properly released.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from schemas import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a timestamp value into a timezone-aware datetime or None.

    Accepts:
    - None, empty string, "Present" (case-insensitive) -> None
    - ISO-8601 strings with optional trailing "Z" (mapped to +00:00)
    - ISO-8601 strings with timezone offset

    Unparseable values return None rather than raising.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value_lower = value.lower().strip()
        if value_lower == "present":
            return None
    if not isinstance(value, str):
        return None
    try:
        value_clean = value.strip()
        # Map "Z" suffix to "+00:00" for fromisoformat compatibility
        if value_clean.endswith("Z"):
            value_clean = value_clean[:-1] + "+00:00"
        dt = datetime.fromisoformat(value_clean)
        # Ensure timezone-aware; assume UTC if naive
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def normalize_dsn(dsn: str) -> tuple[str, str | None]:
    """Normalize a PostgreSQL connection string.

    Returns (clean_dsn, sslmode) where:
    - clean_dsn: DSN with postgres:// rewritten to postgresql:// and sslmode
      query parameter removed (but all other params preserved)
    - sslmode: The sslmode value if present, otherwise None

    Example:
        >>> normalize_dsn("postgres://user:pw@host/db?sslmode=require&application_name=test")
        ("postgresql://user:pw@host/db?application_name=test", "require")
    """
    parsed = urlparse(dsn)

    # Rewrite postgres:// scheme to postgresql://
    scheme = "postgresql" if parsed.scheme == "postgres" else parsed.scheme

    # Parse and filter query parameters
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    sslmode = None
    if "sslmode" in query_params:
        # parse_qs returns lists; take the first value
        sslmode_list = query_params.pop("sslmode")
        sslmode = sslmode_list[0] if sslmode_list else None

    # Rebuild the query string (encode=True to flatten list values)
    new_query = urlencode(query_params, doseq=True)

    # Reconstruct the DSN
    clean_dsn = urlunparse(
        (scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )

    return clean_dsn, sslmode


def ssl_argument(sslmode: str | None) -> object:
    """Map a libpq sslmode to asyncpg's ssl= parameter.

    Args:
        sslmode: libpq sslmode value or None

    Returns:
        - "disable" -> False
        - "allow", "prefer" -> "prefer"
        - "require" -> "require"
        - "verify-ca" -> "verify-ca"
        - "verify-full" -> "verify-full"
        - None -> None
        - Unknown values -> "prefer"
    """
    if sslmode is None:
        return None
    if sslmode == "disable":
        return False
    if sslmode in ("allow", "prefer"):
        return "prefer"
    if sslmode in ("require", "verify-ca", "verify-full"):
        return sslmode
    # Unknown sslmode: default to "prefer"
    return "prefer"


def format_vector_literal(values: Sequence[float]) -> str:
    """Format a sequence of floats as a pgvector literal string.

    Returns a string like "[0.1,0.2,0.3]" with no spaces.

    Args:
        values: Sequence of floats

    Returns:
        pgvector text literal string

    Raises:
        ValueError: If length != EMBEDDING_DIMENSIONS or any value is not a
                   finite real number (rejects NaN, inf, bool, str, None)
    """
    if len(values) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Expected {EMBEDDING_DIMENSIONS} values, got {len(values)}"
        )

    formatted_parts = []
    for v in values:
        # Reject non-numeric types
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(
                f"Vector element must be a finite real number, got {type(v).__name__}: {v!r}"
            )
        # Convert to float and check for NaN/inf
        fv = float(v)
        if not (-1e308 < fv < 1e308):  # Crude check for inf
            raise ValueError(f"Vector element is not finite: {fv!r}")
        # Use repr to format; it gives exact float representation
        formatted_parts.append(repr(fv))

    return "[" + ",".join(formatted_parts) + "]"


async def connect(dsn: str) -> Any:
    """Establish an asyncpg connection to a PostgreSQL database.

    Normalizes the DSN (rewrites postgres:// -> postgresql://, extracts sslmode),
    then connects with the appropriate SSL settings.

    Args:
        dsn: PostgreSQL connection string (may include sslmode query param)

    Returns:
        An asyncpg connection object

    Example:
        >>> conn = await connect("postgresql://user:pw@localhost:5432/mydb")
        >>> await conn.close()
    """
    import asyncpg

    clean_dsn, sslmode = normalize_dsn(dsn)
    ssl_arg = ssl_argument(sslmode)

    # Build connection kwargs
    connect_kwargs = {"ssl": ssl_arg} if ssl_arg is not None else {}
    return await asyncpg.connect(clean_dsn, **connect_kwargs)


async def ensure_schema(conn: Any, migrations_dir: Path | None = None) -> None:
    """Execute the initial schema migration on the connection.

    Reads migrations/001_init.sql relative to this module by default
    and executes it with conn.execute.

    Args:
        conn: An asyncpg connection
        migrations_dir: Directory containing migration files. If None, uses
                       migrations/ relative to this file.
    """
    if migrations_dir is None:
        migrations_dir = Path(__file__).parent / "migrations"

    migration_file = migrations_dir / "001_init.sql"
    sql = migration_file.read_text()
    await conn.execute(sql)
    logger.info("Schema migration applied: %s", migration_file)


async def upsert_project(
    conn: Any,
    specification: dict[str, Any],
    details: dict[str, Any],
    details_markdown: str,
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
            spec_created_at, spec_updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, now(), now()
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
