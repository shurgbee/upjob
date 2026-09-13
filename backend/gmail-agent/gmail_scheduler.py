"""2-hour APScheduler job that runs the Gmail sync.

``start_gmail_scheduler`` is called from the umbrella app's FastAPI lifespan and
returns the scheduler so the caller can shut it down. The interval job calls the
same ``sync_recent_threads`` entry point the HTTP route uses; failures are logged,
never raised, so one bad run does not kill the scheduler.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # backend/

import logging
from typing import Any

from gmail_agent import sync_recent_threads

logger = logging.getLogger(__name__)

SYNC_INTERVAL_HOURS = 2


def start_gmail_scheduler(
    dsn: str | None = None,
    gemini_api_key: str | None = None,
    *,
    interval_hours: int = SYNC_INTERVAL_HOURS,
) -> Any:
    """Create and start an AsyncIOScheduler running the sync every N hours."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    async def _run() -> None:
        try:
            summary = await sync_recent_threads(
                hours=interval_hours, dsn=dsn, gemini_api_key=gemini_api_key
            )
            logger.info("gmail-agent scheduled sync: %s", summary)
        except Exception:  # noqa: BLE001 - keep the scheduler alive across failures
            logger.exception("gmail-agent scheduled sync failed")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run,
        trigger="interval",
        hours=interval_hours,
        id="gmail_sync",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("gmail-agent scheduler started (every %dh)", interval_hours)
    return scheduler
