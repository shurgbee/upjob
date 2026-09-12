"""Embeddings for project architectures using Google GenAI.

This module generates vector embeddings of repository architecture descriptions
using the Google GenAI API. The lazy import of google-genai inside async
functions keeps this module testable without the SDK installed.
"""

from __future__ import annotations

import math
from typing import Sequence

from common.models import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, NATIVE_EMBEDDING_DIMENSIONS


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


def normalize_vector(values: Sequence[float]) -> list[float]:
    """Scale a vector to unit length, returning it unchanged if already zero.

    A zero vector has no direction to preserve, so it is returned as-is for the
    caller to reject via :func:`is_zero_vector` rather than dividing by zero.
    """
    vector = [float(v) for v in values]
    magnitude = math.sqrt(sum(v * v for v in vector))
    if magnitude == 0.0:
        return vector
    return [v / magnitude for v in vector]


def is_zero_vector(values: Sequence[float]) -> bool:
    """True when every element is exactly 0.0 (a degenerate embedding)."""
    return all(v == 0.0 for v in values)


async def embed_text(
    text: str,
    *,
    api_key: str,
    model: str = EMBEDDING_MODEL,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> list[float]:
    """Embed a single text string using Google GenAI.

    Raises ValueError if text is empty/blank after strip. Lazily imports
    google-genai and raises RuntimeError with an actionable install hint if
    import fails. Calls the async API, extracts the embedding vector defensively,
    coerces elements with float(), and validates the returned length.

    If dimensions != NATIVE_EMBEDDING_DIMENSIONS, normalizes the output vector.

    Args:
        text: Text string to embed
        api_key: Google GenAI API key
        model: Embedding model name (default: EMBEDDING_MODEL)
        dimensions: Output dimensionality (default: EMBEDDING_DIMENSIONS)

    Returns:
        List of floats representing the embedding

    Raises:
        ValueError: If text is empty/blank after strip
        RuntimeError: If google-genai is not installed or response is invalid
    """
    if not text.strip():
        raise ValueError("no text to embed")

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
        contents=text,
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

    # Truncated outputs are not unit-length as returned; see the module notes.
    if dimensions != NATIVE_EMBEDDING_DIMENSIONS:
        vector = normalize_vector(vector)

    return vector


async def embed_architectures(
    architectures: Sequence[str],
    *,
    api_key: str,
    model: str = EMBEDDING_MODEL,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> list[float]:
    """Embed a list of architecture strings using Google GenAI.

    Builds a document from the architectures list. Raises ValueError if the
    document is empty. Delegates to embed_text for the actual embedding.

    Only the architectures list is embedded per spec, not the whole project
    description.

    Args:
        architectures: List of architecture strings
        api_key: Google GenAI API key
        model: Embedding model name (default: EMBEDDING_MODEL)
        dimensions: Output dimensionality (default: EMBEDDING_DIMENSIONS)

    Returns:
        List of floats representing the embedding

    Raises:
        ValueError: If no architectures to embed
        RuntimeError: If embedding fails
    """
    document = architecture_document(architectures)
    if not document:
        raise ValueError("no architectures to embed")

    return await embed_text(document, api_key=api_key, model=model, dimensions=dimensions)
