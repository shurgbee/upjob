Here is the complete, production-ready implementation specification for the **Reward Handler & Gamified Engine**, structured so Claude Code can build the backend services, state transitions, transaction logic, and API routes cleanly without ambiguity.

---

# Specification: Feature #3 – Reward Handler & Gamified Engine

### 1. Overview & Boundaries

- **Purpose:** Act as the centralized transaction and progression engine that ingests verification events (applications, quest completions), calculates dynamic payouts (XP, Bytes, Cores), maintains streak states, enforces freeze logic, updates real-time Redis leaderboards, and handles currency redemptions.
- **Execution Model:** Asynchronous backend service / controller with atomic PostgreSQL transactions, paired with write-through updates to Redis Sorted Sets.
- **Tech Stack:**
- **Reasoning & Verification:** `gemini-3.5-flash-lite` (via `google-genai` / LangChain).
- **Primary Database:** PostgreSQL (`user_economy`, `verification_logs`).
- **Leaderboard Cache:** Redis (`ZSET`).

- **Economic Model:**
- **Soft Currency (`bytes`):** Consumable grinding currency. Used for streak freezes and UI cosmetics.
- **Premium Currency (`cores`):** High-friction currency. Used strictly for functional platform unlocks.
- **Experience (`xp_score`):** Monotonically increasing score for global/weekly leaderboard ranking.

---

### 2. Tier Configurations & Reward Constants

Claude Code must define these baseline constants in an engine configuration file:

```python
# Application Tiers: (tier_id, name, target_count, base_bytes, tier_bonus_bytes, base_xp)
APPLICATION_TIERS = {
    1: {"name": "Casual",  "target": 1, "base_bytes": 50,  "bonus_bytes": 25,  "xp_per_app": 100},
    2: {"name": "Hustler", "target": 3, "base_bytes": 50,  "bonus_bytes": 100, "xp_per_app": 100},
    3: {"name": "Grinder", "target": 5, "base_bytes": 50,  "bonus_bytes": 250, "xp_per_app": 100}
}

# Streak Multipliers: Multiplier applied to daily tier bonus bytes
STREAK_TIER_MULTIPLIERS = {
    0: 1.0,   # 1-6 days
    7: 1.25,  # 7-13 days
    14: 1.5,  # 14-29 days
    30: 2.0   # 30+ days
}

# Milestone Bonuses (One-time payouts upon crossing streak thresholds)
MILESTONES = {
    7:  {"cores": 50,  "badge_id": "streak_flame_bronze"},
    30: {"cores": 250, "badge_id": "streak_flame_gold"}
}

# Shop Costs
SHOP_CATALOG = {
    # Consumables (Soft Currency)
    "streak_freeze":               {"cost_bytes": 150, "cost_cores": 0},
    "cosmetic_border_distributed": {"cost_bytes": 500, "cost_cores": 0},
    "cosmetic_title_cicd_demon":   {"cost_bytes": 300, "cost_cores": 0},

    # Premium Functional Unlocks (Premium Currency)
    "unlock_outreach_drafts":      {"cost_bytes": 0, "cost_cores": 100},
    "unlock_company_mock_loop":    {"cost_bytes": 0, "cost_cores": 200},
    "unlock_ats_deep_scanner":     {"cost_bytes": 0, "cost_cores": 150},
    "unlock_reach_job_deep_scan":  {"cost_bytes": 0, "cost_cores": 300}
}

```

---

### 3. Pipeline Step 1: Verification Event Ingestion

The Reward Handler receives events from two verification routes and one quest evaluator:

#### A. Daily Job Application Verified Event

- **Trigger:** Successful verification via **Email Forwarding Gate** or **Vision-Based Validation System**.

- **Payload:** `{ user_id: UUID, job_id: UUID, confirmation_hash: string, verification_type: 'EMAIL' | 'VISION' }`
- **Idempotency Check:** Query `verification_logs` for `confirmation_hash`. If exists, abort with `409 Conflict` (Anti-Cheat duplicate protection).
- **Reward Processing:**

1. Log entry to `verification_logs` hypertable.
2. Query total verified applications completed by `user_id` today between `00:00:00 UTC` and `NOW()`.
3. Increment user's `xp_score` by `APPLICATION_TIERS[user.daily_tier_target]["xp_per_app"]` (100 XP).
4. **First App of Day:** Grant `base_bytes` (50 Bytes).
5. **Tier Target Reached:** If `today_app_count == user.daily_tier_target`:

- Calculate multiplier from `STREAK_TIER_MULTIPLIERS` based on `current_streak`.
- Grant `tier_bonus_bytes * multiplier`.

#### B. Architectural Quest Verified Event

- **Trigger:** Successful evaluation from `gemini-3.5-flash-lite` analyzing GitHub repository via GitHub MCP.
- **Payload:** `{ user_id: UUID, quest_id: UUID, architectural_component: string, score: int }`
- **Reward Processing:**

1. Base Quest completion awards: **`500 XP`**, **`100 Cores`**, and **`200 Bytes`**.
2. If `score >= 90` (High Quality Bonus): Add **`50 Cores`**.
3. Append unlocked architectural component to user's verified profile.

---

### 4. Pipeline Step 2: Midnight Rollover & Streak State Machine

Claude Code must implement a daily cron job scheduled at `00:00:00 UTC` (or lazy evaluation on user's first daily request):

```text
[Daily Check at 00:00:00 UTC]
               │
               ▼
   Did User Hit Daily Target?
        (apps_yesterday >= daily_tier_target)
               │
      ┌────────┴────────┐
     YES                NO
      │                 │
      ▼                 ▼
current_streak += 1;   active_streak_freezes > 0?
Check Milestones;       │
                        ├─────────────────┐
                       YES                NO
                        │                 │
                        ▼                 ▼
             active_streak_freezes -= 1;  current_streak = 0;
             (Streak Preserved)           (Streak Reset)

```

#### Milestone Trigger Logic

If `current_streak` matches any key in `MILESTONES`:

1. Issue specified `cores` to `user_economy.cores`.
2. Insert `badge_id` into the user's cosmetics inventory table/JSONB array.
3. Enqueue a real-time notification payload for frontend display.

---

### 5. Pipeline Step 3: Shop & Functional Unlocks

Claude Code must implement the `POST /api/economy/redeem` endpoint executing in an atomic database transaction (`SERIALIZABLE` or `SELECT ... FOR UPDATE`):

1. **Validation:** Verify item exists in `SHOP_CATALOG`.
2. **Affordability Check:**

- Ensure `user_economy.bytes >= item.cost_bytes`.
- Ensure `user_economy.cores >= item.cost_cores`.

1. **Atomic Debit:**

```sql
UPDATE user_economy
SET bytes = bytes - :cost_bytes,
    cores = cores - :cost_cores,
    active_streak_freezes = active_streak_freezes + CASE WHEN :item_id = 'streak_freeze' THEN 1 ELSE 0 END,
    updated_at = NOW()
WHERE user_id = :user_id AND bytes >= :cost_bytes AND cores >= :cost_cores;

```

1. **Feature Unlock Flag:** If item is a functional platform unlock (e.g., `unlock_reach_job_deep_scan`), toggle the corresponding boolean flag in `user_profile.unlocked_features` to `true`.

---

### 6. Pipeline Step 4: Redis Leaderboard Synchronization

Every time `xp_score` is updated (application verified, quest completed, or milestone achieved), sync immediately to Redis:

#### Redis Keys

- `leaderboard:all_time`: Sorted Set of total historical XP.
- `leaderboard:weekly`: Sorted Set of XP earned in the current weekly sprint.

#### Execution Pipeline

```python
# Run via async pipeline on XP changes
async def update_leaderboard(redis_client, user_id: str, xp_gained: int):
    pipe = redis_client.pipeline()
    # 1. Update All-Time Score
    pipe.zincrby("leaderboard:all_time", xp_gained, str(user_id))
    # 2. Update Weekly Sprint Score
    pipe.zincrby("leaderboard:weekly", xp_gained, str(user_id))
    await pipe.execute()

```

#### Weekly Reset Cron

Run every **Monday at 00:00:00 UTC**:

1. Archive or snapshot top 10 from `leaderboard:weekly` to Postgres historical records.
2. Execute `DEL leaderboard:weekly` in Redis to reset the sprint.

---

### 7. API Endpoints Schema for Frontend & Agents

Claude Code must expose these 4 endpoints:

#### 1. `GET /api/economy/me`

- **Response:**

```json
{
  "user_id": "uuid",
  "bytes": 450,
  "cores": 120,
  "xp_score": 3200,
  "current_streak": 8,
  "active_streak_freezes": 1,
  "daily_tier": {
    "tier_id": 2,
    "name": "Hustler",
    "target": 3,
    "completed_today": 2
  },
  "streak_multiplier": 1.25,
  "rankings": {
    "all_time_rank": 14,
    "weekly_rank": 4
  }
}
```

#### 2. `POST /api/economy/set-tier`

- **Payload:** `{ "tier_id": 1 | 2 | 3 }`
- **Response:** `{ "status": "SUCCESS", "new_tier": 2 }`

#### 3. `POST /api/economy/redeem`

- **Payload:** `{ "item_id": "string" }`
- **Response:** `{ "status": "SUCCESS", "remaining_bytes": 300, "remaining_cores": 120, "unlocked": "streak_freeze" }`

#### 4. `GET /api/leaderboard?type=weekly&limit=50`

- **Internal Call:** `ZREVRANGE leaderboard:weekly 0 49 WITHSCORES`
- **Response:**

```json
{
  "leaderboard_type": "weekly",
  "top_users": [
    { "rank": 1, "user_id": "uuid", "xp": 1400, "flair": "CI/CD Demon" },
    { "rank": 2, "user_id": "uuid", "xp": 1250, "flair": "Async Wizard" }
  ],
  "current_user": { "rank": 4, "xp": 800 }
}
```
