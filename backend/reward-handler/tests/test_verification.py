"""Offline unit tests for application and quest verification event ingestion."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from leaderboard import InMemoryRedisLeaderboard, KEY_ALL_TIME, KEY_WEEKLY
from schemas import (
    ApplicationVerificationPayload,
    QuestVerificationPayload,
)
from verification import (
    ingest_application_verification,
    ingest_quest_verification,
)


def make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    return conn


class TestApplicationVerificationIngestion(unittest.IsolatedAsyncioTestCase):
    """Test suite for daily job application verification events."""

    async def test_duplicate_confirmation_hash_conflict(self):
        """Duplicate confirmation hash returns CONFLICT (409) and aborts processing."""
        conn = make_mock_conn()
        conn.fetchval.return_value = 123  # existing id found

        redis = InMemoryRedisLeaderboard()
        payload = ApplicationVerificationPayload(
            user_id="user_dup",
            job_id="job_1",
            confirmation_hash="duplicate_hash_xyz",
            verification_type="EMAIL",
        )
        res = await ingest_application_verification(conn, redis, payload)
        self.assertEqual(res.status, "CONFLICT")
        self.assertEqual(res.xp_gained, 0)
        self.assertEqual(res.bytes_gained, 0)

    async def test_first_app_tier1_reached_rewards(self):
        """First application for Tier 1 (Casual, target 1) awards base + bonus + 100 XP."""
        conn = make_mock_conn()
        redis = InMemoryRedisLeaderboard()

        # 1. fetchval for confirmation_hash check -> None (not duplicate)
        # 2. count of apps today -> 1
        conn.fetchval.side_effect = [None, 1]

        # fetchrow for user_economy check & update
        conn.fetchrow.side_effect = [
            # get_or_create_user_economy
            {
                "user_id": "u1",
                "bytes": 0,
                "cores": 0,
                "xp_score": 0,
                "current_streak": 0,
                "active_streak_freezes": 0,
                "daily_tier_target": 1,
                "last_rollover_date": None,
                "created_at": datetime.now(timezone.utc),
            },
            # UPDATE RETURNING
            {"bytes": 75, "xp_score": 100},
        ]

        payload = ApplicationVerificationPayload(
            user_id="u1",
            job_id="job_t1",
            confirmation_hash="fresh_hash_1",
            verification_type="VISION",
        )
        res = await ingest_application_verification(conn, redis, payload)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.xp_gained, 100)
        # 50 base bytes + 25 bonus bytes = 75 bytes
        self.assertEqual(res.bytes_gained, 75)
        self.assertTrue(res.tier_target_reached)
        self.assertEqual(res.new_balance_bytes, 75)
        self.assertEqual(res.new_balance_xp, 100)

        # Confirm Redis synced
        self.assertEqual(await redis.zscore(KEY_ALL_TIME, "u1"), 100.0)
        self.assertEqual(await redis.zscore(KEY_WEEKLY, "u1"), 100.0)

    async def test_tier_target_with_streak_multiplier(self):
        """Third application on Tier 2 with a 14-day streak applies 1.5x multiplier to bonus bytes."""
        conn = make_mock_conn()
        redis = InMemoryRedisLeaderboard()

        # 1. check hash -> None, 2. count today -> 3
        conn.fetchval.side_effect = [None, 3]

        conn.fetchrow.side_effect = [
            # User on Tier 2 with streak 14
            {
                "user_id": "u_streaker",
                "bytes": 50,
                "cores": 0,
                "xp_score": 200,
                "current_streak": 14,
                "active_streak_freezes": 1,
                "daily_tier_target": 2,
                "last_rollover_date": None,
                "created_at": datetime.now(timezone.utc),
            },
            # UPDATE RETURNING (previous 50 + bonus 150 = 200)
            {"bytes": 200, "xp_score": 300},
        ]

        payload = ApplicationVerificationPayload(
            user_id="u_streaker",
            job_id="job_3",
            confirmation_hash="fresh_hash_3",
            verification_type="EMAIL",
        )
        res = await ingest_application_verification(conn, redis, payload)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.xp_gained, 100)
        # Tier 2 bonus is 100 bytes * 1.5 multiplier = 150 bytes
        self.assertEqual(res.bytes_gained, 150)
        self.assertTrue(res.tier_target_reached)


class TestQuestVerificationIngestion(unittest.IsolatedAsyncioTestCase):
    """Test suite for architectural quest verification events."""

    async def test_duplicate_quest_completion(self):
        """Duplicate quest verification for same user returns ALREADY_COMPLETED."""
        conn = make_mock_conn()
        # fetchrow for user_economy, fetchval for quest_completions check
        conn.fetchrow.return_value = {"user_id": "u_q"}
        conn.fetchval.return_value = 999  # already completed

        redis = InMemoryRedisLeaderboard()
        payload = QuestVerificationPayload(
            user_id="u_q",
            quest_id="quest_docker",
            architectural_component="Distributed Container Engine",
            score=95,
        )
        res = await ingest_quest_verification(conn, redis, payload)
        self.assertEqual(res.status, "ALREADY_COMPLETED")
        self.assertEqual(res.xp_gained, 0)

    async def test_standard_quest_completion(self):
        """Standard passing score (< 90) awards 500 XP, 100 Cores, 200 Bytes."""
        conn = make_mock_conn()
        redis = InMemoryRedisLeaderboard()

        conn.fetchrow.side_effect = [
            {"user_id": "u_std"},
            {"xp_score": 500, "cores": 100, "bytes": 200},
        ]
        conn.fetchval.return_value = None  # not completed

        payload = QuestVerificationPayload(
            user_id="u_std",
            quest_id="quest_proxy",
            architectural_component="Reverse Proxy",
            score=82,
        )
        res = await ingest_quest_verification(conn, redis, payload)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.xp_gained, 500)
        self.assertEqual(res.cores_gained, 100)
        self.assertEqual(res.bytes_gained, 200)
        self.assertFalse(res.high_quality_bonus)

        # Check Redis
        self.assertEqual(await redis.zscore(KEY_ALL_TIME, "u_std"), 500.0)

    async def test_high_quality_bonus_quest_completion(self):
        """Score >= 90 awards extra 50 Cores (total 150 Cores)."""
        conn = make_mock_conn()
        redis = InMemoryRedisLeaderboard()

        conn.fetchrow.side_effect = [
            {"user_id": "u_hq"},
            {"xp_score": 500, "cores": 150, "bytes": 200},
        ]
        conn.fetchval.return_value = None

        payload = QuestVerificationPayload(
            user_id="u_hq",
            quest_id="quest_raft",
            architectural_component="Raft Consensus",
            score=94,
        )
        res = await ingest_quest_verification(conn, redis, payload)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.cores_gained, 150)
        self.assertTrue(res.high_quality_bonus)


if __name__ == "__main__":
    unittest.main()
