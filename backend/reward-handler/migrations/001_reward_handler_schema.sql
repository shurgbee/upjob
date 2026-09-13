-- Initialize schema for Reward Handler and Gamified Progression Engine
-- Safe to re-run: uses IF NOT EXISTS on all CREATE statements

-- 1. Core user economy state (balances, streak, tier targets)
CREATE TABLE IF NOT EXISTS user_economy (
    user_id TEXT PRIMARY KEY,
    bytes BIGINT NOT NULL DEFAULT 0 CHECK (bytes >= 0),
    cores BIGINT NOT NULL DEFAULT 0 CHECK (cores >= 0),
    xp_score BIGINT NOT NULL DEFAULT 0 CHECK (xp_score >= 0),
    current_streak INT NOT NULL DEFAULT 0 CHECK (current_streak >= 0),
    active_streak_freezes INT NOT NULL DEFAULT 0 CHECK (active_streak_freezes >= 0),
    daily_tier_target INT NOT NULL DEFAULT 1 CHECK (daily_tier_target IN (1, 2, 3)),
    last_active_date DATE,
    last_rollover_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE user_economy IS
    'Tracks soft currency (bytes), premium currency (cores), XP, streak counts, and tier targets.';

CREATE INDEX IF NOT EXISTS idx_user_economy_xp_score
    ON user_economy (xp_score DESC);

-- 2. Anti-cheat idempotency & verification history
CREATE TABLE IF NOT EXISTS verification_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES user_economy(user_id) ON DELETE CASCADE,
    job_id TEXT,
    confirmation_hash TEXT NOT NULL UNIQUE,
    verification_type TEXT NOT NULL CHECK (verification_type IN ('EMAIL', 'VISION')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE verification_logs IS
    'Idempotency and anti-cheat audit log for application verifications.';

CREATE INDEX IF NOT EXISTS idx_verification_logs_user_date
    ON verification_logs (user_id, verified_at);

CREATE INDEX IF NOT EXISTS idx_verification_logs_hash
    ON verification_logs (confirmation_hash);

-- 3. Architectural quest completions
CREATE TABLE IF NOT EXISTS quest_completions (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES user_economy(user_id) ON DELETE CASCADE,
    quest_id TEXT NOT NULL,
    architectural_component TEXT NOT NULL,
    score INT NOT NULL CHECK (score >= 0 AND score <= 100),
    xp_awarded INT NOT NULL,
    bytes_awarded INT NOT NULL,
    cores_awarded INT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_quest UNIQUE (user_id, quest_id)
);

COMMENT ON TABLE quest_completions IS
    'Audit record of completed architectural quests and awarded currency.';

CREATE INDEX IF NOT EXISTS idx_quest_completions_user
    ON quest_completions (user_id);

-- 4. User unlocked features, cosmetics inventory, and verified architectures
CREATE TABLE IF NOT EXISTS user_inventory (
    user_id TEXT PRIMARY KEY REFERENCES user_economy(user_id) ON DELETE CASCADE,
    badges TEXT[] NOT NULL DEFAULT '{}',
    cosmetics TEXT[] NOT NULL DEFAULT '{}',
    unlocked_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    verified_architectures TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE user_inventory IS
    'User cosmetics, awarded milestone badges, functional feature unlock flags, and verified components.';

-- 5. Weekly leaderboard snapshots (for historical auditing and reset archives)
CREATE TABLE IF NOT EXISTS weekly_leaderboard_snapshots (
    id BIGSERIAL PRIMARY KEY,
    week_start DATE NOT NULL,
    rank INT NOT NULL,
    user_id TEXT NOT NULL,
    xp BIGINT NOT NULL,
    flair TEXT,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE weekly_leaderboard_snapshots IS
    'Archival snapshot of weekly leaderboard standings taken prior to Monday resets.';

CREATE INDEX IF NOT EXISTS idx_leaderboard_snapshots_week
    ON weekly_leaderboard_snapshots (week_start, rank);
