# reward-handler

The centralized transaction and gamified progression engine for Upjob. Ingests verification events (job applications, architectural quests), calculates dynamic currency and streak rewards, maintains streak state machines with freeze preservation, updates real-time Redis leaderboards, and handles currency redemptions. Implements the specification in `../RewardHandler.md`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL, optional REDIS_URL, GEMINI_API_KEY
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes (for live runs) | PostgreSQL connection string with pgvector / schema. |
| `REDIS_URL` | Optional | Redis connection string. If omitted, falls back to in-memory sorted set for local development. |
| `GEMINI_API_KEY` | Optional | Required only when invoking AI subagents for live quest code evaluation. |

### Database migration

Migrations run automatically via `ensure_schema(conn)` on first run, or apply manually:
```bash
psql $DATABASE_URL -f migrations/001_reward_handler_schema.sql
```

## CLI Usage

Run commands directly from the `reward-handler/` directory:

```bash
# Check user economy status, streak, and rankings
python reward_handler.py status <user_id>

# Change daily application target tier (1 = Casual, 2 = Hustler, 3 = Grinder)
python reward_handler.py set-tier <user_id> 2

# Redeem an item from the shop catalog
python reward_handler.py redeem <user_id> streak_freeze
python reward_handler.py redeem <user_id> cosmetic_title_cicd_demon
python reward_handler.py redeem <user_id> unlock_reach_job_deep_scan

# View current leaderboard standings
python reward_handler.py leaderboard --type weekly --limit 10
python reward_handler.py leaderboard --type all_time --limit 25 --user-id <user_id>

# Verify a job application (with anti-cheat duplicate hash checking)
python reward_handler.py verify-app <user_id> <job_id> <confirmation_hash> --type EMAIL

# Verify an architectural quest
python reward_handler.py verify-quest <user_id> <quest_id> "Distributed Container Engine" 95

# Trigger daily midnight UTC rollover
python reward_handler.py rollover
python reward_handler.py rollover --date 2026-09-12

# Trigger weekly Monday 00:00:00 UTC leaderboard reset & snapshot
python reward_handler.py weekly-reset
```

## Running Tests

All 57 unit tests run completely offline with no network, PostgreSQL, or Redis server required:

```bash
# Run all tests from reward-handler/
python -m unittest discover -s tests -t .

# Run single test module
python -m unittest tests.test_economy
python -m unittest tests.test_leaderboard
python -m unittest tests.test_subagents
python -m unittest tests.test_verification
python -m unittest tests.test_reward_handler
```
