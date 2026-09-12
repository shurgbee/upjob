"""Embeddings for project architectures.

This module generates vector embeddings of repository architecture descriptions
using the Google GenAI API.  The lazy import of google-genai inside async
functions keeps this module testable without the SDK installed.
"""

from __future__ import annotations

from typing import Sequence

from schemas import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL


def architecture_document(architectures: Sequence[str]) -> str:
    """Join architecture strings into a single text for embedding.

    Drops non-strings and blanks, collapses internal whitespace in each entry
    (" ".join(s.split())), dedupes case-insensitively while preserving
    first-seen order and original casing, and joins with "; ".

    Returns "" if nothing survives.
    """
    seen: dict[str, str] = {}
    order: list[str] = []

    for arch in architectures:
        if not isinstance(arch, str):
            continue
        # Collapse internal whitespace
        normalized = " ".join(arch.split())
        if not normalized:
            continue
        # Case-insensitive key for deduplication
        key = normalized.casefold()
        if key not in seen:
            seen[key] = normalized
            order.append(key)

    return "; ".join(seen[key] for key in order)


async def embed_architectures(
    architectures: Sequence[str],
    *,
    api_key: str,
    model: str = EMBEDDING_MODEL,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> list[float]:
    """Embed a list of architecture strings using Google GenAI.

    Builds a document from the architectures list. Raises ValueError if the
    document is empty. Lazily imports google-genai and raises RuntimeError
    with an actionable install hint if import fails. Calls the async API,
    extracts the embedding vector defensively, coerces elements with float(),
    and validates the returned length.

    Only the architectures list is embedded per spec, not the whole project
    description.
    """
    document = architecture_document(architectures)
    if not document:
        raise ValueError("no architectures to embed")

    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError(
            "google-genai is required for embedding. Install it with: "
            "pip install 'google-genai>=1.0,<2'"
        ) from e

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.embed_content(
        model=model,
        contents=document,
        config={"output_dimensionality": dimensions},
    )

    if not response.embeddings:
        raise RuntimeError("embedding response has no embeddings")

    try:
        values = response.embeddings[0].values
    except (IndexError, AttributeError) as e:
        raise RuntimeError(
            f"unable to extract embedding values from response: {e}"
        ) from e

    vector = [float(v) for v in values]

    if len(vector) != dimensions:
        raise RuntimeError(
            f"embedding dimension mismatch: got {len(vector)}, "
            f"expected {dimensions}"
        )

    return vector


def is_zero_vector(values: Sequence[float]) -> bool:
    """True when every element is exactly 0.0 (a degenerate embedding)."""
    return all(v == 0.0 for v in values)
