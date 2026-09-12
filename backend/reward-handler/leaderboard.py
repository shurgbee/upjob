"""Redis leaderboard synchronization, ranking queries, and in-memory fallback.

Implements real-time XP synchronization across leaderboard:all_time and
leaderboard:weekly, weekly reset snapshots, and an in-memory sorted-set fallback
for local development without a live Redis instance.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Bootstrap backend/ onto sys.path
_BACKEND_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from schemas import (  # noqa: E402
    LeaderboardResponse,
    LeaderboardUserEntry,
    UserRankings,
)

logger = logging.getLogger("reward_handler.leaderboard")

KEY_ALL_TIME = "leaderboard:all_time"
KEY_WEEKLY = "leaderboard:weekly"


# ---------------------------------------------------------------------------
# In-Memory Sorted-Set Fallback for Local Dev & Tests
# ---------------------------------------------------------------------------

class InMemoryPipeline:
    """Mock Redis async pipeline executing zincrby commands in memory."""

    def __init__(self, in_memory_redis: InMemoryRedisLeaderboard) -> None:
        self._redis = in_memory_redis
        self._commands: list[tuple[str, tuple, dict]] = []

    def zincrby(self, name: str, amount: float, value: str) -> InMemoryPipeline:
        self._commands.append(("zincrby", (name, amount, value), {}))
        return self

    async def execute(self) -> list[Any]:
        results = []
        for cmd, args, kwargs in self._commands:
            if cmd == "zincrby":
                res = await self._redis.zincrby(*args, **kwargs)
                results.append(res)
        self._commands.clear()
        return results


class InMemoryRedisLeaderboard:
    """Pure-Python in-memory implementation of Redis Sorted Sets for local dev/testing."""

    def __init__(self) -> None:
        # dict of key -> dict of member (str) -> score (float)
        self._stores: dict[str, dict[str, float]] = {
            KEY_ALL_TIME: {},
            KEY_WEEKLY: {},
        }

    def pipeline(self) -> InMemoryPipeline:
        return InMemoryPipeline(self)

    async def zincrby(self, name: str, amount: float, value: str) -> float:
        if name not in self._stores:
            self._stores[name] = {}
        val_str = str(value)
        current = self._stores[name].get(val_str, 0.0)
        new_score = current + float(amount)
        self._stores[name][val_str] = new_score
        return new_score

    async def zscore(self, name: str, value: str) -> float | None:
        val_str = str(value)
        return self._stores.get(name, {}).get(val_str)

    async def zrevrank(self, name: str, value: str) -> int | None:
        """0-indexed rank from highest to lowest score, or None if member not present."""
        store = self._stores.get(name, {})
        val_str = str(value)
        if val_str not in store:
            return None
        # Sort descending by score; break ties deterministically by member name
        sorted_members = sorted(
            store.items(), key=lambda item: (-item[1], item[0])
        )
        for idx, (member, _) in enumerate(sorted_members):
            if member == val_str:
                return idx
        return None

    async def zrevrange(
        self, name: str, start: int, end: int, withscores: bool = False
    ) -> list[Any]:
        """Return range of members from highest to lowest score.

        In Redis, 'end' is inclusive. E.g. zrevrange(name, 0, 49) returns up to 50 items.
        Negative indexing: -1 means end of set.
        """
        store = self._stores.get(name, {})
        sorted_members = sorted(
            store.items(), key=lambda item: (-item[1], item[0])
        )
        if not sorted_members:
            return []

        total = len(sorted_members)
        # Normalize python slice indices
        s = start if start >= 0 else max(total + start, 0)
        e = (end + 1) if end >= 0 else (total + end + 1)
        sliced = sorted_members[s:e]

        if withscores:
            # Redis returns list of (member, score) tuples when withscores=True
            return [(m, score) for m, score in sliced]
        return [m for m, _ in sliced]

    async def delete(self, *names: str) -> int:
        count = 0
        for name in names:
            if name in self._stores:
                self._stores[name].clear()
                count += 1
        return count


# Singleton instance for in-memory fallback
_GLOBAL_IN_MEMORY_LEADERBOARD = InMemoryRedisLeaderboard()


def get_redis_client(redis_url: str | None = None) -> Any:
    """Return an async Redis client, or fallback to InMemoryRedisLeaderboard.

    If redis_url is None, checks REDIS_URL from environment. If still unset,
    returns the in-memory fallback instance.
    """
    url = redis_url or os.getenv("REDIS_URL")
    if not url:
        return _GLOBAL_IN_MEMORY_LEADERBOARD

    try:
        import redis.asyncio as aioredis
        return aioredis.from_url(url, decode_responses=True)
    except ImportError:
        logger.warning("redis package not installed; falling back to InMemoryRedisLeaderboard.")
        return _GLOBAL_IN_MEMORY_LEADERBOARD


# ---------------------------------------------------------------------------
# Leaderboard Operations
# ---------------------------------------------------------------------------

async def update_leaderboard(redis_client: Any, user_id: str, xp_gained: int) -> None:
    """Update both All-Time and Weekly sprint leaderboards atomically via pipeline."""
    if xp_gained <= 0 or not redis_client:
        return
    try:
        pipe = redis_client.pipeline()
        pipe.zincrby(KEY_ALL_TIME, xp_gained, str(user_id))
        pipe.zincrby(KEY_WEEKLY, xp_gained, str(user_id))
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to update Redis leaderboards: %s", exc)


async def get_user_rankings(redis_client: Any, user_id: str) -> UserRankings:
    """Query user's current 1-based rank on All-Time and Weekly leaderboards."""
    if not redis_client:
        return UserRankings()

    try:
        all_time_rank_0 = await redis_client.zrevrank(KEY_ALL_TIME, str(user_id))
        weekly_rank_0 = await redis_client.zrevrank(KEY_WEEKLY, str(user_id))

        all_time_rank = (all_time_rank_0 + 1) if all_time_rank_0 is not None else None
        weekly_rank = (weekly_rank_0 + 1) if weekly_rank_0 is not None else None

        return UserRankings(
            all_time_rank=all_time_rank,
            weekly_rank=weekly_rank,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch user rankings from Redis: %s", exc)
        return UserRankings()


async def get_leaderboard(
    redis_client: Any,
    leaderboard_type: str = "weekly",
    limit: int = 50,
    current_user_id: str | None = None,
    user_flair_map: dict[str, str] | None = None,
) -> LeaderboardResponse:
    """Fetch top users from sorted set along with current user's standing.

    Internal call: ZREVRANGE <key> 0 (limit - 1) WITHSCORES
    """
    key = KEY_ALL_TIME if leaderboard_type == "all_time" else KEY_WEEKLY
    flairs = user_flair_map or {}

    top_users: list[LeaderboardUserEntry] = []
    current_user_info: dict[str, Any] = {"rank": None, "xp": None}

    if redis_client:
        try:
            # Fetch top range
            raw_top = await redis_client.zrevrange(key, 0, max(0, limit - 1), withscores=True)
            for idx, item in enumerate(raw_top):
                # item is (member, score)
                member_id = str(item[0])
                score = int(float(item[1]))
                top_users.append(
                    LeaderboardUserEntry(
                        rank=idx + 1,
                        user_id=member_id,
                        xp=score,
                        flair=flairs.get(member_id),
                    )
                )

            # Fetch current user rank & score if requested
            if current_user_id:
                rank_0 = await redis_client.zrevrank(key, str(current_user_id))
                score = await redis_client.zscore(key, str(current_user_id))
                if rank_0 is not None and score is not None:
                    current_user_info = {
                        "rank": rank_0 + 1,
                        "xp": int(float(score)),
                    }
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to fetch leaderboard from Redis: %s", exc)

    return LeaderboardResponse(
        leaderboard_type=leaderboard_type,
        top_users=top_users,
        current_user=current_user_info,
    )


async def reset_weekly_leaderboard(
    redis_client: Any,
    conn: Any,
    week_start: date | None = None,
) -> dict[str, Any]:
    """Execute Monday 00:00:00 UTC weekly reset.

    1. Snapshot top 10 from leaderboard:weekly to PostgreSQL weekly_leaderboard_snapshots.
    2. Execute DEL leaderboard:weekly in Redis.
    """
    if week_start is None:
        # Most recent Monday
        today = datetime.now(timezone.utc).date()
        week_start = today - timedelta(days=today.weekday())

    snapshotted_count = 0
    if redis_client:
        top_10 = await redis_client.zrevrange(KEY_WEEKLY, 0, 9, withscores=True)
        if top_10 and conn:
            for rank_0, (user_id, xp_val) in enumerate(top_10):
                rank = rank_0 + 1
                xp = int(float(xp_val))
                await conn.execute(
                    """
                    INSERT INTO weekly_leaderboard_snapshots (week_start, rank, user_id, xp)
                    VALUES ($1, $2, $3, $4)
                    """,
                    week_start,
                    rank,
                    str(user_id),
                    xp,
                )
                snapshotted_count += 1

        # Reset weekly leaderboard in Redis
        await redis_client.delete(KEY_WEEKLY)

    return {
        "status": "SUCCESS",
        "week_start": week_start.isoformat(),
        "snapshotted_count": snapshotted_count,
        "message": f"Weekly leaderboard reset. Snapshotted {snapshotted_count} users for week of {week_start}.",
    }
