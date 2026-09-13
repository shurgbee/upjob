"""Schemas, baseline constants, and data contracts for the Reward Handler engine.

Follows the specifications in RewardHandler.md and conventions from CLAUDE.md.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Bootstrap backend/ onto sys.path so common can be imported cleanly
_BACKEND_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from common.models import DEFAULT_GEMINI_MODEL  # noqa: E402

# ---------------------------------------------------------------------------
# Tier Configurations & Reward Constants (RewardHandler.md Section 2)
# ---------------------------------------------------------------------------

#: Application Tiers: (tier_id, name, target_count, base_bytes, tier_bonus_bytes, base_xp)
APPLICATION_TIERS: dict[int, dict[str, Any]] = {
    1: {"name": "Casual",  "target": 1, "base_bytes": 50,  "bonus_bytes": 25,  "xp_per_app": 100},
    2: {"name": "Hustler", "target": 3, "base_bytes": 50,  "bonus_bytes": 100, "xp_per_app": 100},
    3: {"name": "Grinder", "target": 5, "base_bytes": 50,  "bonus_bytes": 250, "xp_per_app": 100},
}

#: Streak Multipliers: Multiplier applied to daily tier bonus bytes
STREAK_TIER_MULTIPLIERS: dict[int, float] = {
    0: 1.0,   # 1-6 days
    7: 1.25,  # 7-13 days
    14: 1.5,  # 14-29 days
    30: 2.0,  # 30+ days
}

#: Milestone Bonuses (One-time payouts upon crossing streak thresholds)
MILESTONES: dict[int, dict[str, Any]] = {
    7:  {"cores": 50,  "badge_id": "streak_flame_bronze"},
    30: {"cores": 250, "badge_id": "streak_flame_gold"},
}

#: Catalog of purchasable items and feature unlocks
SHOP_CATALOG: dict[str, dict[str, Any]] = {
    # Consumables (Soft Currency)
    "streak_freeze":               {"cost_bytes": 150, "cost_cores": 0,   "type": "consumable"},
    "cosmetic_border_distributed": {"cost_bytes": 500, "cost_cores": 0,   "type": "cosmetic"},
    "cosmetic_title_cicd_demon":   {"cost_bytes": 300, "cost_cores": 0,   "type": "cosmetic"},

    # Premium Functional Unlocks (Premium Currency)
    "unlock_outreach_drafts":      {"cost_bytes": 0,   "cost_cores": 100, "type": "feature"},
    "unlock_company_mock_loop":    {"cost_bytes": 0,   "cost_cores": 200, "type": "feature"},
    "unlock_ats_deep_scanner":     {"cost_bytes": 0,   "cost_cores": 150, "type": "feature"},
    "unlock_reach_job_deep_scan":  {"cost_bytes": 0,   "cost_cores": 300, "type": "feature"},
}

# Quest Reward Constants
QUEST_BASE_XP = 500
QUEST_BASE_CORES = 100
QUEST_BASE_BYTES = 200
QUEST_HIGH_SCORE_THRESHOLD = 90
QUEST_HIGH_SCORE_BONUS_CORES = 50

# Default subagent models
DEFAULT_SCOUT_MODEL = DEFAULT_GEMINI_MODEL
DEFAULT_EVALUATOR_MODEL = DEFAULT_GEMINI_MODEL


# ---------------------------------------------------------------------------
# Pure Helper Functions
# ---------------------------------------------------------------------------

def get_streak_multiplier(streak: int) -> float:
    """Calculate the streak tier multiplier based on consecutive active days.

    Thresholds:
    - 0-6 days: 1.0
    - 7-13 days: 1.25
    - 14-29 days: 1.5
    - 30+ days: 2.0
    """
    if streak < 0:
        return 1.0
    active_mult = 1.0
    for threshold in sorted(STREAK_TIER_MULTIPLIERS.keys()):
        if streak >= threshold:
            active_mult = STREAK_TIER_MULTIPLIERS[threshold]
        else:
            break
    return active_mult


def get_tier_config(tier_id: int) -> dict[str, Any]:
    """Retrieve tier configuration, defaulting to Tier 1 (Casual) if invalid."""
    return APPLICATION_TIERS.get(tier_id, APPLICATION_TIERS[1])


def validate_tier_id(tier_id: int) -> bool:
    """True if tier_id is 1, 2, or 3."""
    return tier_id in APPLICATION_TIERS


# ---------------------------------------------------------------------------
# Typed Data Contracts & Response DTOs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DailyTierInfo:
    tier_id: int
    name: str
    target: int
    completed_today: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UserRankings:
    all_time_rank: int | None = None
    weekly_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EconomyMeResponse:
    """Matches schema in RewardHandler.md Section 7.1."""
    user_id: str
    bytes: int
    cores: int
    xp_score: int
    current_streak: int
    active_streak_freezes: int
    daily_tier: DailyTierInfo
    streak_multiplier: float
    rankings: UserRankings

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "bytes": self.bytes,
            "cores": self.cores,
            "xp_score": self.xp_score,
            "current_streak": self.current_streak,
            "active_streak_freezes": self.active_streak_freezes,
            "daily_tier": self.daily_tier.to_dict(),
            "streak_multiplier": self.streak_multiplier,
            "rankings": self.rankings.to_dict(),
        }


@dataclass(frozen=True)
class ApplicationVerificationPayload:
    user_id: str
    job_id: str
    confirmation_hash: str
    verification_type: Literal["EMAIL", "VISION"]
    extra_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplicationVerificationResponse:
    status: Literal["SUCCESS", "CONFLICT", "ERROR"]
    user_id: str
    job_id: str
    confirmation_hash: str
    xp_gained: int = 0
    bytes_gained: int = 0
    today_app_count: int = 0
    tier_target_reached: bool = False
    new_balance_bytes: int = 0
    new_balance_xp: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QuestVerificationPayload:
    user_id: str
    quest_id: str
    architectural_component: str
    score: int
    repo_url: str | None = None


@dataclass(frozen=True)
class QuestVerificationResponse:
    status: Literal["SUCCESS", "ALREADY_COMPLETED", "ERROR"]
    user_id: str
    quest_id: str
    architectural_component: str
    score: int
    xp_gained: int = 0
    bytes_gained: int = 0
    cores_gained: int = 0
    high_quality_bonus: bool = False
    new_balance_xp: int = 0
    new_balance_cores: int = 0
    new_balance_bytes: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SetTierResponse:
    status: Literal["SUCCESS", "ERROR"]
    new_tier: int
    tier_name: str
    daily_target: int
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RedeemResponse:
    status: Literal["SUCCESS", "INSUFFICIENT_FUNDS", "ITEM_NOT_FOUND", "ERROR"]
    remaining_bytes: int
    remaining_cores: int
    unlocked: str
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LeaderboardUserEntry:
    rank: int
    user_id: str
    xp: int
    flair: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LeaderboardResponse:
    leaderboard_type: str
    top_users: list[LeaderboardUserEntry]
    current_user: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaderboard_type": self.leaderboard_type,
            "top_users": [u.to_dict() for u in self.top_users],
            "current_user": self.current_user or {"rank": None, "xp": None},
        }


@dataclass(frozen=True)
class RolloverUserResult:
    user_id: str
    previous_streak: int
    new_streak: int
    apps_completed: int
    target_required: int
    streak_preserved: bool
    freezes_remaining: int
    milestone_awarded: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RolloverBatchResponse:
    status: Literal["SUCCESS", "ERROR"]
    processed_count: int
    results: list[RolloverUserResult]
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "processed_count": self.processed_count,
            "results": [r.to_dict() for r in self.results],
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Structured JSON Schemas for AI Subagents
# ---------------------------------------------------------------------------

#: Schema for Subagent 1: Scout (locates the 1-3 candidate files for an architectural component)
SCOUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate_files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of 1 to 3 file paths in the repository that implement or define the target architectural component.",
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of why these files were selected.",
        },
    },
    "required": ["candidate_files", "reasoning"],
}

#: Schema for Subagent 2: Evaluator (scores the extracted architectural code snippet against a rubric)
EVALUATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "Score from 0 to 100 assessing the completeness and quality of the architectural component.",
        },
        "passed": {
            "type": "boolean",
            "description": "True if the code genuinely demonstrates the architectural component (score >= 70).",
        },
        "key_mechanisms_found": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete technical mechanisms or primitives identified in the code.",
        },
        "critique": {
            "type": "string",
            "description": "Concise technical critique evaluating architecture design, concurrency, or robustness.",
        },
    },
    "required": ["score", "passed", "key_mechanisms_found", "critique"],
}
