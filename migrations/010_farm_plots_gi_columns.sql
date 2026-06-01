-- migrations/010_farm_plots_gi_columns.sql
-- SDD §3.3 (Bagaan Sathi GI verification)
--
-- Adds Jardalu GI verification columns to farm_plots so the AMED
-- pipeline can persist verification results alongside the plot itself.
-- Idempotent: every column add uses IF NOT EXISTS.
--
-- Apply via Supabase SQL editor, psql -f, or `python scripts/apply_migrations.py --apply`.

BEGIN;

ALTER TABLE farm_plots
  ADD COLUMN IF NOT EXISTS gi_verified           boolean    DEFAULT false,
  ADD COLUMN IF NOT EXISTS gi_certificate_ref    text,
  ADD COLUMN IF NOT EXISTS gi_verified_at        timestamptz,
  ADD COLUMN IF NOT EXISTS gi_premium_multiplier numeric    DEFAULT 1.0;

CREATE INDEX IF NOT EXISTS idx_farm_plots_gi_verified
  ON farm_plots(gi_verified);

COMMIT;
