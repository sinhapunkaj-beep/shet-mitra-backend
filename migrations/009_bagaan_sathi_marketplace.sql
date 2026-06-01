-- ShetMitra Bagaan Sathi + Trader-Farmer Connect Marketplace Migration 009
-- Date: 2026-06-01
-- Target: TEST  (https://euydubpywdsettjywkms.supabase.co)
-- Description:
--   Introduces multi-region architecture and the full trader-farmer
--   connect marketplace per SDD Sections 2.1, 2.2 and 4.1.
--
--   - Creates the regions master table and seeds Maharashtra (MH) +
--     Jharkhand (JH) rows (SDD 2.1).
--   - Adds farmers.region_code (default 'MH') with FK to regions
--     (SDD 2.2).
--   - Creates the six marketplace tables: trader_requirements,
--     farmer_lots, lot_matches, lot_aggregations, farmer_trades,
--     lot_bids — plus all indexes listed in SDD 4.1.
--
-- All statements are idempotent and safe to re-run:
--   * Tables use CREATE TABLE IF NOT EXISTS.
--   * Indexes use CREATE INDEX IF NOT EXISTS.
--   * Column additions use ADD COLUMN IF NOT EXISTS.
--   * Seed inserts use ON CONFLICT (region_code) DO NOTHING.
--   * CHECK constraints on freshly created tables are inline so the
--     IF NOT EXISTS guard on CREATE TABLE keeps re-runs safe.
--
-- FK dependencies (must already exist on TEST before this migration):
--   farmers(id)     — pre-existing core table
--   farm_plots(id)  — pre-existing core table (extended by 001, 002,
--                     004, 007)
--   traders(id)     — created by migration 006_trader_intelligence.sql
--
-- ===========================================================================
-- HOW TO APPLY
-- ===========================================================================
-- Option A — Supabase SQL editor (recommended for TEST):
--   1. Open https://supabase.com/dashboard and select the ShetMitra
--      TEST project (ref: euydubpywdsettjywkms).
--   2. Go to SQL editor → New query.
--   3. Paste the contents of this file and click Run.
--
-- Option B — psql against the Supabase Postgres directly:
--   psql "postgres://postgres:<PASSWORD>@db.euydubpywdsettjywkms.supabase.co:5432/postgres" \
--        -f migrations/009_bagaan_sathi_marketplace.sql
--
-- Option C — supabase CLI (project must be linked):
--   supabase db execute --file migrations/009_bagaan_sathi_marketplace.sql
--
-- Option D — project Python helper (dry-run by default):
--   python scripts/apply_migrations.py            # dry-run
--   python scripts/apply_migrations.py --apply    # actually executes
-- ===========================================================================

-- Required for gen_random_uuid(). Already installed by migration 001 on
-- Supabase, but kept here so this file is standalone-runnable.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- 2.1 — NEW TABLE: regions
-- Master table for the multi-region architecture.
-- Each region has its own WhatsApp sender name, default language,
-- AMED bounding box, primary crops, and tracked mandis.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  region_code text UNIQUE NOT NULL,
  region_name text NOT NULL,
  whatsapp_sender_name text NOT NULL,
  default_language text DEFAULT 'Marathi',
  amed_bbox_north numeric,
  amed_bbox_south numeric,
  amed_bbox_east numeric,
  amed_bbox_west numeric,
  primary_crops text[],
  primary_mandis text[],
  is_active boolean DEFAULT true,
  launched_at date,
  created_at timestamptz DEFAULT now()
);

-- Seed the two launch regions: Maharashtra (ShetMitra) and
-- Jharkhand (Bagaan Sathi). Idempotent via the UNIQUE region_code.
INSERT INTO regions
  (region_code, region_name, whatsapp_sender_name, default_language,
   amed_bbox_north, amed_bbox_south, amed_bbox_east, amed_bbox_west,
   primary_crops, primary_mandis, is_active, launched_at)
VALUES
  ('MH', 'Maharashtra', 'ShetMitra', 'Marathi',
   22.0, 15.5, 80.5, 72.5,
   ARRAY['Grapes','Pomegranate','Mango'],
   ARRAY['Tasgaon APMC','Sangli APMC','Solapur APMC',
         'Nashik APMC','Ratnagiri APMC'],
   true, DATE '2026-07-01'),
  ('JH', 'Jharkhand', 'Bagaan Sathi', 'Hindi',
   25.0, 21.5, 87.5, 83.5,
   ARRAY['Mango'],
   ARRAY['Bhagalpur APMC','Ranchi APMC','Malda APMC',
         'Patna APMC','Delhi Azadpur APMC'],
   true, DATE '2027-03-01')
ON CONFLICT (region_code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2.2 — ALTER farmers: add region_code with default 'MH'
-- ---------------------------------------------------------------------------
ALTER TABLE farmers
  ADD COLUMN IF NOT EXISTS region_code text DEFAULT 'MH';

-- Backfill any pre-existing NULL region_code values before adding the FK.
UPDATE farmers SET region_code = 'MH' WHERE region_code IS NULL;

-- Idempotent FK add — guarded so re-runs don't fail.
DO $$ BEGIN
  ALTER TABLE farmers
    ADD CONSTRAINT farmers_region_code_fkey
    FOREIGN KEY (region_code) REFERENCES regions(region_code);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- 4.1.a — NEW TABLE: trader_requirements
-- Traders announce what they want to buy. The matching engine pairs
-- these rows with farmer_lots.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trader_requirements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trader_id uuid REFERENCES traders(id),
  region_code text REFERENCES regions(region_code),
  commodity text NOT NULL,
  variety text,
  quantity_kg_min numeric NOT NULL,
  quantity_kg_max numeric,
  grade text[] DEFAULT ARRAY['A','B'],
  price_per_kg_offered numeric,
  collection_from date NOT NULL,
  collection_to date NOT NULL,
  location_district text,
  location_state text,
  farm_pickup boolean DEFAULT true,
  gi_required boolean DEFAULT false,
  status text DEFAULT 'ACTIVE'
    CHECK (status IN
      ('ACTIVE','FULFILLED','EXPIRED','CANCELLED','PAUSED')),
  matched_count integer DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  expires_at timestamptz DEFAULT now() + INTERVAL '7 days'
);

CREATE INDEX IF NOT EXISTS idx_req_commodity_date
  ON trader_requirements(commodity, collection_from, collection_to);
CREATE INDEX IF NOT EXISTS idx_req_status
  ON trader_requirements(status);

-- ---------------------------------------------------------------------------
-- 4.1.b — NEW TABLE: farmer_lots
-- Auto-generated (or farmer-announced) lots available for sale.
-- The matching engine pairs these with trader_requirements.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS farmer_lots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lot_ref text UNIQUE NOT NULL,
  farmer_id uuid REFERENCES farmers(id),
  plot_id uuid REFERENCES farm_plots(id),
  region_code text REFERENCES regions(region_code),
  commodity text NOT NULL,
  variety text,
  quantity_kg_estimated numeric NOT NULL,
  quantity_kg_min_acceptable numeric,
  grade_predicted text,
  brix_estimated_min numeric,
  brix_estimated_max numeric,
  harvest_date_from date NOT NULL,
  harvest_date_to date NOT NULL,
  farm_district text,
  farm_state text,
  centroid_lat numeric,
  centroid_lng numeric,
  satellite_verified boolean DEFAULT true,
  amed_verified boolean DEFAULT false,
  gi_verified boolean DEFAULT false,
  gi_certificate_ref text,
  min_price_per_kg numeric,
  auto_created boolean DEFAULT true,
  status text DEFAULT 'AVAILABLE'
    CHECK (status IN
      ('AVAILABLE','MATCHED','PARTIALLY_MATCHED',
       'SOLD','EXPIRED','WITHDRAWN')),
  created_at timestamptz DEFAULT now(),
  expires_at timestamptz DEFAULT now() + INTERVAL '14 days'
);

CREATE INDEX IF NOT EXISTS idx_lots_commodity_date
  ON farmer_lots(commodity, harvest_date_from, harvest_date_to);
CREATE INDEX IF NOT EXISTS idx_lots_region_status
  ON farmer_lots(region_code, status);

-- ---------------------------------------------------------------------------
-- 4.1.c — NEW TABLE: lot_matches
-- One row per (lot, requirement) pair produced by the matching engine,
-- with each side's response captured for the negotiation lifecycle.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lot_matches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lot_id uuid REFERENCES farmer_lots(id),
  requirement_id uuid REFERENCES trader_requirements(id),
  farmer_id uuid REFERENCES farmers(id),
  trader_id uuid REFERENCES traders(id),
  match_score numeric,
  match_reasons text[],
  farmer_notified_at timestamptz,
  farmer_response text
    CHECK (farmer_response IN
      ('ACCEPTED','REJECTED','NO_RESPONSE','NEGOTIATING')),
  farmer_counter_price numeric,
  farmer_responded_at timestamptz,
  trader_notified_at timestamptz,
  trader_response text
    CHECK (trader_response IN
      ('ACCEPTED','REJECTED','COUNTER')),
  trader_counter_price numeric,
  connection_made boolean DEFAULT false,
  connection_made_at timestamptz,
  created_at timestamptz DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 4.1.d — NEW TABLE: lot_aggregations
-- Small farmer lots grouped into a single tradeable bundle by the
-- weekly aggregator (SDD 4.3). lot_ids / farmer_ids arrays capture
-- the constituent lots for traceability.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lot_aggregations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  aggregation_ref text UNIQUE NOT NULL,
  region_code text REFERENCES regions(region_code),
  commodity text NOT NULL,
  variety text,
  grade_predicted text,
  harvest_week_start date NOT NULL,
  harvest_week_end date NOT NULL,
  total_quantity_kg numeric,
  farm_count integer,
  lot_ids uuid[],
  farmer_ids uuid[],
  min_price_per_kg numeric,
  status text DEFAULT 'OPEN'
    CHECK (status IN ('OPEN','OFFERED','SOLD','EXPIRED')),
  created_at timestamptz DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 4.1.e — NEW TABLE: farmer_trades
-- Completed trades (both sides confirmed). Source of truth for the
-- platform-fee, premium-achieved, and revenue analytics dashboards.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS farmer_trades (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lot_id uuid REFERENCES farmer_lots(id),
  farmer_id uuid REFERENCES farmers(id),
  trader_id uuid REFERENCES traders(id),
  match_id uuid REFERENCES lot_matches(id),
  region_code text REFERENCES regions(region_code),
  commodity text NOT NULL,
  variety text,
  quantity_kg_actual numeric,
  price_per_kg_actual numeric,
  total_value numeric,
  mandi_price_same_day numeric,
  premium_achieved_pct numeric,
  trade_date date,
  payment_mode text,
  razorpay_payment_id text,
  platform_fee_pct numeric DEFAULT 2.0,
  platform_fee_amount numeric,
  confirmed_by_farmer boolean DEFAULT false,
  confirmed_by_trader boolean DEFAULT false,
  gi_premium_applied boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 4.1.f — NEW TABLE: lot_bids
-- Per-trader bids against a farmer_lot for the Phase-2 reverse auction.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lot_bids (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lot_id uuid REFERENCES farmer_lots(id),
  trader_id uuid REFERENCES traders(id),
  bid_price_per_kg numeric NOT NULL,
  bid_quantity_kg numeric,
  bid_notes text,
  bid_valid_until timestamptz,
  status text DEFAULT 'ACTIVE'
    CHECK (status IN
      ('ACTIVE','WINNING','OUTBID','ACCEPTED',
       'REJECTED','EXPIRED','WITHDRAWN')),
  created_at timestamptz DEFAULT now()
);

-- ===========================================================================
-- VERIFICATION QUERIES (run in Supabase SQL editor after apply)
-- ===========================================================================
--
-- -- 1. regions table seeded
-- SELECT region_code, region_name, whatsapp_sender_name, default_language
-- FROM regions ORDER BY region_code;
-- -- Expect 2 rows: JH (Bagaan Sathi, Hindi), MH (ShetMitra, Marathi).
--
-- -- 2. farmers.region_code column present
-- SELECT column_name, column_default
-- FROM information_schema.columns
-- WHERE table_schema='public' AND table_name='farmers'
--   AND column_name='region_code';
-- -- Expect 1 row with default 'MH'.
--
-- -- 3. All 6 marketplace tables present
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema='public'
--   AND table_name IN
--     ('trader_requirements','farmer_lots','lot_matches',
--      'lot_aggregations','farmer_trades','lot_bids')
-- ORDER BY table_name;
-- -- Expect 6 rows.
--
-- -- 4. Required indexes present
-- SELECT indexname FROM pg_indexes
-- WHERE schemaname='public'
--   AND indexname IN
--     ('idx_req_commodity_date','idx_req_status',
--      'idx_lots_commodity_date','idx_lots_region_status')
-- ORDER BY indexname;
-- -- Expect 4 rows.
