-- ShetMitra AMED Integration Migration 002
-- Date: 2026-05-31
-- Description: Add AMED-related columns to existing tables farm_plots,
-- spray_advisories, and price_history_training as specified in SDD Section 3.4.
-- All statements use ADD COLUMN IF NOT EXISTS so they are idempotent and
-- safe to re-run against an already-altered schema.

-- ---------------------------------------------------------------------------
-- farm_plots  — AMED verification + mismatch flags
-- ---------------------------------------------------------------------------
ALTER TABLE farm_plots
    ADD COLUMN IF NOT EXISTS amed_crop_verified boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS amed_field_id text,
    ADD COLUMN IF NOT EXISTS amed_last_fetch date,
    ADD COLUMN IF NOT EXISTS crop_type_mismatch boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS area_mismatch_pct numeric;

-- ---------------------------------------------------------------------------
-- spray_advisories  — harvest window enrichment
-- Values for harvest_source: 'amed_confirmed' or 'ndvi_estimate'.
-- Wrapped in a DO block so the migration succeeds on databases that have
-- not yet created spray_advisories (it is owned by a downstream migration
-- in some deployment trees).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'spray_advisories'
    ) THEN
        ALTER TABLE spray_advisories
            ADD COLUMN IF NOT EXISTS harvest_window_start date,
            ADD COLUMN IF NOT EXISTS harvest_window_end date,
            ADD COLUMN IF NOT EXISTS harvest_source text DEFAULT 'ndvi_estimate',
            ADD COLUMN IF NOT EXISTS harvest_confidence numeric;
    ELSE
        RAISE NOTICE 'spray_advisories does not exist; skipping AMED harvest columns. Re-run after the table is created.';
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- price_history_training  — AMED leading-indicator features for ML model
-- ---------------------------------------------------------------------------
ALTER TABLE price_history_training
    ADD COLUMN IF NOT EXISTS amed_belt_volume_mt numeric,
    ADD COLUMN IF NOT EXISTS amed_fields_harvesting integer,
    ADD COLUMN IF NOT EXISTS amed_health_pct_good numeric,
    ADD COLUMN IF NOT EXISTS amed_season_week integer;
