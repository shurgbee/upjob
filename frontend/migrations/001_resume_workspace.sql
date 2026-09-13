BEGIN;
CREATE TABLE IF NOT EXISTS public.app_users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workos_user_id TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Preserve legacy ownership without guessing which WorkOS account owns it.
INSERT INTO public.app_users (user_id)
SELECT DISTINCT user_id FROM public.projects ON CONFLICT DO NOTHING;
DO $$
DECLARE fk RECORD;
BEGIN
    FOR fk IN SELECT conname FROM pg_constraint
      WHERE conrelid = 'public.projects'::regclass AND contype = 'f'
        AND confrelid = to_regclass('public.user_economy')
    LOOP EXECUTE format('ALTER TABLE public.projects DROP CONSTRAINT %I', fk.conname); END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.projects'::regclass AND conname = 'projects_app_user_fkey') THEN
        ALTER TABLE public.projects ADD CONSTRAINT projects_app_user_fkey FOREIGN KEY (user_id) REFERENCES public.app_users(user_id);
    END IF;
END $$;
ALTER TABLE public.projects ADD COLUMN IF NOT EXISTS github_repo_url TEXT;
ALTER TABLE public.projects ADD COLUMN IF NOT EXISTS analysis JSONB NOT NULL DEFAULT '{}';
ALTER TABLE public.projects ADD COLUMN IF NOT EXISTS user_context TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS projects_owner_repo ON public.projects(user_id, github_repo_url);

CREATE TABLE IF NOT EXISTS public.resumes (
    user_id UUID PRIMARY KEY REFERENCES public.app_users(user_id),
    filename TEXT NOT NULL,
    source TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    pdf BYTEA,
    pdf_revision INTEGER,
    compile_status TEXT NOT NULL DEFAULT 'queued',
    compile_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.resume_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.app_users(user_id),
    kind TEXT NOT NULL CHECK (kind IN ('compile', 'scan', 'review', 'generate')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'superseded')),
    revision INTEGER,
    payload JSONB NOT NULL DEFAULT '{}',
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS resume_operations_pending ON public.resume_operations(status, created_at);
CREATE INDEX IF NOT EXISTS resume_operations_owner ON public.resume_operations(user_id, created_at DESC);
COMMIT;
