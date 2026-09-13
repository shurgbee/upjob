"""Offline unit tests for reward-handler Redis leaderboard and in-memory fallback."""

import unittest
from datetime import date
from unittest.mock import AsyncMock

from leaderboard import (
    KEY_ALL_TIME,
    KEY_WEEKLY,
    InMemoryRedisLeaderboard,
    get_leaderboard,
    get_redis_client,
    get_user_rankings,
    reset_weekly_leaderboard,
    update_leaderboard,
)


class TestInMemoryRedisLeaderboard(unittest.IsolatedAsyncioTestCase):
    """Test suite for pure-Python in-memory Redis sorted set emulation."""

    async def test_zincrby_and_zscore(self):
        redis = InMemoryRedisLeaderboard()
        score1 = await redis.zincrby(KEY_ALL_TIME, 100, "user_1")
        self.assertEqual(score1, 100.0)

        score2 = await redis.zincrby(KEY_ALL_TIME, 50, "user_1")
        self.assertEqual(score2, 150.0)

        stored = await redis.zscore(KEY_ALL_TIME, "user_1")
        self.assertEqual(stored, 150.0)

        none_val = await redis.zscore(KEY_ALL_TIME, "nonexistent")
        self.assertIsNone(none_val)

    async def test_zrevrank_order(self):
        redis = InMemoryRedisLeaderboard()
        await redis.zincrby(KEY_ALL_TIME, 100, "user_low")
        await redis.zincrby(KEY_ALL_TIME, 500, "user_high")
        await redis.zincrby(KEY_ALL_TIME, 250, "user_mid")

        rank_high = await redis.zrevrank(KEY_ALL_TIME, "user_high")
        rank_mid = await redis.zrevrank(KEY_ALL_TIME, "user_mid")
        rank_low = await redis.zrevrank(KEY_ALL_TIME, "user_low")
        rank_missing = await redis.zrevrank(KEY_ALL_TIME, "user_missing")

        self.assertEqual(rank_high, 0)
        self.assertEqual(rank_mid, 1)
        self.assertEqual(rank_low, 2)
        self.assertIsNone(rank_missing)

    async def test_zrevrange_with_scores(self):
        redis = InMemoryRedisLeaderboard()
        await redis.zincrby(KEY_WEEKLY, 200, "u1")
        await redis.zincrby(KEY_WEEKLY, 500, "u2")
        await redis.zincrby(KEY_WEEKLY, 300, "u3")

        # Top 2
        top_2 = await redis.zrevrange(KEY_WEEKLY, 0, 1, withscores=True)
        self.assertEqual(len(top_2), 2)
        self.assertEqual(top_2[0], ("u2", 500.0))
        self.assertEqual(top_2[1], ("u3", 300.0))

    async def test_pipeline_execution(self):
        redis = InMemoryRedisLeaderboard()
        pipe = redis.pipeline()
        pipe.zincrby(KEY_ALL_TIME, 100, "user_pipe")
        pipe.zincrby(KEY_WEEKLY, 100, "user_pipe")
        results = await pipe.execute()
        self.assertEqual(results, [100.0, 100.0])

        self.assertEqual(await redis.zscore(KEY_ALL_TIME, "user_pipe"), 100.0)
        self.assertEqual(await redis.zscore(KEY_WEEKLY, "user_pipe"), 100.0)

    async def test_delete(self):
        redis = InMemoryRedisLeaderboard()
        await redis.zincrby(KEY_WEEKLY, 50, "u1")
        self.assertEqual(await redis.zscore(KEY_WEEKLY, "u1"), 50.0)

        deleted = await redis.delete(KEY_WEEKLY)
        self.assertEqual(deleted, 1)
        self.assertIsNone(await redis.zscore(KEY_WEEKLY, "u1"))


class TestLeaderboardOperations(unittest.IsolatedAsyncioTestCase):
    """Test suite for higher-level leaderboard operations using in-memory client."""

    async def test_update_leaderboard_both_sets(self):
        redis = InMemoryRedisLeaderboard()
        await update_leaderboard(redis, "user_test", 150)

        self.assertEqual(await redis.zscore(KEY_ALL_TIME, "user_test"), 150.0)
        self.assertEqual(await redis.zscore(KEY_WEEKLY, "user_test"), 150.0)

    async def test_get_user_rankings_1_based(self):
        redis = InMemoryRedisLeaderboard()
        await redis.zincrby(KEY_ALL_TIME, 1000, "leader")
        await redis.zincrby(KEY_ALL_TIME, 500, "runner_up")

        await redis.zincrby(KEY_WEEKLY, 200, "leader")
        await redis.zincrby(KEY_WEEKLY, 400, "runner_up")

        rankings_leader = await get_user_rankings(redis, "leader")
        self.assertEqual(rankings_leader.all_time_rank, 1)
        self.assertEqual(rankings_leader.weekly_rank, 2)

        rankings_runner = await get_user_rankings(redis, "runner_up")
        self.assertEqual(rankings_runner.all_time_rank, 2)
        self.assertEqual(rankings_runner.weekly_rank, 1)

    async def test_get_leaderboard_payload_structure(self):
        redis = InMemoryRedisLeaderboard()
        await redis.zincrby(KEY_WEEKLY, 1400, "uuid1")
        await redis.zincrby(KEY_WEEKLY, 1250, "uuid2")
        await redis.zincrby(KEY_WEEKLY, 800, "uuid3")

        flair_map = {"uuid1": "CI/CD Demon", "uuid2": "Async Wizard"}

        resp = await get_leaderboard(
            redis,
            leaderboard_type="weekly",
            limit=2,
            current_user_id="uuid3",
            user_flair_map=flair_map,
        )
        data = resp.to_dict()

        self.assertEqual(data["leaderboard_type"], "weekly")
        self.assertEqual(len(data["top_users"]), 2)
        self.assertEqual(data["top_users"][0]["rank"], 1)
        self.assertEqual(data["top_users"][0]["user_id"], "uuid1")
        self.assertEqual(data["top_users"][0]["xp"], 1400)
        self.assertEqual(data["top_users"][0]["flair"], "CI/CD Demon")

        self.assertEqual(data["top_users"][1]["rank"], 2)
        self.assertEqual(data["top_users"][1]["flair"], "Async Wizard")

        self.assertEqual(data["current_user"]["rank"], 3)
        self.assertEqual(data["current_user"]["xp"], 800)

    async def test_reset_weekly_leaderboard_snapshots(self):
        redis = InMemoryRedisLeaderboard()
        await redis.zincrby(KEY_WEEKLY, 2500, "top_1")
        await redis.zincrby(KEY_WEEKLY, 1800, "top_2")

        mock_conn = AsyncMock()
        result = await reset_weekly_leaderboard(redis, mock_conn, date(2026, 9, 8))

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["snapshotted_count"], 2)
        self.assertEqual(mock_conn.execute.call_count, 2)

        # Confirm weekly set was deleted in Redis
        self.assertIsNone(await redis.zscore(KEY_WEEKLY, "top_1"))


if __name__ == "__main__":
    unittest.main()
