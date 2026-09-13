-- Link applications to the job spec a user applied to.
--
-- job_specs gets a stable uuid (its integer id is a serial, awkward as a public
-- key), and job_applications gains a FK into it. Semantics: one row == "this
-- job_spec, applied to by this user". Additive and idempotent so ensure_schema
-- can re-run it safely. gen_random_uuid() is core Postgres (>= 13), no extension.

ALTER TABLE job_specs
    ADD COLUMN IF NOT EXISTS spec_id uuid NOT NULL DEFAULT gen_random_uuid();

DO $$ BEGIN
    ALTER TABLE job_specs ADD CONSTRAINT job_specs_spec_id_key UNIQUE (spec_id);
-- UNIQUE builds an index, so a re-run raises duplicate_table (42P07), not just
-- duplicate_object (42710); catch both so ensure_schema is idempotent.
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

ALTER TABLE job_applications
    ADD COLUMN IF NOT EXISTS job_spec_id uuid;

DO $$ BEGIN
    ALTER TABLE job_applications
        ADD CONSTRAINT job_applications_job_spec_id_fkey
        FOREIGN KEY (job_spec_id) REFERENCES job_specs(spec_id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_job_applications_user_spec
    ON job_applications (user_id, job_spec_id);
