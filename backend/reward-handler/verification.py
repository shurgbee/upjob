"""Verification event ingestion for job applications and architectural quests.

Implements anti-cheat duplicate protection (409 Conflict), dynamic tier and streak
multiplier payouts, and real-time Redis leaderboard synchronization.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys
from datetime import datetime, time, timezone
from typing import Any

# Bootstrap backend/ onto sys.path
_BACKEND_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from economy import get_or_create_user_economy  # noqa: E402
from leaderboard import update_leaderboard  # noqa: E402
from schemas import (  # noqa: E402
    APPLICATION_TIERS,
    QUEST_BASE_BYTES,
    QUEST_BASE_CORES,
    QUEST_BASE_XP,
    QUEST_HIGH_SCORE_BONUS_CORES,
    QUEST_HIGH_SCORE_THRESHOLD,
    ApplicationVerificationPayload,
    ApplicationVerificationResponse,
    QuestVerificationPayload,
    QuestVerificationResponse,
    get_streak_multiplier,
    get_tier_config,
)

logger = logging.getLogger("reward_handler.verification")


# ---------------------------------------------------------------------------
# Daily Job Application Event Ingestion (RewardHandler.md Section 3.A)
# ---------------------------------------------------------------------------

async def ingest_application_verification(
    conn: Any,
    redis_client: Any,
    payload: ApplicationVerificationPayload,
) -> ApplicationVerificationResponse:
    """Ingest a verified job application event.

    1. Anti-Cheat Idempotency Check: Query verification_logs for confirmation_hash.
       If exists, abort with CONFLICT (HTTP 409).
    2. Record in verification_logs.
    3. Count verified apps completed today between 00:00:00 UTC and NOW().
    4. Grant 100 XP per application.
    5. First App of Day: Grant 50 base_bytes.
    6. Tier Target Reached: Grant tier_bonus_bytes * streak_multiplier.
    7. Sync XP to Redis.
    """
    user_id = str(payload.user_id)
    job_id = str(payload.job_id)
    confirmation_hash = str(payload.confirmation_hash).strip()
    verif_type = payload.verification_type

    # 1. Idempotency check
    existing = await conn.fetchval(
        "SELECT id FROM verification_logs WHERE confirmation_hash = $1",
        confirmation_hash,
    )
    if existing:
        return ApplicationVerificationResponse(
            status="CONFLICT",
            user_id=user_id,
            job_id=job_id,
            confirmation_hash=confirmation_hash,
            message=f"Duplicate confirmation hash '{confirmation_hash}' detected. Event aborted.",
        )

    # Ensure user exists in user_economy
    user_row = await get_or_create_user_economy(conn, user_id)
    daily_tier = user_row.get("daily_tier_target", 1)
    current_streak = user_row.get("current_streak", 0)
    tier_cfg = get_tier_config(daily_tier)

    # Calculate today's UTC start (00:00:00 UTC)
    now_utc = datetime.now(timezone.utc)
    today_start = datetime.combine(now_utc.date(), time.min, tzinfo=timezone.utc)

    async with conn.transaction():
        # 2. Log entry to verification_logs
        await conn.execute(
            """
            INSERT INTO verification_logs (
                user_id, job_id, confirmation_hash, verification_type, payload, verified_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            user_id,
            job_id,
            confirmation_hash,
            verif_type,
            json.dumps(payload.extra_payload),
            now_utc,
        )

        # 3. Count total apps completed today by user
        today_app_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM verification_logs
            WHERE user_id = $1
              AND verified_at >= $2
            """,
            user_id,
            today_start,
        )
        today_app_count = int(today_app_count or 1)

        # 4. XP Payout
        xp_gain = tier_cfg.get("xp_per_app", 100)

        # 5. Base Bytes (First App of the Day)
        bytes_gain = 0
        if today_app_count == 1:
            bytes_gain += tier_cfg.get("base_bytes", 50)

        # 6. Tier Target Reached Bonus
        tier_target_reached = (today_app_count == tier_cfg["target"])
        if tier_target_reached:
            mult = get_streak_multiplier(current_streak)
            bonus_bytes = int(tier_cfg["bonus_bytes"] * mult)
            bytes_gain += bonus_bytes

        # Atomic balance update in user_economy
        updated_row = await conn.fetchrow(
            """
            UPDATE user_economy
            SET xp_score = xp_score + $1,
                bytes = bytes + $2,
                last_active_date = $3,
                updated_at = NOW()
            WHERE user_id = $4
            RETURNING bytes, xp_score
            """,
            xp_gain,
            bytes_gain,
            now_utc.date(),
            user_id,
        )

    new_bytes = updated_row["bytes"] if updated_row else 0
    new_xp = updated_row["xp_score"] if updated_row else 0

    # 7. Redis Leaderboard Synchronization
    if redis_client and xp_gain > 0:
        await update_leaderboard(redis_client, user_id, xp_gain)

    return ApplicationVerificationResponse(
        status="SUCCESS",
        user_id=user_id,
        job_id=job_id,
        confirmation_hash=confirmation_hash,
        xp_gained=xp_gain,
        bytes_gained=bytes_gain,
        today_app_count=today_app_count,
        tier_target_reached=tier_target_reached,
        new_balance_bytes=new_bytes,
        new_balance_xp=new_xp,
        message="Application verified successfully.",
    )


# ---------------------------------------------------------------------------
# Architectural Quest Verified Event Ingestion (RewardHandler.md Section 3.B)
# ---------------------------------------------------------------------------

async def ingest_quest_verification(
    conn: Any,
    redis_client: Any,
    payload: QuestVerificationPayload,
) -> QuestVerificationResponse:
    """Ingest a verified architectural quest event.

    1. Check for duplicate quest completion.
    2. Base Quest completion: 500 XP, 100 Cores, 200 Bytes.
    3. High Quality Bonus: If score >= 90, award +50 Cores.
    4. Add architectural component to verified inventory.
    5. Sync XP to Redis.
    """
    user_id = str(payload.user_id)
    quest_id = str(payload.quest_id)
    component = str(payload.architectural_component).strip()
    score = max(0, min(100, int(payload.score)))

    # Ensure user exists in user_economy
    await get_or_create_user_economy(conn, user_id)

    # Check for duplicate completion
    existing = await conn.fetchval(
        "SELECT id FROM quest_completions WHERE user_id = $1 AND quest_id = $2",
        user_id,
        quest_id,
    )
    if existing:
        return QuestVerificationResponse(
            status="ALREADY_COMPLETED",
            user_id=user_id,
            quest_id=quest_id,
            architectural_component=component,
            score=score,
            message=f"Quest '{quest_id}' already completed by user '{user_id}'.",
        )

    # Rewards
    xp_gain = QUEST_BASE_XP
    cores_gain = QUEST_BASE_CORES
    bytes_gain = QUEST_BASE_BYTES
    high_quality_bonus = score >= QUEST_HIGH_SCORE_THRESHOLD
    if high_quality_bonus:
        cores_gain += QUEST_HIGH_SCORE_BONUS_CORES

    async with conn.transaction():
        # Record quest completion
        await conn.execute(
            """
            INSERT INTO quest_completions (
                user_id, quest_id, architectural_component, score,
                xp_awarded, bytes_awarded, cores_awarded
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            user_id,
            quest_id,
            component,
            score,
            xp_gain,
            bytes_gain,
            cores_gain,
        )

        # Append architectural component to user inventory
        await conn.execute(
            """
            UPDATE user_inventory
            SET verified_architectures = array_append(verified_architectures, $1),
                updated_at = NOW()
            WHERE user_id = $2 AND NOT ($1 = ANY(verified_architectures))
            """,
            component,
            user_id,
        )

        # Atomic balance update in user_economy
        updated_row = await conn.fetchrow(
            """
            UPDATE user_economy
            SET xp_score = xp_score + $1,
                cores = cores + $2,
                bytes = bytes + $3,
                updated_at = NOW()
            WHERE user_id = $4
            RETURNING xp_score, cores, bytes
            """,
            xp_gain,
            cores_gain,
            bytes_gain,
            user_id,
        )

    new_xp = updated_row["xp_score"] if updated_row else 0
    new_cores = updated_row["cores"] if updated_row else 0
    new_bytes = updated_row["bytes"] if updated_row else 0

    # Sync XP to Redis
    if redis_client and xp_gain > 0:
        await update_leaderboard(redis_client, user_id, xp_gain)

    return QuestVerificationResponse(
        status="SUCCESS",
        user_id=user_id,
        quest_id=quest_id,
        architectural_component=component,
        score=score,
        xp_gained=xp_gain,
        bytes_gained=bytes_gain,
        cores_gained=cores_gain,
        high_quality_bonus=high_quality_bonus,
        new_balance_xp=new_xp,
        new_balance_cores=new_cores,
        new_balance_bytes=new_bytes,
        message="Architectural quest verified and rewards issued.",
    )
