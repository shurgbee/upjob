"""Offline unit tests for reward-handler economy engine and state transitions."""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from economy import (
    calculate_day_bounds_utc,
    compute_rollover_transition,
    count_user_apps_in_range,
    ensure_schema,
    get_or_create_user_economy,
    get_user_inventory,
    process_daily_rollover,
    process_user_rollover,
    redeem_shop_item,
    set_user_tier,
)


class TestPureRolloverTransitions(unittest.TestCase):
    """Tests for compute_rollover_transition pure logic."""

    def test_target_met_advances_streak(self):
        """When applications >= daily_target, streak increments by 1."""
        new_streak, freezes, preserved, milestone = compute_rollover_transition(
            current_streak=3,
            active_freezes=1,
            apps_completed=3,
            daily_target=3,
        )
        self.assertEqual(new_streak, 4)
        self.assertEqual(freezes, 1)
        self.assertTrue(preserved)
        self.assertIsNone(milestone)

    def test_target_met_exceeding_apps(self):
        """Overachieving daily target still increments streak normally."""
        new_streak, freezes, preserved, milestone = compute_rollover_transition(
            current_streak=5,
            active_freezes=2,
            apps_completed=7,
            daily_target=3,
        )
        self.assertEqual(new_streak, 6)
        self.assertEqual(freezes, 2)
        self.assertTrue(preserved)

    def test_target_met_triggers_day_7_milestone(self):
        """Crossing 7-day streak awards 50 Cores and streak_flame_bronze badge."""
        new_streak, freezes, preserved, milestone = compute_rollover_transition(
            current_streak=6,
            active_freezes=0,
            apps_completed=1,
            daily_target=1,
        )
        self.assertEqual(new_streak, 7)
        self.assertTrue(preserved)
        self.assertIsNotNone(milestone)
        self.assertEqual(milestone["cores"], 50)
        self.assertEqual(milestone["badge_id"], "streak_flame_bronze")

    def test_target_met_triggers_day_30_milestone(self):
        """Crossing 30-day streak awards 250 Cores and streak_flame_gold badge."""
        new_streak, freezes, preserved, milestone = compute_rollover_transition(
            current_streak=29,
            active_freezes=1,
            apps_completed=5,
            daily_target=5,
        )
        self.assertEqual(new_streak, 30)
        self.assertTrue(preserved)
        self.assertIsNotNone(milestone)
        self.assertEqual(milestone["cores"], 250)
        self.assertEqual(milestone["badge_id"], "streak_flame_gold")

    def test_target_missed_uses_streak_freeze(self):
        """When target is missed and freezes > 0, streak is preserved and 1 freeze is consumed."""
        new_streak, freezes, preserved, milestone = compute_rollover_transition(
            current_streak=8,
            active_freezes=2,
            apps_completed=1,
            daily_target=3,
        )
        self.assertEqual(new_streak, 8)  # preserved
        self.assertEqual(freezes, 1)     # 2 - 1
        self.assertTrue(preserved)
        self.assertIsNone(milestone)

    def test_target_missed_zero_freezes_resets_streak(self):
        """When target is missed and freezes == 0, streak resets to 0."""
        new_streak, freezes, preserved, milestone = compute_rollover_transition(
            current_streak=8,
            active_freezes=0,
            apps_completed=0,
            daily_target=1,
        )
        self.assertEqual(new_streak, 0)
        self.assertEqual(freezes, 0)
        self.assertFalse(preserved)
        self.assertIsNone(milestone)


class TestDayBounds(unittest.TestCase):
    """Tests for calculate_day_bounds_utc."""

    def test_day_bounds_utc(self):
        d = date(2026, 9, 12)
        start, end = calculate_day_bounds_utc(d)
        self.assertEqual(start, datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 13, 0, 0, 0, tzinfo=timezone.utc))


def make_mock_conn() -> AsyncMock:
    """Helper to create an AsyncMock connection with asyncpg transaction support."""
    conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    return conn


class TestAsyncEconomyTransactions(unittest.IsolatedAsyncioTestCase):
    """Offline unit tests mocking asyncpg connection operations."""

    async def test_ensure_schema_executes_sql(self):
        """ensure_schema executes migration SQL against connection."""
        mock_conn = make_mock_conn()
        await ensure_schema(mock_conn)
        mock_conn.execute.assert_called_once()
        sql_arg = mock_conn.execute.call_args[0][0]
        self.assertIn("CREATE TABLE IF NOT EXISTS user_economy", sql_arg)

    async def test_get_or_create_user_economy_existing(self):
        """When user already exists, returns existing row without inserting."""
        mock_conn = make_mock_conn()
        mock_conn.fetchrow.return_value = {
            "user_id": "u1",
            "bytes": 200,
            "cores": 50,
            "xp_score": 1000,
            "current_streak": 5,
            "active_streak_freezes": 1,
            "daily_tier_target": 2,
            "last_active_date": None,
            "last_rollover_date": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        res = await get_or_create_user_economy(mock_conn, "u1")
        self.assertEqual(res["user_id"], "u1")
        self.assertEqual(res["bytes"], 200)
        # Should not have called execute (INSERT) since user was found
        mock_conn.execute.assert_not_called()

    async def test_get_or_create_user_economy_new_user(self):
        """When user does not exist, inserts defaults and returns row."""
        mock_conn = make_mock_conn()
        # First fetchrow returns None, second fetchrow returns the newly created row
        mock_conn.fetchrow.side_effect = [
            None,
            {
                "user_id": "u2",
                "bytes": 0,
                "cores": 0,
                "xp_score": 0,
                "current_streak": 0,
                "active_streak_freezes": 0,
                "daily_tier_target": 1,
                "last_active_date": None,
                "last_rollover_date": date.today(),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        ]
        res = await get_or_create_user_economy(mock_conn, "u2")
        self.assertEqual(res["user_id"], "u2")
        self.assertEqual(mock_conn.execute.call_count, 2)  # insert economy + inventory

    async def test_set_user_tier(self):
        """Updating tier to valid id updates DB, invalid id returns error."""
        mock_conn = make_mock_conn()
        mock_conn.fetchrow.return_value = {"user_id": "u1"}

        # Valid tier 2
        res = await set_user_tier(mock_conn, "u1", 2)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.new_tier, 2)
        self.assertEqual(res.tier_name, "Hustler")
        self.assertEqual(res.daily_target, 3)

        # Invalid tier 4
        res_invalid = await set_user_tier(mock_conn, "u1", 4)
        self.assertEqual(res_invalid.status, "ERROR")

    async def test_redeem_unknown_item(self):
        """Redeeming an item not in catalog returns ITEM_NOT_FOUND."""
        mock_conn = make_mock_conn()
        res = await redeem_shop_item(mock_conn, "u1", "unknown_magic_sword")
        self.assertEqual(res.status, "ITEM_NOT_FOUND")

    async def test_redeem_insufficient_funds(self):
        """Redeeming when balances are too low returns INSUFFICIENT_FUNDS."""
        mock_conn = make_mock_conn()
        # User has 100 bytes, item costs 150 bytes
        mock_conn.fetchrow.return_value = {
            "bytes": 100,
            "cores": 0,
            "active_streak_freezes": 0,
        }
        res = await redeem_shop_item(mock_conn, "u1", "streak_freeze")
        self.assertEqual(res.status, "INSUFFICIENT_FUNDS")
        self.assertEqual(res.remaining_bytes, 100)

    async def test_redeem_streak_freeze_success(self):
        """Redeeming streak_freeze debits 150 bytes and adds freeze."""
        mock_conn = make_mock_conn()
        mock_conn.fetchrow.side_effect = [
            # SELECT FOR UPDATE
            {"bytes": 300, "cores": 0, "active_streak_freezes": 0},
            # UPDATE RETURNING
            {"bytes": 150, "cores": 0, "active_streak_freezes": 1},
        ]
        res = await redeem_shop_item(mock_conn, "u1", "streak_freeze")
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.remaining_bytes, 150)
        self.assertEqual(res.unlocked, "streak_freeze")

    async def test_redeem_cosmetic_success(self):
        """Redeeming cosmetic adds to cosmetics array in user_inventory."""
        mock_conn = make_mock_conn()
        mock_conn.fetchrow.side_effect = [
            {"bytes": 500, "cores": 0, "active_streak_freezes": 0},
            {"bytes": 0, "cores": 0, "active_streak_freezes": 0},
        ]
        res = await redeem_shop_item(mock_conn, "u1", "cosmetic_border_distributed")
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.remaining_bytes, 0)
        self.assertEqual(res.unlocked, "cosmetic_border_distributed")
        mock_conn.execute.assert_called_once()
        self.assertIn("cosmetics = array_append", mock_conn.execute.call_args[0][0])

    async def test_redeem_feature_unlock_success(self):
        """Redeeming feature unlock debits cores and sets unlocked_features flag."""
        mock_conn = make_mock_conn()
        mock_conn.fetchrow.side_effect = [
            {"bytes": 0, "cores": 100, "active_streak_freezes": 0},
            {"bytes": 0, "cores": 0, "active_streak_freezes": 0},
        ]
        res = await redeem_shop_item(mock_conn, "u1", "unlock_outreach_drafts")
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.remaining_cores, 0)
        self.assertEqual(res.unlocked, "unlock_outreach_drafts")
        mock_conn.execute.assert_called_once()
        self.assertIn("unlocked_features = jsonb_set", mock_conn.execute.call_args[0][0])

    async def test_process_user_rollover_with_milestone(self):
        """Rollover advancing to day 7 persists milestone badge and bonus cores."""
        mock_conn = make_mock_conn()
        # Count 1 app on target date
        mock_conn.fetchval.return_value = 1

        user_row = {
            "user_id": "u_milestone",
            "current_streak": 6,
            "active_streak_freezes": 0,
            "daily_tier_target": 1,
        }
        res = await process_user_rollover(mock_conn, user_row, date(2026, 9, 11))
        self.assertEqual(res.new_streak, 7)
        self.assertTrue(res.streak_preserved)
        self.assertIsNotNone(res.milestone_awarded)
        self.assertEqual(res.milestone_awarded["badge_id"], "streak_flame_bronze")
        self.assertEqual(res.milestone_awarded["cores"], 50)
        # Verify 2 updates: inventory (badge) + economy (streak & cores)
        self.assertEqual(mock_conn.execute.call_count, 2)


if __name__ == "__main__":
    unittest.main()
