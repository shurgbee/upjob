"""Shared Gemini model identifiers and embedding geometry.

Single source of truth so every component embeds and generates with the same
models -- critical for the resume tailor, whose cosine search only makes sense
when the job-specification embedding and the stored project embeddings come from
the same model at the same dimensionality.
"""

from __future__ import annotations

#: Generation model for both repository analysis and resume bullet generation.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"

#: Embedding model. The spec's "gemini-embedding-1" is not a real name; the v1
#: model is "gemini-embedding-001". It must match what repo-analyzer stored.
EMBEDDING_MODEL = "gemini-embedding-001"

#: Stored/queried embedding dimensionality. gemini-embedding-001 returns
#: unit-length vectors only at its native 3072 dims, so reduced-dimension output
#: (768) must be L2-normalized by hand before use or storage.
EMBEDDING_DIMENSIONS = 768
NATIVE_EMBEDDING_DIMENSIONS = 3072
