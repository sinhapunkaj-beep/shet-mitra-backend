-- ShetMitra Bagaan Sathi Migration 009c
-- Date: 2026-06-01
-- Target: TEST
-- Description:
--   Reconciles the pre-existing `mandi_config` table (created by an earlier
--   build with 7 Maharashtra rows) with the column set the Bagaan Sathi
--   seed in `009b_mandi_jharkhand_seed.sql` expects.
--
--   The legacy schema only carries
--     [id, mandi_name, district, state, crops, agmarknet_mandi_code,
--      enam_mandi_id, latitude, longitude, has_correspondent,
--      correspondent_name, correspondent_mobile, is_active, created_at]
--
--   The 009b INSERT references seven new columns that don't exist there.
--   This migration adds them in place (ADD COLUMN IF NOT EXISTS) so 009b
--   can be re-applied without further edits. Existing Maharashtra rows
--   are backfilled with sensible defaults (region_code='MH', etc.).
--
--   Apply order:
--     009  ->  009c  ->  009b
--
--   All statements are idempotent and safe to re-run.

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. Extend the regions seed.
--    Migration 009 seeds only MH + JH because those are the *farmer-serving*
--    regions. The Bagaan Sathi mandi seed in 009b references additional
--    mandi-source codes:
--
--      'BR'  — Bihar  (Bhagalpur/Patna/Munger/etc. — primary buyers)
--      'WB'  — West Bengal  (Malda/Kolkata Koley — price setters)
--      'NAT' — National  (Delhi Azadpur, Mumbai Vashi, Hyderabad)
--
--    These regions are not farmer-onboarding regions, so they're seeded
--    with `is_active = false`. The FK on `mandi_config.region_code`
--    (added below) needs them present before 009b runs.
-- ---------------------------------------------------------------------------
INSERT INTO regions (
  region_code, region_name, whatsapp_sender_name,
  default_language, primary_crops, primary_mandis, is_active
) VALUES
  ('BR',  'Bihar',        'ShetMitra', 'Hindi',
   ARRAY['Mango']::text[], ARRAY['Bhagalpur APMC','Patna APMC']::text[], false),
  ('WB',  'West Bengal',  'ShetMitra', 'Hindi',
   ARRAY['Mango']::text[], ARRAY['Malda APMC','Kolkata Koley Market']::text[], false),
  ('NAT', 'National',     'ShetMitra', 'English',
   ARRAY['Mango']::text[], ARRAY['Delhi Azadpur APMC']::text[], false)
ON CONFLICT (region_code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. Add the seven Bagaan Sathi columns to the legacy mandi_config.
-- ---------------------------------------------------------------------------
ALTER TABLE mandi_config
  ADD COLUMN IF NOT EXISTS region_code        text,
  ADD COLUMN IF NOT EXISTS commodity_primary  text,
  ADD COLUMN IF NOT EXISTS varieties_traded   text[],
  ADD COLUMN IF NOT EXISTS role               text,
  ADD COLUMN IF NOT EXISTS is_price_setter    boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS is_gi_specialist   boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS ceda_state_code    text,
  ADD COLUMN IF NOT EXISTS notes              text;

-- ---------------------------------------------------------------------------
-- 2. UNIQUE(mandi_name). The legacy schema lacks this constraint, but
--    009b's seed uses `ON CONFLICT (mandi_name) DO NOTHING` so we need
--    the constraint in place before that file runs. Wrapped in a DO
--    block because Postgres has no `ADD CONSTRAINT IF NOT EXISTS`.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM information_schema.table_constraints
     WHERE table_name = 'mandi_config'
       AND constraint_name = 'mandi_config_mandi_name_key'
  ) THEN
    ALTER TABLE mandi_config
      ADD CONSTRAINT mandi_config_mandi_name_key UNIQUE (mandi_name);
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 3. FK region_code -> regions(region_code). Wrapped in a DO block because
--    Postgres has no `ADD CONSTRAINT IF NOT EXISTS`.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM information_schema.table_constraints
     WHERE table_name = 'mandi_config'
       AND constraint_name = 'mandi_config_region_code_fkey'
  ) THEN
    ALTER TABLE mandi_config
      ADD CONSTRAINT mandi_config_region_code_fkey
      FOREIGN KEY (region_code) REFERENCES regions(region_code);
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 4. Backfill legacy Maharashtra rows so their new columns aren't NULL.
--    Only touch rows whose region_code is still NULL — re-runs are no-ops.
-- ---------------------------------------------------------------------------
UPDATE mandi_config
   SET region_code       = 'MH',
       commodity_primary = COALESCE(commodity_primary, 'Grapes'),
       role              = COALESCE(role, 'hub'),
       ceda_state_code   = COALESCE(ceda_state_code, 'Maharashtra'),
       is_price_setter   = COALESCE(is_price_setter, false),
       is_gi_specialist  = COALESCE(is_gi_specialist, false)
 WHERE region_code IS NULL
   AND state = 'Maharashtra';

-- ---------------------------------------------------------------------------
-- 5. Indexes referenced by 009b. Adding them here is harmless if 009b
--    later re-issues CREATE INDEX IF NOT EXISTS.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_mandi_config_state
  ON mandi_config(state);
CREATE INDEX IF NOT EXISTS idx_mandi_config_region
  ON mandi_config(region_code);
CREATE INDEX IF NOT EXISTS idx_mandi_config_commodity
  ON mandi_config(commodity_primary);

COMMIT;

-- ---------------------------------------------------------------------------
-- Verification (run after applying 009c, then 009b):
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name = 'mandi_config' ORDER BY ordinal_position;
--   -- expect: id, mandi_name, district, state, crops,
--   --         agmarknet_mandi_code, enam_mandi_id, latitude, longitude,
--   --         has_correspondent, correspondent_name, correspondent_mobile,
--   --         is_active, created_at,
--   --         region_code, commodity_primary, varieties_traded, role,
--   --         is_price_setter, is_gi_specialist, ceda_state_code, notes
--
--   SELECT state, COUNT(*) FROM mandi_config GROUP BY state ORDER BY state;
--   -- after 009b:
--   --   Bihar 6, Delhi 1, Jharkhand 7, Maharashtra >=1,
--   --   Telangana 1, West Bengal 4
-- ---------------------------------------------------------------------------
