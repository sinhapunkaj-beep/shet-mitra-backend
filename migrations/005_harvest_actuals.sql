-- ShetMitra Harvest Outcomes Migration 005
-- Date: 2026-05-31
-- Description: Add the farm_harvest_actuals table that captures end-of-season
-- yield / price / grade / sell-date reported by farmers over WhatsApp, plus
-- the corresponding state-tracking columns on farmers, and extend the
-- whatsapp_sessions.collection_flow CHECK constraint to allow the new
-- 'harvest_actuals' flow value. All statements are idempotent and safe to
-- re-run.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- TASK 1 — farm_harvest_actuals: end-of-season outcome capture
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS farm_harvest_actuals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farmer_id uuid NOT NULL REFERENCES farmers(id),
  plot_id uuid REFERENCES farm_plots(id),
  season_label text NOT NULL,
  crop_type text NOT NULL,
  variety text,
  total_yield_kg numeric,
  yield_per_acre_kg numeric,
  selling_price_inr_per_kg numeric,
  grade text CHECK (grade IN ('A','B','C','MIXED','UNKNOWN')),
  sold_date date,
  buyer_type text,
  reported_via text DEFAULT 'whatsapp'
    CHECK (reported_via IN ('whatsapp','sms','agent','app')),
  amed_predicted_yield_kg numeric,
  amed_predicted_grade text,
  yield_accuracy_pct numeric,
  raw_response jsonb,
  collection_started_at timestamptz,
  collection_completed_at timestamptz,
  status text DEFAULT 'IN_PROGRESS'
    CHECK (status IN ('IN_PROGRESS','COMPLETE','ABANDONED','SKIPPED')),
  created_at timestamptz DEFAULT now(),
  UNIQUE(farmer_id, plot_id, season_label, crop_type)
);

CREATE INDEX IF NOT EXISTS idx_harvest_actuals_farmer
  ON farm_harvest_actuals(farmer_id);
CREATE INDEX IF NOT EXISTS idx_harvest_actuals_season
  ON farm_harvest_actuals(season_label, crop_type);

-- ---------------------------------------------------------------------------
-- TASK 2 — farmers: harvest-collection state-tracking columns
-- ---------------------------------------------------------------------------
ALTER TABLE farmers
  ADD COLUMN IF NOT EXISTS harvest_actuals_collected boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS harvest_actuals_collected_at timestamptz,
  ADD COLUMN IF NOT EXISTS harvest_collection_attempts integer DEFAULT 0,
  ADD COLUMN IF NOT EXISTS harvest_collection_attempted_at timestamptz,
  ADD COLUMN IF NOT EXISTS harvest_collection_status text DEFAULT 'PENDING';

-- Idempotent CHECK constraint add (wrap in DO block so re-runs don't fail)
DO $$ BEGIN
  ALTER TABLE farmers ADD CONSTRAINT farmers_harvest_collection_status_check
    CHECK (harvest_collection_status IN ('PENDING','AWAITING_REPLY','COMPLETE','SKIPPED','FAILED'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- TASK 3 — whatsapp_sessions.collection_flow: allow 'harvest_actuals'
-- Drop and re-add the CHECK if needed (idempotent via DO block).
-- ---------------------------------------------------------------------------
DO $$ BEGIN
  ALTER TABLE whatsapp_sessions DROP CONSTRAINT IF EXISTS whatsapp_sessions_collection_flow_check;
  ALTER TABLE whatsapp_sessions ADD CONSTRAINT whatsapp_sessions_collection_flow_check
    CHECK (collection_flow IN ('booking','variety_collection','registration','harvest_actuals'));
END $$;
