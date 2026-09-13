BEGIN;

CREATE TABLE IF NOT EXISTS public.skill_quests (
    quest_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.app_users(user_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    target_skills TEXT[] NOT NULL DEFAULT '{}',
    learning_objective TEXT NOT NULL,
    acceptance_criteria TEXT[] NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'evaluating', 'passed', 'needs_work')),
    repository_url TEXT,
    evaluation JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS skill_quests_owner
    ON public.skill_quests(user_id, created_at);

CREATE TABLE IF NOT EXISTS public.skill_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.app_users(user_id) ON DELETE CASCADE,
    quest_id UUID REFERENCES public.skill_quests(quest_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('plan', 'evaluate')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    payload JSONB NOT NULL DEFAULT '{}',
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS skill_operations_pending
    ON public.skill_operations(status, created_at);
CREATE INDEX IF NOT EXISTS skill_operations_owner
    ON public.skill_operations(user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS skill_operations_active_plan
    ON public.skill_operations(user_id, kind)
    WHERE kind = 'plan' AND status IN ('queued', 'running');
CREATE UNIQUE INDEX IF NOT EXISTS skill_operations_active_evaluation
    ON public.skill_operations(quest_id, kind)
    WHERE kind = 'evaluate' AND status IN ('queued', 'running');

COMMIT;
