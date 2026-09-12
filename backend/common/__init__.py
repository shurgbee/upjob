"""Shared helpers for the upjob backend components.

This package holds the code reused across sibling project roots (``repo-analyzer``,
``resume-tailor``): database connection/DSN handling, the Gemini structured-call
layer with retries, embeddings, shared model identifiers, and LaTeX escaping.

Import as ``from common.db import connect`` etc.  Because each component runs from
its own directory, the ``backend/`` directory (this package's parent) must be on
``sys.path`` before importing ``common``.  Components add it with a raw bootstrap
at the top of every entry point and test module (it cannot live in ``common``
itself -- importing it would already require the path)::

    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend/
"""
