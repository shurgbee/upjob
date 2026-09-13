-- job_applications had no primary key (rows addressed by ctid within a txn),
-- so nothing outside a single transaction could reference a specific row. Add a
-- stable uuid id so the UI can update one application (e.g. its status).
-- gen_random_uuid() is volatile, so ADD COLUMN backfills existing rows each with
-- a distinct value. Additive and idempotent for ensure_schema re-runs.

ALTER TABLE job_applications
    ADD COLUMN IF NOT EXISTS id uuid NOT NULL DEFAULT gen_random_uuid();

DO $$ BEGIN
    ALTER TABLE job_applications ADD CONSTRAINT job_applications_id_key UNIQUE (id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;
