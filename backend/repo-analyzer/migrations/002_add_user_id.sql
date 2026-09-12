-- Add user_id column to projects table for multi-user support
-- Safe to re-run: uses IF NOT EXISTS on ADD COLUMN and CREATE INDEX

ALTER TABLE projects ADD COLUMN IF NOT EXISTS user_id TEXT;

COMMENT ON COLUMN projects.user_id IS
    'Identifies the owning user (nullable until auth is wired; scoping key for resume tailor).';

CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
