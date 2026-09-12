"""Backward-compatible shim: embeddings live in common.embeddings now."""
from __future__ import annotations

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # backend/

from common.embeddings import (  # noqa: F401
    architecture_document, embed_architectures, embed_text,
    normalize_vector, is_zero_vector, NATIVE_EMBEDDING_DIMENSIONS,
)
