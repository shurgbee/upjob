"""Database persistence helpers for pgvector and asyncpg.

This module provides utilities for connecting to PostgreSQL with pgvector support
and formatting vector literals. All operations are async-ready with lazy imports
of asyncpg so that unit tests can run without it installed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from common.models import EMBEDDING_DIMENSIONS


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
