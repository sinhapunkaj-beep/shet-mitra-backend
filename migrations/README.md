# ShetMitra AMED Integration — Database Migrations

This directory contains the Postgres DDL needed to extend the ShetMitra
Supabase schema for the AMED integration (SDD Section 3).

**Target project:** confirmed as ShetMitra TEST
(`euydubpywdsettjywkms.supabase.co`). All migrations in this directory run
against the TEST project; do not run them against any other Supabase
project ref.

## Files (run in numerical order)

1. `001_amed_new_tables.sql` — creates the three new AMED tables:
   `amed_readings`, `amed_belt_data`, `amed_history` (plus required indexes
   and the `pgcrypto` extension for `gen_random_uuid()`).
2. `002_amed_alter_existing.sql` — adds AMED-related columns to the existing
   `farm_plots`, `spray_advisories`, and `price_history_training` tables.
3. `003_seed_amed_history.sql` — seeds `amed_history` with 3 seasons of mock
   Tasgaon/Sangli grape belt data (2022-23, 2023-24, 2024-25).
4. `004_variety_collection.sql` — adds variety-collection columns to `farmers`,
   `farm_plots`, and `whatsapp_sessions`; creates the `variety_responses`
   audit table (plus `whatsapp_sessions` defensively); installs CHECK
   constraints via `DO $$ ... duplicate_object` blocks so re-runs are safe.
5. `005_harvest_actuals.sql` — creates the `farm_harvest_actuals` table that
   captures end-of-season yield / price / grade / sell-date reported by
   farmers; adds the matching `harvest_*` state-tracking columns on
   `farmers`; extends the `whatsapp_sessions.collection_flow` CHECK
   constraint to allow the new `harvest_actuals` flow value.
6. `006_trader_intelligence.sql` — creates the paid trader-intelligence
   subscription platform: `traders`, `intelligence_reports`,
   `report_deliveries`, `trader_queries`, `trader_payments`, and
   `flash_alert_triggers` (with all indexes from SDD Section 4). CHECK
   constraints are added with explicit `CONSTRAINT name CHECK (...)` so
   they can be referenced and re-added safely. The self-FK on
   `traders.referred_by` is fine in Postgres and is added in the same
   CREATE TABLE statement.
7. `007_mango_crop_expansion.sql` — adds mango-crop columns to
   `farm_plots` (bearing_year, flowering_detected, fruit_set_detected,
   tree_count, etc.) and `farmers` (region, preferred_language); creates
   the `mango_phenology_log` and `mango_belt_data` regional tables;
   extends `price_history_training` with `bearing_year_flag`,
   `export_demand_proxy`, `flowering_weather_score`, and `variety`;
   creates the Flutter `agents` territory table and the `forex_rates`
   (USD/INR proxy for Alphonso) table. CHECK constraint adds on the two
   pre-existing tables (`farm_plots.bearing_year`,
   `farmers.preferred_language`) are wrapped in `DO $$ ... duplicate_object`
   blocks so re-runs are safe.

## How to apply

The migrations are written for the Supabase SQL editor:

1. Open https://supabase.com/dashboard and select the ShetMitra project.
2. Go to **SQL editor** in the sidebar.
3. Open `001_amed_new_tables.sql`, copy its contents into a new query, and
   click **Run**.
4. Repeat for `002_amed_alter_existing.sql`.
5. Repeat for `003_seed_amed_history.sql`.
6. Repeat for `004_variety_collection.sql`.
7. Repeat for `005_harvest_actuals.sql`.
8. Repeat for `006_trader_intelligence.sql`.
9. Repeat for `007_mango_crop_expansion.sql`.

Every statement is idempotent:

- New tables use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`.
- Column additions use `ADD COLUMN IF NOT EXISTS`.
- The seed insert uses `ON CONFLICT (region, season_label, crop_type) DO NOTHING`.

Running them more than once is safe and will not destroy data.

## Optional: apply via Python helper (dry-run by default)

From the project root:

```
python scripts/apply_migrations.py            # dry-run, prints what would run
python scripts/apply_migrations.py --apply    # actually executes (production DB)
```

Connection params come from `config.DB_CONFIG` and can be overridden with the
environment variables `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.

## Verification queries

After applying, run these in the Supabase SQL editor to confirm everything
landed:

```sql
-- 1. New tables exist
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('amed_readings', 'amed_belt_data', 'amed_history')
ORDER BY table_name;
-- Expect 3 rows.

-- 2. Indexes exist
SELECT indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN ('idx_amed_plot', 'idx_amed_date', 'idx_belt_region_date')
ORDER BY indexname;
-- Expect 3 rows.

-- 3. New columns on farm_plots
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'farm_plots'
  AND column_name IN (
    'amed_crop_verified', 'amed_field_id', 'amed_last_fetch',
    'crop_type_mismatch', 'area_mismatch_pct'
  )
ORDER BY column_name;
-- Expect 5 rows.

-- 4. New columns on spray_advisories
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'spray_advisories'
  AND column_name IN (
    'harvest_window_start', 'harvest_window_end',
    'harvest_source', 'harvest_confidence'
  )
ORDER BY column_name;
-- Expect 4 rows.

-- 5. New columns on price_history_training
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'price_history_training'
  AND column_name IN (
    'amed_belt_volume_mt', 'amed_fields_harvesting',
    'amed_health_pct_good', 'amed_season_week'
  )
ORDER BY column_name;
-- Expect 4 rows.

-- 6. Seed data
SELECT season_label, total_area_acres, estimated_total_volume_mt, avg_price_modal_kg
FROM amed_history
WHERE region = 'Tasgaon_Sangli_belt' AND crop_type = 'Grapes'
ORDER BY season_label;
-- Expect 3 rows: 2022-23 (118.50), 2023-24 (134.20), 2024-25 (298.40).

-- 7. Migration 004 — variety_responses table exists and is empty initially
SELECT COUNT(*) AS variety_responses_rows FROM variety_responses;
-- Expect 0 right after migration; grows as the WhatsApp flow runs.

-- 8. Migration 004 — new columns on farmers
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'farmers'
  AND column_name IN (
    'amed_variety_collected',
    'amed_variety_collected_at',
    'alternate_mobile',
    'variety_collection_attempts',
    'variety_collection_status',
    'variety_collection_attempted_at'
  )
ORDER BY column_name;
-- Expect 6 rows.

-- 9. Migration 004 — new columns on farm_plots (only the variety-specific ones)
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'farm_plots'
  AND column_name IN (
    'current_crop_variety',
    'self_reported_acres',
    'amed_verification_date',
    'variety_source'
  )
ORDER BY column_name;
-- Expect 4 rows.

-- 10. Migration 004 — collection_flow column on whatsapp_sessions
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'whatsapp_sessions'
  AND column_name = 'collection_flow';
-- Expect 1 row.

-- 11. Migration 004 — CHECK constraints landed
SELECT conname
FROM pg_constraint
WHERE conname IN (
  'farmers_variety_collection_status_chk',
  'farm_plots_variety_source_chk',
  'whatsapp_sessions_collection_flow_chk'
)
ORDER BY conname;
-- Expect 3 rows.

-- 12. Migration 005 — farm_harvest_actuals table exists and is empty initially
SELECT COUNT(*) AS harvest_actuals_rows FROM farm_harvest_actuals;
-- Expect 0 right after migration; grows as the harvest-collection flow runs.

-- 13. Migration 005 — new columns on farmers
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'farmers'
  AND column_name IN (
    'harvest_actuals_collected',
    'harvest_actuals_collected_at',
    'harvest_collection_attempts',
    'harvest_collection_attempted_at',
    'harvest_collection_status'
  )
ORDER BY column_name;
-- Expect 5 rows.

-- 14. Migration 005 — whatsapp_sessions.collection_flow now allows harvest_actuals
SELECT pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conname = 'whatsapp_sessions_collection_flow_check';
-- Expect a CHECK definition listing harvest_actuals among the allowed values.

-- 15. Migration 006 — all 6 trader-intelligence tables exist
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'traders',
    'intelligence_reports',
    'report_deliveries',
    'trader_queries',
    'trader_payments',
    'flash_alert_triggers'
  )
ORDER BY table_name;
-- Expect 6 rows.

-- 16. Migration 006 — required indexes exist
SELECT indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
    'idx_traders_mobile',
    'idx_traders_tier',
    'idx_traders_status',
    'idx_reports_type_date',
    'idx_reports_commodity',
    'idx_deliveries_report',
    'idx_deliveries_trader',
    'idx_trader_payments_trader'
  )
ORDER BY indexname;
-- Expect 8 rows.

-- 17. Migration 006 — traders table is empty after fresh migration
SELECT COUNT(*) AS traders_rows FROM traders;
-- Expect 0 right after migration; grows as real subscribers join.

-- 18. Migration 006 — intelligence_reports columns landed
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'intelligence_reports'
ORDER BY column_name;
-- Expect: bearing_year, belt_volume_mt, commodity, confidence_pct,
-- content_english, created_at, delivered_count, grade_a_pct, grade_b_pct,
-- grade_c_pct, id, model_version, price_forecast_day1, price_forecast_day3,
-- price_forecast_day7, recipients_count, region, report_date, report_type,
-- report_week, signal, trigger_event.

-- 19. Migration 006 — CHECK constraints landed (named per the migration)
SELECT conname
FROM pg_constraint
WHERE conname IN (
  'traders_tier_check',
  'traders_status_check',
  'intelligence_reports_type_check',
  'intelligence_reports_signal_check',
  'report_deliveries_status_check',
  'trader_payments_status_check'
)
ORDER BY conname;
-- Expect 6 rows.

-- 20. Migration 007 — all 4 new mango tables exist
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'mango_phenology_log',
    'mango_belt_data',
    'agents',
    'forex_rates'
  )
ORDER BY table_name;
-- Expect 4 rows.

-- 21. Migration 007 — required mango indexes exist
SELECT indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
    'idx_phenology_plot',
    'idx_phenology_season',
    'idx_mango_belt_region'
  )
ORDER BY indexname;
-- Expect 3 rows.

-- 22. Migration 007 — new mango columns on farm_plots
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'farm_plots'
  AND column_name IN (
    'bearing_year',
    'bearing_confidence',
    'last_bearing_detection_date',
    'flowering_detected',
    'flowering_detected_date',
    'fruit_set_detected',
    'fruit_set_detected_date',
    'crop_region',
    'tree_count',
    'tree_age_years'
  )
ORDER BY column_name;
-- Expect 10 rows.

-- 23. Migration 007 — new columns on farmers
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'farmers'
  AND column_name IN ('region', 'preferred_language')
ORDER BY column_name;
-- Expect 2 rows.

-- 24. Migration 007 — new feature columns on price_history_training
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'price_history_training'
  AND column_name IN (
    'bearing_year_flag',
    'export_demand_proxy',
    'flowering_weather_score',
    'variety'
  )
ORDER BY column_name;
-- Expect 4 rows.

-- 25. Migration 007 — CHECK constraints landed on the existing tables
SELECT conname
FROM pg_constraint
WHERE conname IN (
  'farm_plots_bearing_year_check',
  'farmers_preferred_language_check'
)
ORDER BY conname;
-- Expect 2 rows.

-- 26. Migration 007 — agents and forex_rates start empty on the live DB
SELECT (SELECT COUNT(*) FROM agents) AS agents_rows,
       (SELECT COUNT(*) FROM forex_rates) AS forex_rows;
-- Expect 0, 0 right after migration. Agent 1 only seeds these locally
-- in the SQLite mirror; the live agents/forex_rates rows are populated
-- by Agents 2 + 7 (Flutter onboarding + RBI ingestion).
```
