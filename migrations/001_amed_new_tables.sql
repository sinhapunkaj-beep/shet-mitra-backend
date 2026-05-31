-- ShetMitra AMED Integration Migration 001
-- Date: 2026-05-31
-- Description: Create the three new AMED tables (amed_readings, amed_belt_data,
-- amed_history) as specified in SDD Sections 3.1, 3.2, 3.3.
-- All statements are idempotent: tables use CREATE TABLE IF NOT EXISTS and
-- indexes use CREATE INDEX IF NOT EXISTS. Safe to re-run.

-- Required for gen_random_uuid(). Supabase has this enabled by default but
-- creating it idempotently guards self-hosted setups.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- 3.1 amed_readings  (per-field AMED snapshots)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS amed_readings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plot_id uuid REFERENCES farm_plots(id),
    fetch_date date NOT NULL,
    crop_type_detected text,
    crop_type_confidence numeric,
    field_size_acres_amed numeric,
    sowing_date date,
    harvest_date_predicted date,
    growth_stage text,
    growth_stage_confidence numeric,
    irrigation_detected boolean,
    last_event text,
    last_event_date date,
    data_refresh_date date,
    use_mock boolean DEFAULT true,
    raw_response jsonb,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_amed_plot ON amed_readings(plot_id);
CREATE INDEX IF NOT EXISTS idx_amed_date ON amed_readings(fetch_date);

-- ---------------------------------------------------------------------------
-- 3.2 amed_belt_data  (regional belt rollups)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS amed_belt_data (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region text NOT NULL,
    fetch_date date NOT NULL,
    crop_type text NOT NULL,
    total_fields_detected integer,
    total_area_acres numeric,
    harvest_week_start date,
    harvest_week_end date,
    fields_harvesting integer,
    estimated_volume_mt numeric,
    health_pct_good numeric,
    health_pct_moderate numeric,
    health_pct_stressed numeric,
    health_pct_critical numeric,
    data_refresh_date date,
    raw_response jsonb,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_belt_region_date
    ON amed_belt_data(region, fetch_date);

-- ---------------------------------------------------------------------------
-- 3.3 amed_history  (3-year belt history seed table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS amed_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region text NOT NULL,
    season_label text NOT NULL,
    season_year_start integer,
    crop_type text,
    total_area_acres numeric,
    harvest_start_date date,
    harvest_peak_date date,
    harvest_end_date date,
    estimated_total_volume_mt numeric,
    avg_price_modal_kg numeric,
    created_at timestamptz DEFAULT now(),
    UNIQUE (region, season_label, crop_type)
);
