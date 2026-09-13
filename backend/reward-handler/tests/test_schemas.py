"""Offline unit tests for reward-handler schemas and baseline constants."""

import unittest

from schemas import (
    APPLICATION_TIERS,
    MILESTONES,
    SHOP_CATALOG,
    STREAK_TIER_MULTIPLIERS,
    DailyTierInfo,
    EconomyMeResponse,
    LeaderboardResponse,
    LeaderboardUserEntry,
    RedeemResponse,
    SetTierResponse,
    UserRankings,
    get_streak_multiplier,
    get_tier_config,
    validate_tier_id,
)


class TestRewardHandlerSchemas(unittest.TestCase):
    """Test suite for reward engine constants, multipliers, and response shapes."""

    def test_application_tiers_constants(self):
        """Verify APPLICATION_TIERS matches spec Section 2."""
        self.assertEqual(len(APPLICATION_TIERS), 3)
        self.assertEqual(APPLICATION_TIERS[1]["name"], "Casual")
        self.assertEqual(APPLICATION_TIERS[1]["target"], 1)
        self.assertEqual(APPLICATION_TIERS[1]["base_bytes"], 50)
        self.assertEqual(APPLICATION_TIERS[1]["bonus_bytes"], 25)
        self.assertEqual(APPLICATION_TIERS[1]["xp_per_app"], 100)

        self.assertEqual(APPLICATION_TIERS[2]["name"], "Hustler")
        self.assertEqual(APPLICATION_TIERS[2]["target"], 3)
        self.assertEqual(APPLICATION_TIERS[2]["base_bytes"], 50)
        self.assertEqual(APPLICATION_TIERS[2]["bonus_bytes"], 100)
        self.assertEqual(APPLICATION_TIERS[2]["xp_per_app"], 100)

        self.assertEqual(APPLICATION_TIERS[3]["name"], "Grinder")
        self.assertEqual(APPLICATION_TIERS[3]["target"], 5)
        self.assertEqual(APPLICATION_TIERS[3]["base_bytes"], 50)
        self.assertEqual(APPLICATION_TIERS[3]["bonus_bytes"], 250)
        self.assertEqual(APPLICATION_TIERS[3]["xp_per_app"], 100)

    def test_streak_tier_multipliers(self):
        """Verify STREAK_TIER_MULTIPLIERS mapping and get_streak_multiplier helper."""
        self.assertEqual(get_streak_multiplier(0), 1.0)
        self.assertEqual(get_streak_multiplier(1), 1.0)
        self.assertEqual(get_streak_multiplier(6), 1.0)
        self.assertEqual(get_streak_multiplier(7), 1.25)
        self.assertEqual(get_streak_multiplier(13), 1.25)
        self.assertEqual(get_streak_multiplier(14), 1.5)
        self.assertEqual(get_streak_multiplier(29), 1.5)
        self.assertEqual(get_streak_multiplier(30), 2.0)
        self.assertEqual(get_streak_multiplier(100), 2.0)
        # Negative streak edge case
        self.assertEqual(get_streak_multiplier(-5), 1.0)

    def test_milestones(self):
        """Verify MILESTONES contains 7 and 30 day thresholds."""
        self.assertIn(7, MILESTONES)
        self.assertEqual(MILESTONES[7]["cores"], 50)
        self.assertEqual(MILESTONES[7]["badge_id"], "streak_flame_bronze")

        self.assertIn(30, MILESTONES)
        self.assertEqual(MILESTONES[30]["cores"], 250)
        self.assertEqual(MILESTONES[30]["badge_id"], "streak_flame_gold")

    def test_shop_catalog(self):
        """Verify SHOP_CATALOG entries and costs match spec Section 2."""
        self.assertEqual(SHOP_CATALOG["streak_freeze"]["cost_bytes"], 150)
        self.assertEqual(SHOP_CATALOG["streak_freeze"]["cost_cores"], 0)

        self.assertEqual(SHOP_CATALOG["cosmetic_border_distributed"]["cost_bytes"], 500)
        self.assertEqual(SHOP_CATALOG["cosmetic_border_distributed"]["cost_cores"], 0)

        self.assertEqual(SHOP_CATALOG["cosmetic_title_cicd_demon"]["cost_bytes"], 300)
        self.assertEqual(SHOP_CATALOG["cosmetic_title_cicd_demon"]["cost_cores"], 0)

        self.assertEqual(SHOP_CATALOG["unlock_outreach_drafts"]["cost_bytes"], 0)
        self.assertEqual(SHOP_CATALOG["unlock_outreach_drafts"]["cost_cores"], 100)

        self.assertEqual(SHOP_CATALOG["unlock_company_mock_loop"]["cost_bytes"], 0)
        self.assertEqual(SHOP_CATALOG["unlock_company_mock_loop"]["cost_cores"], 200)

        self.assertEqual(SHOP_CATALOG["unlock_ats_deep_scanner"]["cost_bytes"], 0)
        self.assertEqual(SHOP_CATALOG["unlock_ats_deep_scanner"]["cost_cores"], 150)

        self.assertEqual(SHOP_CATALOG["unlock_reach_job_deep_scan"]["cost_bytes"], 0)
        self.assertEqual(SHOP_CATALOG["unlock_reach_job_deep_scan"]["cost_cores"], 300)

    def test_tier_helpers(self):
        """Verify get_tier_config and validate_tier_id."""
        self.assertTrue(validate_tier_id(1))
        self.assertTrue(validate_tier_id(2))
        self.assertTrue(validate_tier_id(3))
        self.assertFalse(validate_tier_id(0))
        self.assertFalse(validate_tier_id(4))

        self.assertEqual(get_tier_config(2)["name"], "Hustler")
        self.assertEqual(get_tier_config(99)["name"], "Casual")  # fallback to Tier 1

    def test_economy_me_response_serialization(self):
        """Verify EconomyMeResponse produces exact shape from spec Section 7.1."""
        dto = EconomyMeResponse(
            user_id="user-1234",
            bytes=450,
            cores=120,
            xp_score=3200,
            current_streak=8,
            active_streak_freezes=1,
            daily_tier=DailyTierInfo(
                tier_id=2,
                name="Hustler",
                target=3,
                completed_today=2,
            ),
            streak_multiplier=1.25,
            rankings=UserRankings(
                all_time_rank=14,
                weekly_rank=4,
            ),
        )
        data = dto.to_dict()
        self.assertEqual(data["user_id"], "user-1234")
        self.assertEqual(data["bytes"], 450)
        self.assertEqual(data["cores"], 120)
        self.assertEqual(data["xp_score"], 3200)
        self.assertEqual(data["current_streak"], 8)
        self.assertEqual(data["active_streak_freezes"], 1)
        self.assertEqual(data["daily_tier"]["tier_id"], 2)
        self.assertEqual(data["daily_tier"]["name"], "Hustler")
        self.assertEqual(data["daily_tier"]["target"], 3)
        self.assertEqual(data["daily_tier"]["completed_today"], 2)
        self.assertEqual(data["streak_multiplier"], 1.25)
        self.assertEqual(data["rankings"]["all_time_rank"], 14)
        self.assertEqual(data["rankings"]["weekly_rank"], 4)

    def test_other_responses_serialization(self):
        """Verify SetTierResponse, RedeemResponse, and LeaderboardResponse serialization."""
        tier_resp = SetTierResponse(
            status="SUCCESS",
            new_tier=2,
            tier_name="Hustler",
            daily_target=3,
        ).to_dict()
        self.assertEqual(tier_resp["status"], "SUCCESS")
        self.assertEqual(tier_resp["new_tier"], 2)

        redeem_resp = RedeemResponse(
            status="SUCCESS",
            remaining_bytes=300,
            remaining_cores=120,
            unlocked="streak_freeze",
        ).to_dict()
        self.assertEqual(redeem_resp["status"], "SUCCESS")
        self.assertEqual(redeem_resp["remaining_bytes"], 300)
        self.assertEqual(redeem_resp["unlocked"], "streak_freeze")

        lb_resp = LeaderboardResponse(
            leaderboard_type="weekly",
            top_users=[
                LeaderboardUserEntry(rank=1, user_id="u1", xp=1400, flair="CI/CD Demon"),
                LeaderboardUserEntry(rank=2, user_id="u2", xp=1250, flair="Async Wizard"),
            ],
            current_user={"rank": 4, "xp": 800},
        ).to_dict()
        self.assertEqual(lb_resp["leaderboard_type"], "weekly")
        self.assertEqual(len(lb_resp["top_users"]), 2)
        self.assertEqual(lb_resp["top_users"][0]["rank"], 1)
        self.assertEqual(lb_resp["top_users"][0]["flair"], "CI/CD Demon")
        self.assertEqual(lb_resp["current_user"]["rank"], 4)


if __name__ == "__main__":
    unittest.main()
