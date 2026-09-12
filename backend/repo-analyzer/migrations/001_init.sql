-- Initialize schema for repository analysis storage with pgvector support
-- Safe to re-run: uses IF NOT EXISTS on all CREATE statements

CREATE EXTENSION IF NOT EXISTS vector;

-- Relational data: projects and their metadata
CREATE TABLE IF NOT EXISTS projects (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    github_repo_url TEXT NOT NULL UNIQUE,
    start_time TIMESTAMPTZ,
    -- NULL means "Present" (project is ongoing)
    end_time TIMESTAMPTZ,
    -- Array of technology names extracted from analysis
    technologies TEXT[] NOT NULL DEFAULT '{}',
    -- Array of system/architecture names extracted from analysis
    architectures TEXT[] NOT NULL DEFAULT '{}',
    -- Structured analysis data as JSON
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Markdown-formatted representation of details
    details_markdown TEXT NOT NULL DEFAULT '',
    -- Timestamp when the specification was first created
    spec_created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Timestamp when the specification was last updated
    spec_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE projects IS
    'Analyzed GitHub repositories with their extracted project specifications.';

-- Index for efficient relational queries on technology array
CREATE INDEX IF NOT EXISTS idx_projects_technologies
    ON projects USING GIN (technologies);

-- Index for efficient relational queries on architecture array
CREATE INDEX IF NOT EXISTS idx_projects_architectures
    ON projects USING GIN (architectures);

-- Vector embeddings of architecture descriptions for semantic search
CREATE TABLE IF NOT EXISTS project_architectures (
    id BIGSERIAL PRIMARY KEY,
    -- Foreign key to projects table; cascade delete to keep embeddings in sync
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- The combined architecture string that was embedded
    content TEXT NOT NULL,
    -- pgvector (text-embedding-004 is 768-dimensional)
    embedding vector(768) NOT NULL,
    -- When this embedding was created/updated
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE project_architectures IS
    'text-embedding-004 vectors (768-dim) of a project''s architecture list, '
    'keyed to projects.id for similarity matching.';

-- Index for efficient lookups by project
CREATE INDEX IF NOT EXISTS idx_project_architectures_project_id
    ON project_architectures(project_id);

-- ivfflat index for vector similarity search
-- Note: On TigerCloud with pgvectorscale, the diskann index is the native upgrade.
-- At small row counts (< 10K), neither index matters much; start with ivfflat.
CREATE INDEX IF NOT EXISTS idx_project_architectures_embedding
    ON project_architectures USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
