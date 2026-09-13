"""Umbrella FastAPI app for the upjob backend.

Run from this directory with::

    uvicorn main:app --reload

Each backend component keeps its own project directory and exposes either a
FastAPI router or async entry points.  This app mounts those routers.  To add a
new component: append its source directory to the ``sys.path`` bootstrap below,
import its ``create_*_router`` factory, and ``app.include_router`` it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

BASE_DIR = Path(__file__).resolve().parent
REWARD_HANDLER_DIR = BASE_DIR / "reward-handler"

# Components run from their own directories, so they are not importable by
# default.  Add each component source root, plus BASE_DIR for the shared
# ``common`` package that the components import.
for source_root in (REWARD_HANDLER_DIR, BASE_DIR):
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

# Load local development configuration without overriding deployment-provided
# environment variables.
for dotenv_path in (BASE_DIR / ".env", REWARD_HANDLER_DIR / ".env"):
    load_dotenv(dotenv_path=dotenv_path, override=False)

from reward_handler import create_fastapi_router

# reward-handler reads DATABASE_URL / REDIS_URL; the rest of the backend uses
# POSTGRES_URL.  Prefer the reward-handler names and fall back to POSTGRES_URL so
# every component targets one database.  ``None`` lets each entry point resolve
# its own environment variable (and, for Redis, fall back to the in-memory
# leaderboard when REDIS_URL is unset).
DATABASE_DSN = (
    os.getenv("DATABASE_URL", "").strip()
    or os.getenv("POSTGRES_URL", "").strip()
    or None
)
REDIS_URL = os.getenv("REDIS_URL", "").strip() or None

app = FastAPI(
    title="Upjob Backend API",
    version="1.0.0",
    description="Umbrella HTTP interface for the upjob backend components.",
)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Report that the API process is available without calling external services."""

    return {"status": "ok", "services": ["reward-handler"]}


# reward-handler: economy, verification, leaderboard, and cron routes under /api.
app.include_router(create_fastapi_router(dsn=DATABASE_DSN, redis_url=REDIS_URL))
