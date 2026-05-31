-- ShetMitra Variety Collection Migration 004
-- Date: 2026-05-31
-- Description: Add WhatsApp variety-collection schema on top of the AMED
-- integration: new columns on farmers/farm_plots/whatsapp_sessions plus the
-- variety_responses audit table. All statements are idempotent and safe to
-- re-run; CHECK constraints on existing tables are wrapped in DO blocks that
-- swallow duplicate_object so multiple runs do not error.

-- Required for gen_random_uuid(). Already created by migration 001 on Supabase,
-- but kept here so this file is standalone-runnable.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- whatsapp_sessions  — defensive create. Live Supabase has this table, but
-- having a CREATE TABLE IF NOT EXISTS here keeps every downstream environment
-- (local, staging, future Supabase project) consistent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS whatsapp_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);

-- Per-column ADDs make the migration tolerant of a pre-existing
-- whatsapp_sessions table that may have a slightly different shape
-- (e.g. the TEST project's variant uses just `mobile` instead of
-- `mobile_number`). Each ALTER is a no-op when the column already exists.
ALTER TABLE whatsapp_sessions
    ADD COLUMN IF NOT EXISTS mobile_number text,
    ADD COLUMN IF NOT EXISTS farmer_id uuid REFERENCES farmers(id),
    ADD COLUMN IF NOT EXISTS current_step text,
    ADD COLUMN IF NOT EXISTS collection_flow text DEFAULT 'booking',
    ADD COLUMN IF NOT EXISTS session_data jsonb DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now(),
    ADD COLUMN IF NOT EXISTS expires_at timestamptz;

-- If a legacy 'mobile' column exists and 'mobile_number' is empty, mirror.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='whatsapp_sessions' AND column_name='mobile'
    ) THEN
        UPDATE whatsapp_sessions
           SET mobile_number = mobile
         WHERE mobile_number IS NULL AND mobile IS NOT NULL;
    END IF;
END $$;

-- UNIQUE + indexes after the column is guaranteed to exist.
DO $$
BEGIN
    ALTER TABLE whatsapp_sessions ADD CONSTRAINT whatsapp_sessions_mobile_number_key UNIQUE (mobile_number);
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table  THEN NULL;
    WHEN unique_violation THEN
        RAISE NOTICE 'whatsapp_sessions has duplicate mobile_number values; UNIQUE not added. Clean dupes and re-run.';
END $$;

CREATE INDEX IF NOT EXISTS idx_whatsapp_sessions_mobile
    ON whatsapp_sessions(mobile_number);
CREATE INDEX IF NOT EXISTS idx_whatsapp_sessions_farmer
    ON whatsapp_sessions(farmer_id);

-- ---------------------------------------------------------------------------
-- TASK 1 — farmers: variety collection state
-- ---------------------------------------------------------------------------
ALTER TABLE farmers
    ADD COLUMN IF NOT EXISTS amed_variety_collected boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS amed_variety_collected_at timestamptz,
    ADD COLUMN IF NOT EXISTS alternate_mobile text,
    ADD COLUMN IF NOT EXISTS variety_collection_attempts integer DEFAULT 0,
    ADD COLUMN IF NOT EXISTS variety_collection_status text DEFAULT 'PENDING',
    -- Agent 3's pipeline-trigger throttle column.
    ADD COLUMN IF NOT EXISTS variety_collection_attempted_at timestamptz;

-- CHECK constraint on variety_collection_status. Wrapped in DO block so a
-- re-run does not error if it already exists.
DO $$
BEGIN
    ALTER TABLE farmers
        ADD CONSTRAINT farmers_variety_collection_status_chk
        CHECK (variety_collection_status IN (
            'PENDING',
            'AWAITING_REPLY',
            'COMPLETE',
            'FAILED',
            'AGENT_REQUIRED'
        ));
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table  THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- TASK 2 — farm_plots: variety + self-reported acres + verification fields
-- amed_crop_verified and area_mismatch_pct were added in migration 002.
-- ADD COLUMN IF NOT EXISTS keeps it safe.
-- ---------------------------------------------------------------------------
ALTER TABLE farm_plots
    ADD COLUMN IF NOT EXISTS current_crop_variety text,
    ADD COLUMN IF NOT EXISTS self_reported_acres numeric,
    ADD COLUMN IF NOT EXISTS amed_crop_verified boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS amed_verification_date date,
    ADD COLUMN IF NOT EXISTS area_mismatch_pct numeric,
    ADD COLUMN IF NOT EXISTS variety_source text DEFAULT 'farmer_reported';

DO $$
BEGIN
    ALTER TABLE farm_plots
        ADD CONSTRAINT farm_plots_variety_source_chk
        CHECK (variety_source IN (
            'farmer_reported',
            'agent_verified',
            'amed_hint'
        ));
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table  THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- TASK 3 — whatsapp_sessions: collection_flow column + CHECK constraint
-- ---------------------------------------------------------------------------
ALTER TABLE whatsapp_sessions
    ADD COLUMN IF NOT EXISTS collection_flow text DEFAULT 'booking';

DO $$
BEGIN
    ALTER TABLE whatsapp_sessions
        ADD CONSTRAINT whatsapp_sessions_collection_flow_chk
        CHECK (collection_flow IN (
            'booking',
            'variety_collection',
            'registration'
        ));
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table  THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- TASK 4 — variety_responses: audit trail of every farmer reply in the flow
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS variety_responses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    farmer_id uuid REFERENCES farmers(id),
    plot_id uuid REFERENCES farm_plots(id),
    amed_crop_detected text,
    amed_confidence numeric,
    variety_reported text,
    name_confirmed text,
    phone_confirmed text,
    village_confirmed text,
    acres_reported numeric,
    acres_mismatch_pct numeric,
    mismatch_resolution text,
    collection_started_at timestamptz,
    collection_completed_at timestamptz,
    status text DEFAULT 'IN_PROGRESS'
        CHECK (status IN (
            'IN_PROGRESS',
            'COMPLETE',
            'ABANDONED',
            'AGENT_REQUIRED'
        )),
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_variety_farmer
    ON variety_responses(farmer_id);
CREATE INDEX IF NOT EXISTS idx_variety_status
    ON variety_responses(status);
