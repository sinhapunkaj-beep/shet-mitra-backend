-- ShetMitra Continuous Training Migration 008
-- Date: 2026-05-31
-- Target: TEST
-- Description: Adds model_registry (versioned ML model history with MAPE/MAE)
-- and cron_run_log (per-job audit trail of every scheduler firing).

-- ---------------------------------------------------------------------------
-- model_registry: every retrain inserts a new row, sets prior row's
-- is_active=false for the same (commodity, variety) pair.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_registry (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    commodity text NOT NULL,
    variety text,
    model_version text NOT NULL,
    model_type text,
    mape numeric,
    mae numeric,
    training_rows integer,
    training_date_start date,
    training_date_end date,
    retrain_trigger text,
    is_active boolean DEFAULT true,
    pickle_path text,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_registry_active
    ON model_registry(commodity, variety, is_active);
CREATE INDEX IF NOT EXISTS idx_model_registry_commodity_created
    ON model_registry(commodity, created_at DESC);

-- ---------------------------------------------------------------------------
-- cron_run_log: durable audit of every scheduled job firing.
-- An older version of this table may already exist on TEST with a slightly
-- different shape (pipeline_name / records_processed / records_failed /
-- started_at / completed_at). We additively introduce the columns we need
-- and backfill from the legacy fields.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cron_run_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);

ALTER TABLE cron_run_log
    ADD COLUMN IF NOT EXISTS job_id text,
    ADD COLUMN IF NOT EXISTS status text,
    ADD COLUMN IF NOT EXISTS reason text,
    ADD COLUMN IF NOT EXISTS metadata jsonb DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS fired_at timestamptz DEFAULT now();

-- Backfill the new columns from the legacy ones where possible.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='cron_run_log' AND column_name='pipeline_name')
    THEN
        UPDATE cron_run_log SET job_id = pipeline_name
         WHERE job_id IS NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='cron_run_log' AND column_name='completed_at')
    THEN
        UPDATE cron_run_log
           SET fired_at = COALESCE(completed_at, started_at, now())
         WHERE fired_at IS NULL;
    END IF;
END $$;

-- Normalize status values so the CHECK constraint can be added cleanly.
UPDATE cron_run_log SET status = CASE
    WHEN lower(COALESCE(status,'')) IN ('ok','success','done','complete')  THEN 'ok'
    WHEN lower(COALESCE(status,'')) IN ('skipped','skip','noop','no_op')   THEN 'skipped'
    WHEN lower(COALESCE(status,'')) IN ('error','fail','failed','crash')   THEN 'error'
    ELSE 'error'
END
WHERE status IS NULL
   OR status NOT IN ('ok','skipped','error');

-- Default job_id for any row that still has none (legacy rows without a
-- pipeline_name column or with NULL pipeline_name).
UPDATE cron_run_log SET job_id = 'legacy_unknown' WHERE job_id IS NULL;

DO $$ BEGIN
    ALTER TABLE cron_run_log ADD CONSTRAINT cron_run_log_status_chk
        CHECK (status IN ('ok','skipped','error'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_cron_run_log_job_fired
    ON cron_run_log(job_id, fired_at DESC);
