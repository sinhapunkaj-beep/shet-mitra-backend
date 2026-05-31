-- ShetMitra Trader Intelligence Migration 006
-- Date: 2026-05-31
-- Target: TEST (euydubpywdsettjywkms)
-- Description: Adds the paid trader-intelligence subscription platform on top
-- of the existing ShetMitra data infrastructure. Creates 6 new tables:
--   * traders                    (subscriber master)
--   * intelligence_reports       (audit/log of every generated report)
--   * report_deliveries          (per-trader delivery state)
--   * trader_queries             (PREMIUM direct-query log)
--   * trader_payments            (Razorpay payment ledger)
--   * flash_alert_triggers       (what fired each flash alert)
-- All statements are idempotent and safe to re-run; CHECK constraints carry
-- named CONSTRAINT clauses so future table renames do not silently collide.

-- Required for gen_random_uuid(). Already created by migration 001 on Supabase,
-- but kept here so this file is standalone-runnable.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- 4.1 — traders: subscriber master record
-- The self-FK on referred_by is fine in Postgres; the SQLite mirror in
-- scripts/seed_local_sqlite.py keeps the column but omits the FK reference.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS traders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name text NOT NULL,
    mobile text NOT NULL UNIQUE,
    business_name text,
    location text,
    district text,
    commodities text[],
    subscription_tier text DEFAULT 'BASIC'
        CONSTRAINT traders_tier_check
        CHECK (subscription_tier IN ('BASIC', 'STANDARD', 'PREMIUM')),
    subscription_status text DEFAULT 'TRIAL'
        CONSTRAINT traders_status_check
        CHECK (subscription_status IN ('TRIAL', 'ACTIVE', 'PAUSED', 'CANCELLED')),
    trial_started_at timestamptz DEFAULT now(),
    trial_ends_at timestamptz DEFAULT now() + INTERVAL '4 weeks',
    subscription_started_at timestamptz,
    subscription_renewed_at timestamptz,
    razorpay_customer_id text,
    razorpay_subscription_id text,
    monthly_amount numeric,
    whatsapp_opted_in boolean DEFAULT true,
    private_group_added boolean DEFAULT false,
    query_count_this_month integer DEFAULT 0,
    is_active boolean DEFAULT true,
    notes text,
    referred_by uuid REFERENCES traders(id),
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_traders_mobile
    ON traders(mobile);
CREATE INDEX IF NOT EXISTS idx_traders_tier
    ON traders(subscription_tier);
CREATE INDEX IF NOT EXISTS idx_traders_status
    ON traders(subscription_status);

-- ---------------------------------------------------------------------------
-- 4.2 — intelligence_reports: every generated report (audit + resend source)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intelligence_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_type text NOT NULL
        CONSTRAINT intelligence_reports_type_check
        CHECK (report_type IN ('WEEKLY', 'FLASH', 'PRE_SEASON', 'DAILY')),
    commodity text NOT NULL,
    region text,
    report_date date NOT NULL,
    report_week integer,
    content_english text NOT NULL,
    signal text
        CONSTRAINT intelligence_reports_signal_check
        CHECK (signal IN (
            'BUY', 'SELL', 'HOLD',
            'IMMEDIATE_BUY', 'SELL_NOW', 'URGENT_HOLD'
        )),
    price_forecast_day1 numeric,
    price_forecast_day3 numeric,
    price_forecast_day7 numeric,
    confidence_pct numeric,
    belt_volume_mt numeric,
    grade_a_pct numeric,
    grade_b_pct numeric,
    grade_c_pct numeric,
    bearing_year text,
    trigger_event text,
    model_version text DEFAULT 'v2',
    recipients_count integer DEFAULT 0,
    delivered_count integer DEFAULT 0,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reports_type_date
    ON intelligence_reports(report_type, report_date);
CREATE INDEX IF NOT EXISTS idx_reports_commodity
    ON intelligence_reports(commodity);

-- ---------------------------------------------------------------------------
-- 4.3 — report_deliveries: per-trader delivery state for each report
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id uuid REFERENCES intelligence_reports(id),
    trader_id uuid REFERENCES traders(id),
    delivered_at timestamptz,
    delivery_status text DEFAULT 'PENDING'
        CONSTRAINT report_deliveries_status_check
        CHECK (delivery_status IN (
            'PENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED'
        )),
    aisensy_message_id text,
    retry_count integer DEFAULT 0,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deliveries_report
    ON report_deliveries(report_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_trader
    ON report_deliveries(trader_id);

-- ---------------------------------------------------------------------------
-- 4.4 — trader_queries: PREMIUM tier direct-query log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trader_queries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trader_id uuid REFERENCES traders(id),
    query_text text NOT NULL,
    query_received_at timestamptz DEFAULT now(),
    response_text text,
    response_sent_at timestamptz,
    model_inputs jsonb,
    created_at timestamptz DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 4.5 — trader_payments: Razorpay payment ledger per trader per month
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trader_payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trader_id uuid REFERENCES traders(id),
    amount numeric NOT NULL,
    currency text DEFAULT 'INR',
    payment_month date NOT NULL,
    razorpay_order_id text,
    razorpay_payment_id text,
    status text DEFAULT 'PENDING'
        CONSTRAINT trader_payments_status_check
        CHECK (status IN ('PENDING', 'PAID', 'FAILED', 'REFUNDED')),
    paid_at timestamptz,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trader_payments_trader
    ON trader_payments(trader_id);

-- ---------------------------------------------------------------------------
-- 4.6 — flash_alert_triggers: log of what fired each flash alert
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flash_alert_triggers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    commodity text NOT NULL,
    trigger_type text NOT NULL,
    trigger_description text,
    price_before numeric,
    price_after numeric,
    arrivals_forecast_mt numeric,
    arrivals_actual_mt numeric,
    alert_sent boolean DEFAULT false,
    report_id uuid REFERENCES intelligence_reports(id),
    detected_at timestamptz DEFAULT now()
);
