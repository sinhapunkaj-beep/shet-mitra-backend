-- ShetMitra Bagaan Sathi Migration 009b (companion seed to 009)
-- Date: 2026-06-01
-- Target: TEST
-- Description: Creates the mandi_config table (Agent 1's 009 migration
-- creates the regions/marketplace tables; this 009b file isolates the
-- mandi seed so it can be re-run without touching marketplace DDL) and
-- seeds Jharkhand + Bihar + West Bengal + National mandis for the
-- Bagaan Sathi (Jharkhand) launch.
--
-- See SDD Section 3.1 for the source list. All statements are
-- idempotent and safe to re-run.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- mandi_config — master list of mandis tracked across both regions.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mandi_config (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mandi_name text UNIQUE NOT NULL,
  state text NOT NULL,
  region_code text,                         -- 'MH', 'JH', 'BR', 'WB', 'NAT'
  commodity_primary text,                   -- 'Mango', 'Grapes', etc.
  varieties_traded text[],                  -- e.g. ARRAY['Mallika','Amrapali']
  role text,                                -- buyer / hub / price_setter / specialist
  is_price_setter boolean DEFAULT false,
  is_gi_specialist boolean DEFAULT false,
  ceda_state_code text,                     -- value to pass to CEDA --state
  notes text,
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mandi_config_state
  ON mandi_config(state);
CREATE INDEX IF NOT EXISTS idx_mandi_config_region
  ON mandi_config(region_code);
CREATE INDEX IF NOT EXISTS idx_mandi_config_commodity
  ON mandi_config(commodity_primary);

-- ---------------------------------------------------------------------------
-- JHARKHAND mandis (SDD Section 3.1)
-- ---------------------------------------------------------------------------
INSERT INTO mandi_config (
  mandi_name, state, region_code, commodity_primary,
  varieties_traded, role, is_gi_specialist, ceda_state_code, notes
) VALUES
  ('Ranchi APMC', 'Jharkhand', 'JH', 'Mango',
   ARRAY['Mallika','Amrapali','Jardalu','Himsagar','Langra_JH'],
   'hub', false, 'Jharkhand', 'State capital — mixed varieties'),
  ('Deoghar APMC', 'Jharkhand', 'JH', 'Mango',
   ARRAY['Mallika','Amrapali'],
   'hub', false, 'Jharkhand', 'Mallika + Amrapali volumes'),
  ('Dumka APMC', 'Jharkhand', 'JH', 'Mango',
   ARRAY['Mallika','Amrapali'],
   'hub', false, 'Jharkhand', 'Mallika + Amrapali volumes'),
  ('Godda Mandi', 'Jharkhand', 'JH', 'Mango',
   ARRAY['Jardalu','Langra_JH'],
   'specialist', true, 'Jharkhand', 'Jardalu GI specialist'),
  ('Sahebganj Mandi', 'Jharkhand', 'JH', 'Mango',
   ARRAY['Jardalu','Himsagar'],
   'specialist', true, 'Jharkhand', 'Jardalu + Himsagar'),
  ('Pakur Mandi', 'Jharkhand', 'JH', 'Mango',
   ARRAY['Himsagar','Mallika'],
   'border', false, 'Jharkhand', 'Border trade (WB / Bihar)'),
  ('Hazaribagh Mandi', 'Jharkhand', 'JH', 'Mango',
   ARRAY['Amrapali'],
   'plateau', false, 'Jharkhand', 'Plateau belt')
ON CONFLICT (mandi_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- BIHAR mandis (SDD Section 3.1)
-- ---------------------------------------------------------------------------
INSERT INTO mandi_config (
  mandi_name, state, region_code, commodity_primary,
  varieties_traded, role, is_gi_specialist, ceda_state_code, notes
) VALUES
  ('Bhagalpur APMC', 'Bihar', 'BR', 'Mango',
   ARRAY['Jardalu','Mallika','Amrapali','Langra_JH'],
   'buyer', true, 'Bihar',
   'MOST IMPORTANT — primary buyer for Jardalu GI mangoes; also buys Mallika + Amrapali'),
  ('Munger APMC', 'Bihar', 'BR', 'Mango',
   ARRAY['Mallika','Amrapali','Langra_JH'],
   'volume_buyer', false, 'Bihar', 'Volume buyer'),
  ('Patna APMC', 'Bihar', 'BR', 'Mango',
   ARRAY['Amrapali','Mallika','Langra_JH'],
   'premium_buyer', false, 'Bihar', 'State capital — premium buyer'),
  ('Saharsa APMC', 'Bihar', 'BR', 'Mango',
   ARRAY['Mallika','Amrapali'],
   'border', false, 'Bihar', 'Border with Jharkhand'),
  ('Banka APMC', 'Bihar', 'BR', 'Mango',
   ARRAY['Jardalu'],
   'specialist', true, 'Bihar', 'Jardalu belt'),
  ('Purnea APMC', 'Bihar', 'BR', 'Mango',
   ARRAY['Mallika','Amrapali'],
   'aggregator', false, 'Bihar', 'North Bihar aggregator')
ON CONFLICT (mandi_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- WEST BENGAL mandis (SDD Section 3.1)
-- ---------------------------------------------------------------------------
INSERT INTO mandi_config (
  mandi_name, state, region_code, commodity_primary,
  varieties_traded, role, is_price_setter, ceda_state_code, notes
) VALUES
  ('Malda APMC', 'West Bengal', 'WB', 'Mango',
   ARRAY['Himsagar','Langra_JH','Amrapali'],
   'hub', false, 'West Bengal', 'Major mango trading hub'),
  ('Murshidabad Mandi', 'West Bengal', 'WB', 'Mango',
   ARRAY['Himsagar','Langra_JH'],
   'volume', false, 'West Bengal', 'Volume market'),
  ('Kolkata Koley Market', 'West Bengal', 'WB', 'Mango',
   ARRAY['Himsagar','Amrapali','Langra_JH','Jardalu'],
   'price_setter', true, 'West Bengal',
   'Sets reference price for all Bengal mandis'),
  ('Siliguri Mandi', 'West Bengal', 'WB', 'Mango',
   ARRAY['Himsagar','Amrapali'],
   'distributor', false, 'West Bengal', 'North Bengal distributor')
ON CONFLICT (mandi_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- NATIONAL mandis (SDD Section 3.1)
-- ---------------------------------------------------------------------------
INSERT INTO mandi_config (
  mandi_name, state, region_code, commodity_primary,
  varieties_traded, role, is_price_setter, ceda_state_code, notes
) VALUES
  ('Delhi Azadpur APMC', 'Delhi', 'NAT', 'Mango',
   ARRAY['Mallika','Amrapali','Jardalu','Langra_JH','Dasheri','Alphonso','Kesar'],
   'price_setter', true, 'Delhi', 'National price signal'),
  ('Mumbai Vashi APMC', 'Maharashtra', 'NAT', 'Mango',
   ARRAY['Alphonso','Kesar','Jardalu','Banganapalli'],
   'export_hub', false, 'Maharashtra', 'Export hub'),
  ('Hyderabad Gaddiannaram', 'Telangana', 'NAT', 'Mango',
   ARRAY['Banganapalli','Totapuri','Kesar'],
   'south_signal', false, 'Telangana', 'South India signal')
ON CONFLICT (mandi_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verification helper (run after applying)
--   SELECT state, COUNT(*) FROM mandi_config GROUP BY state ORDER BY state;
--   Expect: Bihar 6, Delhi 1, Jharkhand 7, Maharashtra 1, Telangana 1, West Bengal 4
-- ---------------------------------------------------------------------------
