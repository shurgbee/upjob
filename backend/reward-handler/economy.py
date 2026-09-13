"""Core economy engine, state machine, and transaction logic.

Manages user economy balances, streak rollover calculations, milestone awards,
and atomic shop debits adhering to RewardHandler.md.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

# Bootstrap backend/ onto sys.path
_BACKEND_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from schemas import (  # noqa: E402
    APPLICATION_TIERS,
    MILESTONES,
    SHOP_CATALOG,
    DailyTierInfo,
    EconomyMeResponse,
    RedeemResponse,
    RolloverBatchResponse,
    RolloverUserResult,
    SetTierResponse,
    UserRankings,
    get_streak_multiplier,
    get_tier_config,
    validate_tier_id,
)

# ---------------------------------------------------------------------------
# Pure State Transition Helpers (Testable without DB)
# ---------------------------------------------------------------------------

def compute_rollover_transition(
    current_streak: int,
    active_freezes: int,
    apps_completed: int,
    daily_target: int,
) -> tuple[int, int, bool, dict[str, Any] | None]:
    """Pure calculation for a single daily rollover transition.

    Returns:
        tuple of (new_streak, new_freezes, streak_preserved, milestone_awarded)
    """
    target_met = apps_completed >= daily_target
    if target_met:
        new_streak = current_streak + 1
        new_freezes = active_freezes
        streak_preserved = True
        milestone = MILESTONES.get(new_streak)
        return new_streak, new_freezes, streak_preserved, milestone

    # Target not met: check streak freeze
    if active_freezes > 0:
        new_streak = current_streak
        new_freezes = active_freezes - 1
        streak_preserved = True
        return new_streak, new_freezes, streak_preserved, None

    # No freeze available: streak resets to 0
    return 0, 0, False, None


def calculate_day_bounds_utc(target_date: date) -> tuple[datetime, datetime]:
    """Return start (00:00:00 UTC) and end (00:00:00 UTC next day) datetimes."""
    start_dt = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return start_dt, end_dt


# ---------------------------------------------------------------------------
# Database Persistence & Schema Management
# ---------------------------------------------------------------------------

async def ensure_schema(conn: Any) -> None:
    """Read and apply migrations/001_reward_handler_schema.sql if needed."""
    schema_path = pathlib.Path(__file__).resolve().parent / "migrations" / "001_reward_handler_schema.sql"
    if schema_path.is_file():
        sql = schema_path.read_text(encoding="utf-8")
        await conn.execute(sql)


async def get_or_create_user_economy(conn: Any, user_id: str) -> dict[str, Any]:
    """Fetch user_economy record, creating default records if user does not exist."""
    today_utc = datetime.now(timezone.utc).date()
    row = await conn.fetchrow(
        """
        SELECT user_id, bytes, cores, xp_score, current_streak,
               active_streak_freezes, daily_tier_target, last_active_date,
               last_rollover_date, created_at, updated_at
        FROM user_economy
        WHERE user_id = $1
        """,
        user_id,
    )
    if row:
        return dict(row)

    # Initialize new user economy & inventory
    await conn.execute(
        """
        INSERT INTO user_economy (
            user_id, bytes, cores, xp_score, current_streak,
            active_streak_freezes, daily_tier_target, last_rollover_date
        ) VALUES ($1, 0, 0, 0, 0, 0, 1, $2)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
        today_utc,
    )
    await conn.execute(
        """
        INSERT INTO user_inventory (user_id)
        VALUES ($1)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
    )

    row = await conn.fetchrow(
        """
        SELECT user_id, bytes, cores, xp_score, current_streak,
               active_streak_freezes, daily_tier_target, last_active_date,
               last_rollover_date, created_at, updated_at
        FROM user_economy
        WHERE user_id = $1
        """,
        user_id,
    )
    return dict(row)


async def get_user_inventory(conn: Any, user_id: str) -> dict[str, Any]:
    """Retrieve user's inventory (badges, cosmetics, unlocked features, verified architectures)."""
    row = await conn.fetchrow(
        """
        SELECT user_id, badges, cosmetics, unlocked_features, verified_architectures
        FROM user_inventory
        WHERE user_id = $1
        """,
        user_id,
    )
    if not row:
        return {
            "user_id": user_id,
            "badges": [],
            "cosmetics": [],
            "unlocked_features": {},
            "verified_architectures": [],
        }
    return {
        "user_id": row["user_id"],
        "badges": list(row["badges"] or []),
        "cosmetics": list(row["cosmetics"] or []),
        "unlocked_features": dict(row["unlocked_features"] or {}),
        "verified_architectures": list(row["verified_architectures"] or []),
    }


async def set_user_tier(conn: Any, user_id: str, tier_id: int) -> SetTierResponse:
    """Set the user's daily application tier target (1, 2, or 3)."""
    if not validate_tier_id(tier_id):
        return SetTierResponse(
            status="ERROR",
            new_tier=tier_id,
            tier_name="Unknown",
            daily_target=0,
            message=f"Invalid tier_id: {tier_id}. Must be 1, 2, or 3.",
        )

    tier_cfg = get_tier_config(tier_id)
    # Ensure user exists
    await get_or_create_user_economy(conn, user_id)

    await conn.execute(
        """
        UPDATE user_economy
        SET daily_tier_target = $1, updated_at = NOW()
        WHERE user_id = $2
        """,
        tier_id,
        user_id,
    )

    return SetTierResponse(
        status="SUCCESS",
        new_tier=tier_id,
        tier_name=tier_cfg["name"],
        daily_target=tier_cfg["target"],
        message=f"Tier updated to {tier_cfg['name']}.",
    )


async def count_user_apps_in_range(
    conn: Any, user_id: str, start_dt: datetime, end_dt: datetime
) -> int:
    """Count verified applications completed by user within a UTC datetime range."""
    count = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM verification_logs
        WHERE user_id = $1
          AND verified_at >= $2
          AND verified_at < $3
        """,
        user_id,
        start_dt,
        end_dt,
    )
    return int(count or 0)


# ---------------------------------------------------------------------------
# Streak Rollover Logic (Scheduled Cron & Lazy Evaluation)
# ---------------------------------------------------------------------------

async def process_user_rollover(
    conn: Any, user_row: dict[str, Any], target_date: date
) -> RolloverUserResult:
    """Execute rollover calculation for a single user for target_date (yesterday)."""
    user_id = user_row["user_id"]
    current_streak = user_row.get("current_streak", 0)
    active_freezes = user_row.get("active_streak_freezes", 0)
    tier_id = user_row.get("daily_tier_target", 1)
    tier_cfg = get_tier_config(tier_id)
    target_required = tier_cfg["target"]

    # Applications on target_date (00:00:00 UTC to 23:59:59.999 UTC)
    start_dt, end_dt = calculate_day_bounds_utc(target_date)
    apps_completed = await count_user_apps_in_range(conn, user_id, start_dt, end_dt)

    new_streak, new_freezes, preserved, milestone = compute_rollover_transition(
        current_streak=current_streak,
        active_freezes=active_freezes,
        apps_completed=apps_completed,
        daily_target=target_required,
    )

    bonus_cores = 0
    if milestone:
        bonus_cores = milestone.get("cores", 0)
        badge_id = milestone.get("badge_id")
        if badge_id:
            await conn.execute(
                """
                UPDATE user_inventory
                SET badges = array_append(badges, $1), updated_at = NOW()
                WHERE user_id = $2 AND NOT ($1 = ANY(badges))
                """,
                badge_id,
                user_id,
            )

    # Persist updated streak, freezes, cores, and last_rollover_date
    await conn.execute(
        """
        UPDATE user_economy
        SET current_streak = $1,
            active_streak_freezes = $2,
            cores = cores + $3,
            last_rollover_date = $4,
            updated_at = NOW()
        WHERE user_id = $5
        """,
        new_streak,
        new_freezes,
        bonus_cores,
        target_date + timedelta(days=1),  # advanced to today
        user_id,
    )

    return RolloverUserResult(
        user_id=user_id,
        previous_streak=current_streak,
        new_streak=new_streak,
        apps_completed=apps_completed,
        target_required=target_required,
        streak_preserved=preserved,
        freezes_remaining=new_freezes,
        milestone_awarded=milestone,
    )


async def process_daily_rollover(
    conn: Any, target_date: date | None = None
) -> RolloverBatchResponse:
    """Batch rollover runner intended for 00:00:00 UTC daily cron.

    Args:
        conn: asyncpg connection
        target_date: Day being evaluated. Defaults to yesterday in UTC.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date() - timedelta(days=1)

    users = await conn.fetch(
        """
        SELECT user_id, current_streak, active_streak_freezes, daily_tier_target, last_rollover_date
        FROM user_economy
        """
    )
    results: list[RolloverUserResult] = []
    for user_record in users:
        u_dict = dict(user_record)
        # Skip if already rolled over for this date
        last_roll = u_dict.get("last_rollover_date")
        if last_roll and last_roll > target_date:
            continue
        res = await process_user_rollover(conn, u_dict, target_date)
        results.append(res)

    return RolloverBatchResponse(
        status="SUCCESS",
        processed_count=len(results),
        results=results,
        message=f"Processed midnight rollover for {len(results)} users as of {target_date.isoformat()}.",
    )


async def evaluate_lazy_rollover(
    conn: Any, user_id: str, as_of_date: date | None = None
) -> dict[str, Any]:
    """Check if user has un-evaluated days prior to as_of_date and catch up.

    Ensures streaks remain consistent even if scheduled cron is delayed.
    """
    if as_of_date is None:
        as_of_date = datetime.now(timezone.utc).date()

    user = await get_or_create_user_economy(conn, user_id)
    last_rollover = user.get("last_rollover_date") or user.get("created_at").date()

    # If last_rollover is older than yesterday, step forward day by day
    eval_date = last_rollover
    yesterday = as_of_date - timedelta(days=1)

    while eval_date <= yesterday:
        await process_user_rollover(conn, user, eval_date)
        # Refresh user state for the next step
        user = await get_or_create_user_economy(conn, user_id)
        eval_date += timedelta(days=1)

    return user


# ---------------------------------------------------------------------------
# Atomic Shop Redemptions (RewardHandler.md Section 5)
# ---------------------------------------------------------------------------

async def redeem_shop_item(conn: Any, user_id: str, item_id: str) -> RedeemResponse:
    """Execute shop purchase inside an atomic transaction with row locking.

    Steps:
    1. Validation: Item exists in SHOP_CATALOG.
    2. Affordability: bytes >= cost_bytes and cores >= cost_cores.
    3. Atomic Debit: SELECT ... FOR UPDATE followed by UPDATE.
    4. Feature / Cosmetic / Freeze allocation.
    """
    if item_id not in SHOP_CATALOG:
        return RedeemResponse(
            status="ITEM_NOT_FOUND",
            remaining_bytes=0,
            remaining_cores=0,
            unlocked="",
            message=f"Item '{item_id}' not found in shop catalog.",
        )

    item = SHOP_CATALOG[item_id]
    cost_bytes = item["cost_bytes"]
    cost_cores = item["cost_cores"]
    item_type = item["type"]

    async with conn.transaction():
        # Row-level lock on user_economy
        row = await conn.fetchrow(
            """
            SELECT bytes, cores, active_streak_freezes
            FROM user_economy
            WHERE user_id = $1
            FOR UPDATE
            """,
            user_id,
        )
        if not row:
            # User doesn't exist yet, create default
            await get_or_create_user_economy(conn, user_id)
            row = await conn.fetchrow(
                """
                SELECT bytes, cores, active_streak_freezes
                FROM user_economy
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )

        current_bytes = row["bytes"]
        current_cores = row["cores"]

        # Affordability check
        if current_bytes < cost_bytes or current_cores < cost_cores:
            return RedeemResponse(
                status="INSUFFICIENT_FUNDS",
                remaining_bytes=current_bytes,
                remaining_cores=current_cores,
                unlocked="",
                message=(
                    f"Insufficient currency. Requires {cost_bytes} Bytes and {cost_cores} Cores. "
                    f"You have {current_bytes} Bytes and {current_cores} Cores."
                ),
            )

        # Atomic debit
        freeze_add = 1 if item_id == "streak_freeze" else 0
        updated_row = await conn.fetchrow(
            """
            UPDATE user_economy
            SET bytes = bytes - $1,
                cores = cores - $2,
                active_streak_freezes = active_streak_freezes + $3,
                updated_at = NOW()
            WHERE user_id = $4
              AND bytes >= $1
              AND cores >= $2
            RETURNING bytes, cores, active_streak_freezes
            """,
            cost_bytes,
            cost_cores,
            freeze_add,
            user_id,
        )
        if not updated_row:
            return RedeemResponse(
                status="INSUFFICIENT_FUNDS",
                remaining_bytes=current_bytes,
                remaining_cores=current_cores,
                unlocked="",
                message="Transaction failed due to concurrent balance debit.",
            )

        # Apply inventory updates
        if item_type == "cosmetic":
            await conn.execute(
                """
                UPDATE user_inventory
                SET cosmetics = array_append(cosmetics, $1), updated_at = NOW()
                WHERE user_id = $2 AND NOT ($1 = ANY(cosmetics))
                """,
                item_id,
                user_id,
            )
        elif item_type == "feature":
            await conn.execute(
                """
                UPDATE user_inventory
                SET unlocked_features = jsonb_set(
                    COALESCE(unlocked_features, '{}'::jsonb),
                    ARRAY[$1],
                    'true'::jsonb,
                    true
                ),
                updated_at = NOW()
                WHERE user_id = $2
                """,
                item_id,
                user_id,
            )

        return RedeemResponse(
            status="SUCCESS",
            remaining_bytes=updated_row["bytes"],
            remaining_cores=updated_row["cores"],
            unlocked=item_id,
            message=f"Successfully redeemed '{item_id}'.",
        )
