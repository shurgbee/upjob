# Documentation — reward-handler

How the feature works, how to navigate the code, and which file to open for a
given bug. For setup and commands see `README.md`; for status and the TODO list
see `../PROGRESS.md` (backend-wide); for the original spec see `../RewardHandler.md`.

## What it does

The **Reward Handler & Gamified Engine** serves as the centralized progression,
economy, and audit backend for Upjob:

1. **Verification Event Ingestion:**
   - **Job Applications:** Idempotency checking via `confirmation_hash` (anti-cheat 409 Conflict), daily application counting, base byte payouts, tier target bonus bytes with streak multipliers, and +100 XP.
   - **Architectural Quests:** Base awards (500 XP, 100 Cores, 200 Bytes) plus high-quality bonus (+50 Cores when score >= 90) and profile architectural component unlocks.
2. **Streak State Machine & Midnight Rollover:**
   - Tracks daily targets against previous day's completions.
   - Preserves streaks by consuming active `streak_freeze` inventory items if target was missed.
   - Evaluates milestone bonuses (e.g. Day 7 bronze badge + 50 Cores, Day 30 gold badge + 250 Cores).
   - Dual execution: scheduled cron (`POST /api/economy/cron/rollover`) and lazy catch-up on user requests.
3. **Atomic Shop Redemptions:**
   - Transactional purchasing of consumables (`streak_freeze`), cosmetics (`cosmetic_title_cicd_demon`, `cosmetic_border_distributed`), and functional platform feature unlocks (`unlock_reach_job_deep_scan`, etc.) with `SELECT ... FOR UPDATE` row locks.
4. **Real-time Leaderboard Synchronization:**
   - Pipelined updates to Redis `leaderboard:all_time` and `leaderboard:weekly` sorted sets on every XP gain.
   - Monday 00:00:00 UTC weekly reset with PostgreSQL snapshotting into `weekly_leaderboard_snapshots`.
   - In-memory sorted-set fallback for local development without an external Redis instance.
5. **Token-Optimized Subagents for Architectural Evaluation:**
   - 3-tier evaluation funnel reducing evaluation costs from ~120,000 tokens down to ~2,500 tokens per repo evaluation (>97% reduction).

## File map

| File | Responsibility |
|---|---|
| `reward_handler.py` | Public async entry points (`get_user_economy_status`, `redeem_item`, `set_user_daily_tier`, `fetch_leaderboard`, `verify_job_application`, `verify_architectural_quest`) + FastAPI router + CLI. |
| `schemas.py` | Engine constants (`APPLICATION_TIERS`, `STREAK_TIER_MULTIPLIERS`, `MILESTONES`, `SHOP_CATALOG`), response DTOs, and AI schemas (`SCOUT_SCHEMA`, `EVALUATOR_SCHEMA`). Pure, stdlib-only. |
| `economy.py` | State machine transitions, midnight rollover, lazy catch-up evaluation, and atomic shop debits (`SELECT ... FOR UPDATE`). |
| `leaderboard.py` | Redis sorted-set synchronization, user rankings, weekly reset snapshotting, and `InMemoryRedisLeaderboard` fallback. |
| `verification.py` | Ingestion handlers for daily job application events (idempotency, dynamic bonus calculation) and architectural quest awards. |
| `subagents.py` | In-process tiered subagents: Tier 0 deterministic filters & caching, Tier 1 Scout Subagent, AST structural signature extractor, Tier 2 Rubric Evaluator Subagent. |
| `migrations/001_reward_handler_schema.sql` | PostgreSQL schema for `user_economy`, `verification_logs`, `quest_completions`, `user_inventory`, and `weekly_leaderboard_snapshots`. |
| `tests/` | 57 offline stdlib `unittest` tests covering schemas, state machine, Redis/memory leaderboards, subagents, and entry points. |

## Data flows

### 1. Job Application Verification Flow

```
verify_job_application(payload)
  │
  ├─ Check verification_logs for confirmation_hash (Anti-Cheat)
  │    └─ If exists → return CONFLICT (HTTP 409)
  │
  ├─ In atomic PostgreSQL transaction:
  │    ├─ INSERT into verification_logs
  │    ├─ Query today's verified apps: COUNT(*) WHERE verified_at >= today_00:00_UTC
  │    ├─ Calculate XP: +100 XP
  │    ├─ First App today: +50 base_bytes
  │    ├─ Target Reached (today_count == daily_target):
  │    │    └─ + (bonus_bytes * streak_multiplier)
  │    └─ UPDATE user_economy SET bytes, xp_score, last_active_date
  │
  ├─ update_leaderboard (Redis pipeline):
  │    ├─ ZINCRBY leaderboard:all_time +xp_gained
  │    └─ ZINCRBY leaderboard:weekly +xp_gained
  │
  └─ Return ApplicationVerificationResponse
```

### 2. Subagent Quest Evaluation Flow

```
evaluate_repository_component(repo_files, architectural_component)
  │
  ├─ Tier 0: Cache Check by (commit_sha, component)
  │    └─ If cached → return cached evaluation (0 tokens)
  │
  ├─ Tier 0: File Tree Filter
  │    └─ Exclude binary, test, doc, and vendor paths (0 tokens)
  │
  ├─ Tier 1: Scout Subagent (gemini-3.5-flash-lite)
  │    └─ Inputs: pruned tree + README context (< 1,000 tokens)
  │    └─ Output: 1–3 candidate source file paths
  │
  ├─ Deterministic AST Snipper
  │    └─ Extracts classes, function headers, concurrency primitives (0 tokens)
  │
  ├─ Tier 2: Evaluator Subagent (gemini-3.5-flash-lite)
  │    └─ Inputs: extracted snippet (< 1,500 tokens) + strict rubric
  │    └─ Output: score (0-100), passed, key_mechanisms, critique
  │
  └─ Return structured evaluation (~2,500 total tokens vs ~120,000)
```

### 3. Streak Rollover & Midnight State Machine

```
process_user_rollover(user, target_date)
  │
  ├─ Query applications completed on target_date
  │
  ├─ Did user hit daily target? (apps >= daily_target)
  │    ├─ YES:
  │    │    ├─ current_streak += 1
  │    │    └─ If current_streak in MILESTONES:
  │    │         ├─ Award milestone cores
  │    │         └─ Append milestone badge to user_inventory.badges
  │    │
  │    └─ NO:
  │         ├─ active_streak_freezes > 0?
  │         │    ├─ YES: active_streak_freezes -= 1 (Streak Preserved)
  │         │    └─ NO:  current_streak = 0 (Streak Reset)
  │
  └─ UPDATE user_economy SET current_streak, active_streak_freezes, cores
```

## Key invariants (do not break these)

- **Idempotent Hash Enforcement:** Confirmation hashes must be unique across all users and time in `verification_logs`. A repeated hash is an anti-cheat violation that must abort with `CONFLICT` (HTTP 409).
- **Row-Level Locking for Redemptions:** Shop purchases must use `SELECT ... FOR UPDATE` inside a database transaction to prevent double-spending under concurrent requests.
- **In-Memory Redis Fallback:** If `REDIS_URL` is omitted, the service falls back to `InMemoryRedisLeaderboard` so local development and offline unit tests work without a running Redis server.
- **In-Process Subagent Execution:** Subagents run directly in the backend process via async calls and `asyncio.Semaphore` bounds without requiring a Celery or Redis worker queue.
- **Degrade, Don't Crash on Redis Failure:** If Redis is down, log an error and allow the PostgreSQL transaction to succeed.

## Which file to change for a bug

| Symptom | Start here |
|---|---|
| Incorrect streak multiplier or milestone payouts | `schemas.py` (`STREAK_TIER_MULTIPLIERS`, `MILESTONES`, `get_streak_multiplier`) |
| Streak resets unexpectedly or freeze not deducted | `economy.py` (`compute_rollover_transition`, `process_user_rollover`, `evaluate_lazy_rollover`) |
| Duplicate application accepted or 409 not returned | `verification.py` (`ingest_application_verification`, confirmation hash lookup) |
| Shop item purchase fails or balance balance underflow | `economy.py` (`redeem_shop_item`, `SELECT ... FOR UPDATE`) |
| Redis leaderboard ranking off by 1 or missing user | `leaderboard.py` (`zrevrank` 1-based indexing, `get_leaderboard`) |
| High token usage during quest code evaluation | `subagents.py` (`filter_file_tree`, `extract_python_structural_signatures`, `run_scout_subagent`) |
| API endpoint error or missing response parameter | `reward_handler.py` (`create_fastapi_router`, response builders) |
