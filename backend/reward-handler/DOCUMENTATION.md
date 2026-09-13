# Documentation — reward-handler

How the feature works, how to navigate the code, and which file to open for a
given bug. For setup and commands see `README.md`; for status and the TODO list
see `../PROGRESS.md` (backend-wide); for the original spec see `../RewardHandler.md`.

## What it does

The **Reward Handler & Gamified Engine** serves as the centralized progression,
transaction, and anti-cheat verification backend for Upjob:

1. **Verification event ingestion:**
   - **Job applications:** Idempotency checking via `confirmation_hash` (anti-cheat HTTP 409 Conflict), daily application counting, base byte payouts, tier target bonus bytes with streak multipliers, and +100 XP.
   - **Architectural quests:** Base awards (500 XP, 100 Cores, 200 Bytes) plus high-quality bonus (+50 Cores when score >= 90) and profile architectural component unlocks.
2. **Streak state machine & midnight rollover:**
   - Tracks daily application targets against completions.
   - Preserves streaks by consuming active `streak_freeze` inventory items if target was missed.
   - Evaluates milestone bonuses (Day 7 bronze badge + 50 Cores, Day 30 gold badge + 250 Cores).
   - Dual execution: scheduled cron (`POST /api/economy/cron/rollover`) and lazy catch-up on user requests.
3. **Atomic shop redemptions:**
   - Transactional purchasing of consumables (`streak_freeze`), cosmetics (`cosmetic_title_cicd_demon`, `cosmetic_border_distributed`), and functional platform feature unlocks (`unlock_reach_job_deep_scan`, etc.) with `SELECT ... FOR UPDATE` row locks.
4. **Real-time leaderboard synchronization:**
   - Pipelined atomic `ZINCRBY` updates to Redis `leaderboard:all_time` and `leaderboard:weekly` sorted sets on every XP gain.
   - Monday 00:00:00 UTC weekly reset with PostgreSQL snapshotting into `weekly_leaderboard_snapshots`.
   - In-memory sorted-set fallback (`InMemoryRedisLeaderboard`) for local development without an external Redis instance.
5. **Token-optimized subagents for architectural evaluation:**
   - 3-tier in-process evaluation funnel reducing evaluation costs from ~120,000 tokens down to ~2,500 tokens per repo evaluation (>97% reduction).

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
| `tests/` | Offline stdlib `unittest` — one module per source file. |

## Data flow

```
verify_job_application(payload)                             [reward_handler.py]
  │
  ├─ ingest_application_verification(conn, redis, payload)   [verification.py]
  │     ├─ check verification_logs for confirmation_hash
  │     │    └─ exists? → return CONFLICT (HTTP 409)         Anti-Cheat
  │     │
  │     ├─ get_or_create_user_economy(conn, user_id)         [economy.py]
  │     │
  │     ├─ async with conn.transaction():
  │     │     ├─ INSERT into verification_logs
  │     │     ├─ count_user_apps_in_range(conn, today_UTC)   [economy.py]
  │     │     ├─ calculate XP (+100) & base bytes (+50 if 1st app)
  │     │     ├─ target reached? → + (bonus_bytes * streak_mult)
  │     │     └─ UPDATE user_economy SET bytes, xp_score
  │     │
  │     └─ update_leaderboard(redis, user_id, xp_gain)       [leaderboard.py]
  │           ├─ zincrby leaderboard:all_time +xp
  │           └─ zincrby leaderboard:weekly +xp
  │
  └─ return ApplicationVerificationResponse envelope
```

```
evaluate_repository_component(repo_files, component)         [subagents.py]
  │
  ├─ Tier 0: Cache check by (commit_sha, component)
  │     └─ hit? → return cached evaluation                   (0 tokens)
  │
  ├─ Tier 0: filter_file_tree(all_paths)
  │     └─ drop binary/noise/tests/vendor paths              (0 tokens)
  │
  ├─ Tier 1: run_scout_subagent(ai_client, tree, component)
  │     └─ gemini-3.5-flash-lite on pruned tree + README     (~800 tokens)
  │     └─ returns top 1-3 candidate source files
  │
  ├─ extract_python_structural_signatures(candidate_files)
  │     └─ ast.parse classes, functions, and interfaces      (0 tokens)
  │
  ├─ Tier 2: run_evaluator_subagent(ai_client, component, snippets)
  │     └─ gemini-3.5-flash-lite on extracted AST snippets   (~1,500 tokens)
  │     └─ returns score (0-100), passed, critique
  │
  └─ return structured evaluation (~2,500 total tokens vs ~120,000)
```

## Key invariants (do not break these)

- **Idempotent hash enforcement.** Confirmation hashes are unique across all users in `verification_logs`. A repeated hash is an anti-cheat violation that immediately aborts with `CONFLICT` (HTTP 409).
- **Row-level locking on shop debits.** Purchases in `redeem_shop_item` must use `SELECT ... FOR UPDATE` inside a database transaction to prevent balance underflow or double-spending under concurrent requests.
- **The economy calculations have no model in them.** Multipliers, streaks, freeze decrements, and shop debits are deterministic Python pure logic; models are used strictly for code evaluation in `subagents.py`.
- **In-memory Redis fallback.** When `REDIS_URL` is omitted, the service falls back to `InMemoryRedisLeaderboard` so local development and unit tests run with zero external infrastructure.
- **In-process subagents.** Subagents run directly within the backend process via async calls and `asyncio.Semaphore` bounds without requiring a separate Celery or Redis worker queue.
- **Degrade, don't crash.** If Redis is temporarily unreachable, the error is logged and the PostgreSQL transaction completes successfully.
- **Heavy SDKs are lazy.** `asyncpg`, `redis`, and `google-genai` are reached only inside functions so pure logic and tests run without them installed.

## Which file to change for a bug

| Symptom | Start here |
|---|---|
| Incorrect streak multiplier or milestone payouts | `schemas.py` — `STREAK_TIER_MULTIPLIERS`, `MILESTONES`, `get_streak_multiplier` |
| Streak resets unexpectedly or freeze not deducted | `economy.py` — `compute_rollover_transition`, `process_user_rollover`, `evaluate_lazy_rollover` |
| Duplicate application accepted or 409 not returned | `verification.py` — `ingest_application_verification`, confirmation hash lookup |
| Shop item purchase fails or balance underflow | `economy.py` — `redeem_shop_item`, `SELECT ... FOR UPDATE` |
| Redis leaderboard ranking off by 1 or missing user | `leaderboard.py` — `zrevrank` 1-based indexing, `get_leaderboard` |
| High token usage during quest code evaluation | `subagents.py` — `filter_file_tree`, `extract_python_structural_signatures`, `run_scout_subagent` |
| API endpoint error or missing response parameter | `reward_handler.py` — `create_fastapi_router`, response builders |
| Database schema or table constraint changes | `migrations/001_reward_handler_schema.sql` (idempotent — safe to re-run) |

## Testing

All tests are offline stdlib `unittest`. Run the suite:

```bash
python -m unittest discover -s tests -t .
```

Conventions to preserve when adding tests:
- Use `make_mock_conn()` in `tests/test_economy.py` and `tests/test_verification.py` to stub `asyncpg` connections with async transaction context managers.
- Use `InMemoryRedisLeaderboard` from `leaderboard.py` to test sorted set pipelines without a live Redis server.
- Stub Gemini model responses via `mock_client.models.generate_content` (see `tests/test_subagents.py`).
- Never hit a live database, network endpoint, or external cache in unit tests.
