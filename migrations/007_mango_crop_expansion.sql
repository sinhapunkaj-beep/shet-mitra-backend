-- ShetMitra Mango Crop Expansion Migration 007
-- Date: 2026-05-31
-- Target: TEST
-- Description: Adds mango-crop columns to farm_plots / farmers /
-- price_history_training; introduces the mango_phenology_log and
-- mango_belt_data regional tables; and creates the agents (Flutter
-- territory) and forex_rates (USD/INR proxy for Alphonso) tables.
-- All statements are idempotent and safe to re-run; CHECK constraint
-- adds on pre-existing tables are wrapped in DO blocks that swallow
-- duplicate_object so repeated runs do not fail.

-- Required for gen_random_uuid(). Already created by migration 001 on
-- Supabase, but kept here so this file is standalone-runnable.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- 3.1 — ALTER farm_plots: mango-specific columns
-- ---------------------------------------------------------------------------
ALTER TABLE farm_plots
  ADD COLUMN IF NOT EXISTS bearing_year text DEFAULT 'UNKNOWN',
  ADD COLUMN IF NOT EXISTS bearing_confidence numeric,
  ADD COLUMN IF NOT EXISTS last_bearing_detection_date date,
  ADD COLUMN IF NOT EXISTS flowering_detected boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS flowering_detected_date date,
  ADD COLUMN IF NOT EXISTS fruit_set_detected boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS fruit_set_detected_date date,
  ADD COLUMN IF NOT EXISTS crop_region text,
  ADD COLUMN IF NOT EXISTS tree_count integer,
  ADD COLUMN IF NOT EXISTS tree_age_years integer;

-- Idempotent CHECK constraint add for farm_plots.bearing_year.
DO $$ BEGIN
  ALTER TABLE farm_plots
    ADD CONSTRAINT farm_plots_bearing_year_check
    CHECK (bearing_year IN ('ON','OFF','UNKNOWN'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- 3.2 — ALTER farmers: region + preferred_language
-- ---------------------------------------------------------------------------
ALTER TABLE farmers
  ADD COLUMN IF NOT EXISTS region text,
  ADD COLUMN IF NOT EXISTS preferred_language text DEFAULT 'Marathi';

-- Normalise legacy ISO-639 two-letter codes (mr/en/hi/kok) into the full
-- names this CHECK constraint expects. TEST seed data uses 'mr', 'en', 'hi'.
UPDATE farmers SET preferred_language = CASE
    WHEN lower(preferred_language) IN ('mr','mar','mr-in','marathi') THEN 'Marathi'
    WHEN lower(preferred_language) IN ('en','eng','en-in','english') THEN 'English'
    WHEN lower(preferred_language) IN ('hi','hin','hindi')           THEN 'Hindi'
    WHEN lower(preferred_language) IN ('kok','konkani')              THEN 'Konkani'
    ELSE preferred_language
END
WHERE preferred_language IS NOT NULL
  AND preferred_language NOT IN ('Marathi','English','Konkani','Hindi');

-- Anything that did NOT match a known code/name is force-defaulted to Marathi
-- so the CHECK constraint can be added cleanly. Log how many were touched.
DO $$
DECLARE n int;
BEGIN
    UPDATE farmers SET preferred_language = 'Marathi'
     WHERE preferred_language NOT IN ('Marathi','English','Konkani','Hindi')
        OR preferred_language IS NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n > 0 THEN
        RAISE NOTICE 'Migration 007: defaulted % farmers.preferred_language values to Marathi.', n;
    END IF;
END $$;

-- Idempotent CHECK constraint add for farmers.preferred_language.
DO $$ BEGIN
  ALTER TABLE farmers
    ADD CONSTRAINT farmers_preferred_language_check
    CHECK (preferred_language IN ('Marathi','English','Konkani','Hindi'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- 3.3 — NEW TABLE: mango_phenology_log
-- Tracks mango-specific crop events per farm per season.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mango_phenology_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plot_id uuid REFERENCES farm_plots(id),
  season_label text NOT NULL,
  bearing_year text CHECK (bearing_year IN ('ON','OFF','UNKNOWN')),
  flowering_start_date date,
  flowering_peak_date date,
  flowering_end_date date,
  flowering_intensity_pct numeric,
  frost_events_count integer DEFAULT 0,
  rain_during_flowering_mm numeric DEFAULT 0,
  fruit_set_date date,
  fruit_set_pct numeric,
  heat_stress_events_count integer DEFAULT 0,
  predicted_yield_kg_per_tree numeric,
  actual_yield_kg_per_tree numeric,
  harvest_start_date date,
  harvest_end_date date,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE(plot_id, season_label)
);

CREATE INDEX IF NOT EXISTS idx_phenology_plot
  ON mango_phenology_log(plot_id);
CREATE INDEX IF NOT EXISTS idx_phenology_season
  ON mango_phenology_log(season_label);

-- ---------------------------------------------------------------------------
-- 3.4 — ALTER price_history_training: mango model features
-- ---------------------------------------------------------------------------
ALTER TABLE price_history_training
  ADD COLUMN IF NOT EXISTS bearing_year_flag integer DEFAULT 0,
  -- 1 = ON year, 0 = OFF year, -1 = unknown
  ADD COLUMN IF NOT EXISTS export_demand_proxy numeric,
  -- USD/INR rate as proxy for export demand
  ADD COLUMN IF NOT EXISTS flowering_weather_score numeric,
  -- 0-100: 100 = perfect flowering weather
  ADD COLUMN IF NOT EXISTS variety text;
  -- for mango: variety-level price data

-- ---------------------------------------------------------------------------
-- 3.5 — NEW TABLE: mango_belt_data
-- Regional harvest forecast for the mango belt
-- (similar to amed_belt_data for grapes).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mango_belt_data (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  region text NOT NULL,
  variety text NOT NULL,
  fetch_date date NOT NULL,
  season_label text,
  total_fields_detected integer,
  total_area_acres numeric,
  bearing_year text,
  harvest_week_start date,
  harvest_week_end date,
  fields_harvesting integer,
  estimated_volume_mt numeric,
  health_pct_good numeric,
  flowering_pct numeric,
  fruit_set_pct numeric,
  data_source text DEFAULT 'AMED',
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mango_belt_region
  ON mango_belt_data(region, variety, fetch_date);

-- ---------------------------------------------------------------------------
-- 7.1 — NEW TABLE: agents (Flutter agent territory master)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  mobile text NOT NULL UNIQUE,
  email text,
  districts text[] NOT NULL,
  region text,
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 4.3 — NEW TABLE: forex_rates (USD/INR proxy for Alphonso export demand)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forex_rates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rate_date date UNIQUE NOT NULL,
  usd_inr_rate numeric NOT NULL,
  source text DEFAULT 'RBI',
  created_at timestamptz DEFAULT now()
);
