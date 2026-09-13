-- Gmail thread IDs are short hex strings (e.g. "18c0f2a9b4d1e6f0"), not UUIDs,
-- so the live job_applications.thread_id uuid column cannot hold them. The table
-- is empty, so retyping it to text is safe and loss-free.
--
-- Idempotent: only retypes when the column is still uuid, so repeated runs (the
-- feature applies migrations on startup) are no-ops after the first.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'job_applications'
          AND column_name = 'thread_id'
          AND data_type = 'uuid'
    ) THEN
        ALTER TABLE public.job_applications
            ALTER COLUMN thread_id TYPE text USING thread_id::text;
    END IF;
END $$;

-- Guarantee a Gmail thread is attached to at most one application row. Partial
-- so the many rows that legitimately have no thread yet are not constrained.
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_applications_thread_id
    ON public.job_applications (thread_id)
    WHERE thread_id IS NOT NULL;
