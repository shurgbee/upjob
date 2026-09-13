"""Offline unit tests for public async entry points and CLI interface."""

import io
import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from leaderboard import InMemoryRedisLeaderboard
from reward_handler import (
    _build_parser,
    _cli_main,
    fetch_leaderboard,
    get_user_economy_status,
    redeem_item,
    set_user_daily_tier,
    verify_architectural_quest,
    verify_job_application,
)


def make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    return conn


class TestPublicEntryPoints(unittest.IsolatedAsyncioTestCase):
    """Test suite for public async entry point functions."""

    async def test_get_user_economy_status(self):
        conn = make_mock_conn()
        # Mock user fetchrow for get_or_create_user_economy
        conn.fetchrow.return_value = {
            "user_id": "u_me",
            "bytes": 450,
            "cores": 120,
            "xp_score": 3200,
            "current_streak": 8,
            "active_streak_freezes": 1,
            "daily_tier_target": 2,
            "last_rollover_date": date.today(),
            "created_at": datetime.now(timezone.utc),
        }
        # Mock count of today's apps
        conn.fetchval.return_value = 2

        redis = InMemoryRedisLeaderboard()
        await redis.zincrby("leaderboard:all_time", 3200, "u_me")
        await redis.zincrby("leaderboard:weekly", 1200, "u_me")

        res = await get_user_economy_status("u_me", conn=conn, redis_client=redis)

        self.assertEqual(res["user_id"], "u_me")
        self.assertEqual(res["bytes"], 450)
        self.assertEqual(res["cores"], 120)
        self.assertEqual(res["xp_score"], 3200)
        self.assertEqual(res["current_streak"], 8)
        self.assertEqual(res["active_streak_freezes"], 1)
        self.assertEqual(res["daily_tier"]["tier_id"], 2)
        self.assertEqual(res["daily_tier"]["name"], "Hustler")
        self.assertEqual(res["daily_tier"]["target"], 3)
        self.assertEqual(res["daily_tier"]["completed_today"], 2)
        self.assertEqual(res["streak_multiplier"], 1.25)
        self.assertEqual(res["rankings"]["all_time_rank"], 1)
        self.assertEqual(res["rankings"]["weekly_rank"], 1)

    async def test_set_user_daily_tier_entry_point(self):
        conn = make_mock_conn()
        conn.fetchrow.return_value = {"user_id": "u_tier"}

        res = await set_user_daily_tier("u_tier", 3, conn=conn)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["new_tier"], 3)
        self.assertEqual(res["daily_target"], 5)
        self.assertEqual(res["tier_name"], "Grinder")

    async def test_redeem_item_entry_point(self):
        conn = make_mock_conn()
        conn.fetchrow.side_effect = [
            # SELECT FOR UPDATE
            {"bytes": 500, "cores": 0, "active_streak_freezes": 0},
            # UPDATE RETURNING
            {"bytes": 0, "cores": 0, "active_streak_freezes": 0},
        ]
        res = await redeem_item("u_cosmetic", "cosmetic_border_distributed", conn=conn)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["unlocked"], "cosmetic_border_distributed")

    async def test_fetch_leaderboard_entry_point(self):
        redis = InMemoryRedisLeaderboard()
        await redis.zincrby("leaderboard:weekly", 1400, "top_dog")

        res = await fetch_leaderboard(
            leaderboard_type="weekly",
            limit=5,
            current_user_id="top_dog",
            redis_client=redis,
        )
        self.assertEqual(res["leaderboard_type"], "weekly")
        self.assertEqual(len(res["top_users"]), 1)
        self.assertEqual(res["top_users"][0]["user_id"], "top_dog")
        self.assertEqual(res["top_users"][0]["xp"], 1400)
        self.assertEqual(res["current_user"]["rank"], 1)

    async def test_verify_job_application_entry_point(self):
        conn = make_mock_conn()
        redis = InMemoryRedisLeaderboard()
        conn.fetchval.side_effect = [None, 1]  # no duplicate, count today = 1
        conn.fetchrow.side_effect = [
            {"user_id": "u_app", "daily_tier_target": 1, "current_streak": 0},
            {"bytes": 75, "xp_score": 100},
        ]

        payload = {
            "user_id": "u_app",
            "job_id": "job_99",
            "confirmation_hash": "hash_entry_1",
            "verification_type": "EMAIL",
        }
        res = await verify_job_application(payload, conn=conn, redis_client=redis)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["xp_gained"], 100)

    async def test_verify_architectural_quest_entry_point(self):
        conn = make_mock_conn()
        redis = InMemoryRedisLeaderboard()
        conn.fetchrow.side_effect = [
            {"user_id": "u_quest"},
            {"xp_score": 500, "cores": 150, "bytes": 200},
        ]
        conn.fetchval.return_value = None  # not duplicate

        payload = {
            "user_id": "u_quest",
            "quest_id": "quest_1",
            "architectural_component": "ETL Pipeline",
            "score": 95,
        }
        res = await verify_architectural_quest(payload, conn=conn, redis_client=redis)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["cores_gained"], 150)
        self.assertTrue(res["high_quality_bonus"])


class TestCLIParser(unittest.TestCase):
    """Test suite for CLI argument parser."""

    def test_parser_status(self):
        parser = _build_parser()
        args = parser.parse_args(["status", "user-123"])
        self.assertEqual(args.subcommand, "status")
        self.assertEqual(args.user_id, "user-123")

    def test_parser_set_tier(self):
        parser = _build_parser()
        args = parser.parse_args(["set-tier", "user-123", "2"])
        self.assertEqual(args.subcommand, "set-tier")
        self.assertEqual(args.user_id, "user-123")
        self.assertEqual(args.tier_id, 2)

    def test_parser_redeem(self):
        parser = _build_parser()
        args = parser.parse_args(["redeem", "user-123", "streak_freeze"])
        self.assertEqual(args.subcommand, "redeem")
        self.assertEqual(args.item_id, "streak_freeze")

    def test_parser_leaderboard(self):
        parser = _build_parser()
        args = parser.parse_args(["leaderboard", "--type", "all_time", "--limit", "20", "--user-id", "u1"])
        self.assertEqual(args.subcommand, "leaderboard")
        self.assertEqual(args.type, "all_time")
        self.assertEqual(args.limit, 20)
        self.assertEqual(args.user_id, "u1")

    def test_parser_verify_app(self):
        parser = _build_parser()
        args = parser.parse_args(["verify-app", "u1", "j1", "hash123", "--type", "VISION"])
        self.assertEqual(args.subcommand, "verify-app")
        self.assertEqual(args.user_id, "u1")
        self.assertEqual(args.confirmation_hash, "hash123")
        self.assertEqual(args.type, "VISION")


if __name__ == "__main__":
    unittest.main()
