"""Public async entry points and API routing for Reward Handler & Gamified Engine.

Implements FastAPI-callable async functions, CLI interface, and router definitions
conforming to RewardHandler.md and CLAUDE.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
from datetime import date, datetime, time, timezone
from typing import Any

# Bootstrap backend/ onto sys.path
_BACKEND_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from common.db import connect  # noqa: E402
from economy import (  # noqa: E402
    count_user_apps_in_range,
    ensure_schema,
    evaluate_lazy_rollover,
    get_or_create_user_economy,
    process_daily_rollover,
    redeem_shop_item,
    set_user_tier,
)
from leaderboard import (  # noqa: E402
    get_leaderboard,
    get_redis_client,
    get_user_rankings,
    reset_weekly_leaderboard,
)
from schemas import (  # noqa: E402
    ApplicationVerificationPayload,
    DailyTierInfo,
    EconomyMeResponse,
    QuestVerificationPayload,
    UserRankings,
    get_streak_multiplier,
    get_tier_config,
)
from verification import (  # noqa: E402
    ingest_application_verification,
    ingest_quest_verification,
)


def _resolve_dsn(dsn: str | None = None) -> str:
    resolved = dsn or os.getenv("DATABASE_URL")
    if not resolved:
        raise ValueError("DATABASE_URL is not set and no DSN was provided.")
    return resolved


# ---------------------------------------------------------------------------
# Public Async Entry Points (JSON-Serializable Envelopes)
# ---------------------------------------------------------------------------

async def get_user_economy_status(
    user_id: str,
    *,
    conn: Any = None,
    redis_client: Any = None,
    dsn: str | None = None,
    redis_url: str | None = None,
) -> dict[str, Any]:
    """Retrieve full user economy, streak, multipliers, and leaderboard standings.

    Matches the GET /api/economy/me schema in RewardHandler.md Section 7.1.
    """
    close_conn = False
    if conn is None:
        conn = await connect(_resolve_dsn(dsn))
        close_conn = True

    try:
        if redis_client is None:
            redis_client = get_redis_client(redis_url)

        # 1. Apply lazy rollover catch-up if needed
        user = await evaluate_lazy_rollover(conn, user_id)

        # 2. Count today's completed apps
        now_utc = datetime.now(timezone.utc)
        today_start = datetime.combine(now_utc.date(), time.min, tzinfo=timezone.utc)
        today_end = datetime.combine(now_utc.date(), time.max, tzinfo=timezone.utc)
        completed_today = await count_user_apps_in_range(conn, user_id, today_start, today_end)

        # 3. Tier details & multipliers
        tier_id = user.get("daily_tier_target", 1)
        tier_cfg = get_tier_config(tier_id)
        current_streak = user.get("current_streak", 0)
        mult = get_streak_multiplier(current_streak)

        # 4. Redis rankings
        rankings = await get_user_rankings(redis_client, user_id)

        resp = EconomyMeResponse(
            user_id=user_id,
            bytes=user.get("bytes", 0),
            cores=user.get("cores", 0),
            xp_score=user.get("xp_score", 0),
            current_streak=current_streak,
            active_streak_freezes=user.get("active_streak_freezes", 0),
            daily_tier=DailyTierInfo(
                tier_id=tier_id,
                name=tier_cfg["name"],
                target=tier_cfg["target"],
                completed_today=completed_today,
            ),
            streak_multiplier=mult,
            rankings=rankings,
        )
        return resp.to_dict()
    finally:
        if close_conn:
            await conn.close()


async def set_user_daily_tier(
    user_id: str,
    tier_id: int,
    *,
    conn: Any = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Set the user's daily application tier (1, 2, or 3).

    Matches POST /api/economy/set-tier in RewardHandler.md Section 7.2.
    """
    close_conn = False
    if conn is None:
        conn = await connect(_resolve_dsn(dsn))
        close_conn = True

    try:
        res = await set_user_tier(conn, user_id, tier_id)
        return res.to_dict()
    finally:
        if close_conn:
            await conn.close()


async def redeem_item(
    user_id: str,
    item_id: str,
    *,
    conn: Any = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Execute atomic shop redemption for cosmetics, freezes, or feature unlocks.

    Matches POST /api/economy/redeem in RewardHandler.md Section 7.3.
    """
    close_conn = False
    if conn is None:
        conn = await connect(_resolve_dsn(dsn))
        close_conn = True

    try:
        res = await redeem_shop_item(conn, user_id, item_id)
        return res.to_dict()
    finally:
        if close_conn:
            await conn.close()


async def fetch_leaderboard(
    leaderboard_type: str = "weekly",
    limit: int = 50,
    current_user_id: str | None = None,
    *,
    redis_client: Any = None,
    redis_url: str | None = None,
) -> dict[str, Any]:
    """Fetch top users from leaderboard and current user's standing.

    Matches GET /api/leaderboard in RewardHandler.md Section 7.4.
    """
    if redis_client is None:
        redis_client = get_redis_client(redis_url)

    res = await get_leaderboard(
        redis_client,
        leaderboard_type=leaderboard_type,
        limit=limit,
        current_user_id=current_user_id,
    )
    return res.to_dict()


async def verify_job_application(
    payload_dict: dict[str, Any],
    *,
    conn: Any = None,
    redis_client: Any = None,
    dsn: str | None = None,
    redis_url: str | None = None,
) -> dict[str, Any]:
    """Ingest and verify job application event (Section 3.A)."""
    close_conn = False
    if conn is None:
        conn = await connect(_resolve_dsn(dsn))
        close_conn = True

    try:
        if redis_client is None:
            redis_client = get_redis_client(redis_url)

        payload = ApplicationVerificationPayload(
            user_id=payload_dict.get("user_id", ""),
            job_id=payload_dict.get("job_id", ""),
            confirmation_hash=payload_dict.get("confirmation_hash", ""),
            verification_type=payload_dict.get("verification_type", "EMAIL"),
            extra_payload=payload_dict.get("extra_payload", {}),
        )
        res = await ingest_application_verification(conn, redis_client, payload)
        return res.to_dict()
    finally:
        if close_conn:
            await conn.close()


async def verify_architectural_quest(
    payload_dict: dict[str, Any],
    *,
    conn: Any = None,
    redis_client: Any = None,
    dsn: str | None = None,
    redis_url: str | None = None,
) -> dict[str, Any]:
    """Ingest and verify architectural quest event (Section 3.B)."""
    close_conn = False
    if conn is None:
        conn = await connect(_resolve_dsn(dsn))
        close_conn = True

    try:
        if redis_client is None:
            redis_client = get_redis_client(redis_url)

        payload = QuestVerificationPayload(
            user_id=payload_dict.get("user_id", ""),
            quest_id=payload_dict.get("quest_id", ""),
            architectural_component=payload_dict.get("architectural_component", ""),
            score=int(payload_dict.get("score", 0)),
            repo_url=payload_dict.get("repo_url"),
        )
        res = await ingest_quest_verification(conn, redis_client, payload)
        return res.to_dict()
    finally:
        if close_conn:
            await conn.close()


async def run_midnight_rollover(
    target_date_str: str | None = None,
    *,
    conn: Any = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Run daily midnight UTC streak rollover (Section 4)."""
    close_conn = False
    if conn is None:
        conn = await connect(_resolve_dsn(dsn))
        close_conn = True

    try:
        target_d = (
            date.fromisoformat(target_date_str)
            if target_date_str
            else None
        )
        res = await process_daily_rollover(conn, target_d)
        return res.to_dict()
    finally:
        if close_conn:
            await conn.close()


async def run_weekly_reset(
    week_start_str: str | None = None,
    *,
    conn: Any = None,
    redis_client: Any = None,
    dsn: str | None = None,
    redis_url: str | None = None,
) -> dict[str, Any]:
    """Run Monday 00:00:00 UTC weekly leaderboard reset & snapshot (Section 6)."""
    close_conn = False
    if conn is None:
        conn = await connect(_resolve_dsn(dsn))
        close_conn = True

    try:
        if redis_client is None:
            redis_client = get_redis_client(redis_url)

        week_start = (
            date.fromisoformat(week_start_str)
            if week_start_str
            else None
        )
        return await reset_weekly_leaderboard(redis_client, conn, week_start)
    finally:
        if close_conn:
            await conn.close()


# ---------------------------------------------------------------------------
# FastAPI Router Builder
# ---------------------------------------------------------------------------

def create_fastapi_router(dsn: str | None = None, redis_url: str | None = None) -> Any:
    """Create a FastAPI APIRouter exposing the economy and leaderboard routes."""
    try:
        from fastapi import APIRouter, HTTPException, Query
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError("FastAPI and Pydantic must be installed to create router.") from exc

    router = APIRouter(prefix="/api", tags=["economy"])

    class SetTierBody(BaseModel):
        tier_id: int

    class RedeemBody(BaseModel):
        item_id: str

    class VerifyAppBody(BaseModel):
        user_id: str
        job_id: str
        confirmation_hash: str
        verification_type: str = "EMAIL"
        extra_payload: dict[str, Any] = {}

    class VerifyQuestBody(BaseModel):
        user_id: str
        quest_id: str
        architectural_component: str
        score: int
        repo_url: str | None = None

    @router.get("/economy/me")
    async def get_me(user_id: str = Query(..., description="User ID")):
        return await get_user_economy_status(user_id, dsn=dsn, redis_url=redis_url)

    @router.post("/economy/set-tier")
    async def set_tier(body: SetTierBody, user_id: str = Query(..., description="User ID")):
        res = await set_user_daily_tier(user_id, body.tier_id, dsn=dsn)
        if res.get("status") == "ERROR":
            raise HTTPException(status_code=400, detail=res.get("message"))
        return res

    @router.post("/economy/redeem")
    async def redeem(body: RedeemBody, user_id: str = Query(..., description="User ID")):
        res = await redeem_item(user_id, body.item_id, dsn=dsn)
        if res.get("status") == "ITEM_NOT_FOUND":
            raise HTTPException(status_code=404, detail=res.get("message"))
        if res.get("status") == "INSUFFICIENT_FUNDS":
            raise HTTPException(status_code=400, detail=res.get("message"))
        return res

    @router.get("/leaderboard")
    async def leaderboard(
        type: str = Query("weekly", description="weekly or all_time"),
        limit: int = Query(50, ge=1, le=100),
        user_id: str | None = Query(None, description="Current user ID"),
    ):
        return await fetch_leaderboard(type, limit, user_id, redis_url=redis_url)

    @router.post("/economy/verify/application")
    async def verify_app(body: VerifyAppBody):
        res = await verify_job_application(body.model_dump(), dsn=dsn, redis_url=redis_url)
        if res.get("status") == "CONFLICT":
            raise HTTPException(status_code=409, detail=res.get("message"))
        return res

    @router.post("/economy/verify/quest")
    async def verify_quest(body: VerifyQuestBody):
        res = await verify_architectural_quest(body.model_dump(), dsn=dsn, redis_url=redis_url)
        return res

    @router.post("/economy/cron/rollover")
    async def cron_rollover(target_date: str | None = None):
        return await run_midnight_rollover(target_date, dsn=dsn)

    @router.post("/economy/cron/weekly-reset")
    async def cron_weekly_reset(week_start: str | None = None):
        return await run_weekly_reset(week_start, dsn=dsn, redis_url=redis_url)

    return router


# ---------------------------------------------------------------------------
# CLI Implementation
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reward_handler",
        description="Reward Handler & Gamified Progression Engine CLI",
    )
    parser.add_argument("--dsn", default=None, help="PostgreSQL connection string")
    parser.add_argument("--redis-url", default=None, help="Redis connection string")

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Get user economy status")
    p_status.add_argument("user_id", help="User ID")

    # set-tier
    p_tier = subparsers.add_parser("set-tier", help="Set user daily tier target")
    p_tier.add_argument("user_id", help="User ID")
    p_tier.add_argument("tier_id", type=int, choices=[1, 2, 3], help="Tier ID (1, 2, 3)")

    # redeem
    p_redeem = subparsers.add_parser("redeem", help="Redeem an item from the shop")
    p_redeem.add_argument("user_id", help="User ID")
    p_redeem.add_argument("item_id", help="Item ID in SHOP_CATALOG")

    # leaderboard
    p_lb = subparsers.add_parser("leaderboard", help="View current leaderboard")
    p_lb.add_argument("--type", default="weekly", choices=["weekly", "all_time"])
    p_lb.add_argument("--limit", type=int, default=10)
    p_lb.add_argument("--user-id", default=None, help="Highlight current user")

    # verify-app
    p_vapp = subparsers.add_parser("verify-app", help="Verify a job application")
    p_vapp.add_argument("user_id", help="User ID")
    p_vapp.add_argument("job_id", help="Job ID")
    p_vapp.add_argument("confirmation_hash", help="Unique confirmation hash")
    p_vapp.add_argument("--type", default="EMAIL", choices=["EMAIL", "VISION"])

    # verify-quest
    p_vq = subparsers.add_parser("verify-quest", help="Verify an architectural quest")
    p_vq.add_argument("user_id", help="User ID")
    p_vq.add_argument("quest_id", help="Quest ID")
    p_vq.add_argument("component", help="Architectural Component name")
    p_vq.add_argument("score", type=int, help="Score (0-100)")

    # rollover
    p_roll = subparsers.add_parser("rollover", help="Trigger daily midnight rollover")
    p_roll.add_argument("--date", default=None, help="Target date (YYYY-MM-DD)")

    # weekly-reset
    p_wreset = subparsers.add_parser("weekly-reset", help="Trigger weekly leaderboard reset")
    p_wreset.add_argument("--week-start", default=None, help="Week start date (YYYY-MM-DD)")

    return parser


async def _cli_main(args: argparse.Namespace) -> int:
    cmd = args.subcommand
    dsn = args.dsn
    redis_url = args.redis_url

    try:
        if cmd == "status":
            res = await get_user_economy_status(args.user_id, dsn=dsn, redis_url=redis_url)
        elif cmd == "set-tier":
            res = await set_user_daily_tier(args.user_id, args.tier_id, dsn=dsn)
        elif cmd == "redeem":
            res = await redeem_item(args.user_id, args.item_id, dsn=dsn)
        elif cmd == "leaderboard":
            res = await fetch_leaderboard(
                leaderboard_type=args.type,
                limit=args.limit,
                current_user_id=args.user_id,
                redis_url=redis_url,
            )
        elif cmd == "verify-app":
            payload = {
                "user_id": args.user_id,
                "job_id": args.job_id,
                "confirmation_hash": args.confirmation_hash,
                "verification_type": args.type,
            }
            res = await verify_job_application(payload, dsn=dsn, redis_url=redis_url)
        elif cmd == "verify-quest":
            payload = {
                "user_id": args.user_id,
                "quest_id": args.quest_id,
                "architectural_component": args.component,
                "score": args.score,
            }
            res = await verify_architectural_quest(payload, dsn=dsn, redis_url=redis_url)
        elif cmd == "rollover":
            res = await run_midnight_rollover(args.date, dsn=dsn)
        elif cmd == "weekly-reset":
            res = await run_weekly_reset(args.week_start, dsn=dsn, redis_url=redis_url)
        else:
            print(f"Unknown subcommand: {cmd}", file=sys.stderr)
            return 1

        print(json.dumps(res, indent=2, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "ERROR", "message": str(exc)}), file=sys.stderr)
        return 1


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    exit_code = asyncio.run(_cli_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
