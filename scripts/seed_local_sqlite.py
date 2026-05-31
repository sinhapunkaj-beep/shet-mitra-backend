"""
Build a local SQLite mirror of the three new AMED tables for offline testing.

This file lets Agents 3, 4, 5 and the pytest suite run without touching the
production Supabase instance.

Postgres -> SQLite type translation used here:
    uuid          -> TEXT  (Python-side uuid4() instead of gen_random_uuid())
    jsonb         -> TEXT  (callers store json.dumps strings)
    numeric       -> REAL
    integer       -> INTEGER
    boolean       -> INTEGER  (0/1)
    date          -> TEXT  (ISO yyyy-mm-dd)
    timestamptz   -> TEXT  (ISO timestamp)

The schemas match SDD Sections 3.1, 3.2, 3.3. The same 3 amed_history seed
rows from migrations/003_seed_amed_history.sql are inserted.

Usage:
    python scripts/seed_local_sqlite.py                 # uses data/test.db
    python scripts/seed_local_sqlite.py path/to/x.db    # uses custom path
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "test.db"


CREATE_AMED_READINGS = """
CREATE TABLE IF NOT EXISTS amed_readings (
    id TEXT PRIMARY KEY,
    plot_id TEXT,
    fetch_date TEXT NOT NULL,
    crop_type_detected TEXT,
    crop_type_confidence REAL,
    field_size_acres_amed REAL,
    sowing_date TEXT,
    harvest_date_predicted TEXT,
    growth_stage TEXT,
    growth_stage_confidence REAL,
    irrigation_detected INTEGER,
    last_event TEXT,
    last_event_date TEXT,
    data_refresh_date TEXT,
    use_mock INTEGER DEFAULT 1,
    raw_response TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_AMED_BELT_DATA = """
CREATE TABLE IF NOT EXISTS amed_belt_data (
    id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    fetch_date TEXT NOT NULL,
    crop_type TEXT NOT NULL,
    total_fields_detected INTEGER,
    total_area_acres REAL,
    harvest_week_start TEXT,
    harvest_week_end TEXT,
    fields_harvesting INTEGER,
    estimated_volume_mt REAL,
    health_pct_good REAL,
    health_pct_moderate REAL,
    health_pct_stressed REAL,
    health_pct_critical REAL,
    data_refresh_date TEXT,
    raw_response TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_AMED_HISTORY = """
CREATE TABLE IF NOT EXISTS amed_history (
    id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    season_label TEXT NOT NULL,
    season_year_start INTEGER,
    crop_type TEXT,
    total_area_acres REAL,
    harvest_start_date TEXT,
    harvest_peak_date TEXT,
    harvest_end_date TEXT,
    estimated_total_volume_mt REAL,
    avg_price_modal_kg REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (region, season_label, crop_type)
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_amed_plot ON amed_readings(plot_id);",
    "CREATE INDEX IF NOT EXISTS idx_amed_date ON amed_readings(fetch_date);",
    "CREATE INDEX IF NOT EXISTS idx_belt_region_date ON amed_belt_data(region, fetch_date);",
]


# ---------------------------------------------------------------------------
# Variety collection schema (migration 004).
#
# These are the SQLite mirrors of the Supabase tables that the variety
# collection flow needs. The live Supabase already has farmers / farm_plots /
# spray_advisories / whatsapp_sessions, but our local SQLite mirror does not,
# so we create minimal compatible versions here for downstream agents to test
# against.
#
# Type translation:
#   uuid         -> TEXT  (Python supplies uuid4())
#   jsonb        -> TEXT  (callers store json.dumps strings)
#   numeric      -> REAL
#   boolean      -> INTEGER  (0/1)
#   date / tstz  -> TEXT  (ISO)
# ---------------------------------------------------------------------------

CREATE_FARMERS = """
CREATE TABLE IF NOT EXISTS farmers (
    id TEXT PRIMARY KEY,
    farmer_full_name TEXT,
    mobile_number TEXT UNIQUE,
    village TEXT,
    taluka TEXT,
    district TEXT,
    amed_variety_collected INTEGER DEFAULT 0,
    amed_variety_collected_at TEXT,
    alternate_mobile TEXT,
    variety_collection_attempts INTEGER DEFAULT 0,
    variety_collection_status TEXT DEFAULT 'PENDING'
        CHECK (variety_collection_status IN (
            'PENDING',
            'AWAITING_REPLY',
            'COMPLETE',
            'FAILED',
            'AGENT_REQUIRED'
        )),
    variety_collection_attempted_at TEXT,
    harvest_actuals_collected INTEGER DEFAULT 0,
    harvest_actuals_collected_at TEXT,
    harvest_collection_attempts INTEGER DEFAULT 0,
    harvest_collection_attempted_at TEXT,
    harvest_collection_status TEXT DEFAULT 'PENDING'
        CHECK (harvest_collection_status IN (
            'PENDING',
            'AWAITING_REPLY',
            'COMPLETE',
            'SKIPPED',
            'FAILED'
        )),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_FARM_PLOTS = """
CREATE TABLE IF NOT EXISTS farm_plots (
    id TEXT PRIMARY KEY,
    farmer_id TEXT REFERENCES farmers(id),
    current_crop TEXT,
    current_crop_variety TEXT,
    self_reported_acres REAL,
    amed_crop_verified INTEGER DEFAULT 0,
    amed_verification_date TEXT,
    amed_field_id TEXT,
    amed_last_fetch TEXT,
    crop_type_mismatch INTEGER DEFAULT 0,
    area_mismatch_pct REAL,
    variety_source TEXT DEFAULT 'farmer_reported'
        CHECK (variety_source IN (
            'farmer_reported',
            'agent_verified',
            'amed_hint'
        )),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_SPRAY_ADVISORIES = """
CREATE TABLE IF NOT EXISTS spray_advisories (
    id TEXT PRIMARY KEY,
    plot_id TEXT REFERENCES farm_plots(id),
    advisory_date TEXT,
    advisory_text TEXT,
    harvest_window_start TEXT,
    harvest_window_end TEXT,
    harvest_source TEXT DEFAULT 'ndvi_estimate',
    harvest_confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_WHATSAPP_SESSIONS = """
CREATE TABLE IF NOT EXISTS whatsapp_sessions (
    id TEXT PRIMARY KEY,
    mobile_number TEXT NOT NULL UNIQUE,
    farmer_id TEXT REFERENCES farmers(id),
    current_step TEXT,
    collection_flow TEXT DEFAULT 'booking'
        CHECK (collection_flow IN (
            'booking',
            'variety_collection',
            'registration',
            'harvest_actuals'
        )),
    session_data TEXT DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT
);
"""

CREATE_VARIETY_RESPONSES = """
CREATE TABLE IF NOT EXISTS variety_responses (
    id TEXT PRIMARY KEY,
    farmer_id TEXT REFERENCES farmers(id),
    plot_id TEXT REFERENCES farm_plots(id),
    amed_crop_detected TEXT,
    amed_confidence REAL,
    variety_reported TEXT,
    name_confirmed TEXT,
    phone_confirmed TEXT,
    village_confirmed TEXT,
    acres_reported REAL,
    acres_mismatch_pct REAL,
    mismatch_resolution TEXT,
    collection_started_at TEXT,
    collection_completed_at TEXT,
    status TEXT DEFAULT 'IN_PROGRESS'
        CHECK (status IN (
            'IN_PROGRESS',
            'COMPLETE',
            'ABANDONED',
            'AGENT_REQUIRED'
        )),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_VARIETY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_whatsapp_sessions_mobile ON whatsapp_sessions(mobile_number);",
    "CREATE INDEX IF NOT EXISTS idx_whatsapp_sessions_farmer ON whatsapp_sessions(farmer_id);",
    "CREATE INDEX IF NOT EXISTS idx_variety_farmer ON variety_responses(farmer_id);",
    "CREATE INDEX IF NOT EXISTS idx_variety_status ON variety_responses(status);",
]


# ---------------------------------------------------------------------------
# Harvest actuals schema (migration 005).
#
# SQLite mirror of the Postgres farm_harvest_actuals table that captures
# end-of-season yield / price / grade / sell-date reported by farmers.
#
# Type translation matches the existing pattern:
#   uuid         -> TEXT
#   jsonb        -> TEXT  (callers store json.dumps strings)
#   numeric      -> REAL
#   boolean      -> INTEGER (0/1)
#   date / tstz  -> TEXT  (ISO)
# ---------------------------------------------------------------------------

CREATE_FARM_HARVEST_ACTUALS = """
CREATE TABLE IF NOT EXISTS farm_harvest_actuals (
    id TEXT PRIMARY KEY,
    farmer_id TEXT NOT NULL REFERENCES farmers(id),
    plot_id TEXT REFERENCES farm_plots(id),
    season_label TEXT NOT NULL,
    crop_type TEXT NOT NULL,
    variety TEXT,
    total_yield_kg REAL,
    yield_per_acre_kg REAL,
    selling_price_inr_per_kg REAL,
    grade TEXT CHECK (grade IN ('A','B','C','MIXED','UNKNOWN')),
    sold_date TEXT,
    buyer_type TEXT,
    reported_via TEXT DEFAULT 'whatsapp'
        CHECK (reported_via IN ('whatsapp','sms','agent','app')),
    amed_predicted_yield_kg REAL,
    amed_predicted_grade TEXT,
    yield_accuracy_pct REAL,
    raw_response TEXT,
    collection_started_at TEXT,
    collection_completed_at TEXT,
    status TEXT DEFAULT 'IN_PROGRESS'
        CHECK (status IN ('IN_PROGRESS','COMPLETE','ABANDONED','SKIPPED')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (farmer_id, plot_id, season_label, crop_type)
);
"""

CREATE_HARVEST_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_harvest_actuals_farmer ON farm_harvest_actuals(farmer_id);",
    "CREATE INDEX IF NOT EXISTS idx_harvest_actuals_season ON farm_harvest_actuals(season_label, crop_type);",
]


# ---------------------------------------------------------------------------
# Trader intelligence schema (migration 006).
#
# SQLite mirrors of the 6 trader-platform tables defined in SDD Section 4.
# Type translation matches the existing pattern:
#   uuid         -> TEXT
#   jsonb        -> TEXT  (callers store json.dumps strings)
#   numeric      -> REAL
#   integer      -> INTEGER
#   boolean      -> INTEGER (0/1)
#   date / tstz  -> TEXT  (ISO)
#   text[]       -> TEXT  (JSON-encoded list)
#
# TODO: the self-FK on traders.referred_by exists in the Postgres migration
# but is intentionally omitted here. SQLite cannot declare a self-FK in the
# same CREATE TABLE statement reliably, and an ALTER TABLE ADD CONSTRAINT
# is not supported. The column is preserved so callers can still write to it.
# ---------------------------------------------------------------------------

CREATE_TRADERS = """
CREATE TABLE IF NOT EXISTS traders (
    id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    mobile TEXT NOT NULL UNIQUE,
    business_name TEXT,
    location TEXT,
    district TEXT,
    commodities TEXT,
    subscription_tier TEXT DEFAULT 'BASIC'
        CHECK (subscription_tier IN ('BASIC', 'STANDARD', 'PREMIUM')),
    subscription_status TEXT DEFAULT 'TRIAL'
        CHECK (subscription_status IN ('TRIAL', 'ACTIVE', 'PAUSED', 'CANCELLED')),
    trial_started_at TEXT,
    trial_ends_at TEXT,
    subscription_started_at TEXT,
    subscription_renewed_at TEXT,
    razorpay_customer_id TEXT,
    razorpay_subscription_id TEXT,
    monthly_amount REAL,
    whatsapp_opted_in INTEGER DEFAULT 1,
    private_group_added INTEGER DEFAULT 0,
    query_count_this_month INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    notes TEXT,
    referred_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_INTELLIGENCE_REPORTS = """
CREATE TABLE IF NOT EXISTS intelligence_reports (
    id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL
        CHECK (report_type IN ('WEEKLY', 'FLASH', 'PRE_SEASON', 'DAILY')),
    commodity TEXT NOT NULL,
    region TEXT,
    report_date TEXT NOT NULL,
    report_week INTEGER,
    content_english TEXT NOT NULL,
    signal TEXT
        CHECK (signal IN (
            'BUY', 'SELL', 'HOLD',
            'IMMEDIATE_BUY', 'SELL_NOW', 'URGENT_HOLD'
        )),
    price_forecast_day1 REAL,
    price_forecast_day3 REAL,
    price_forecast_day7 REAL,
    confidence_pct REAL,
    belt_volume_mt REAL,
    grade_a_pct REAL,
    grade_b_pct REAL,
    grade_c_pct REAL,
    bearing_year TEXT,
    trigger_event TEXT,
    model_version TEXT DEFAULT 'v2',
    recipients_count INTEGER DEFAULT 0,
    delivered_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_REPORT_DELIVERIES = """
CREATE TABLE IF NOT EXISTS report_deliveries (
    id TEXT PRIMARY KEY,
    report_id TEXT REFERENCES intelligence_reports(id),
    trader_id TEXT REFERENCES traders(id),
    delivered_at TEXT,
    delivery_status TEXT DEFAULT 'PENDING'
        CHECK (delivery_status IN (
            'PENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED'
        )),
    aisensy_message_id TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_TRADER_QUERIES = """
CREATE TABLE IF NOT EXISTS trader_queries (
    id TEXT PRIMARY KEY,
    trader_id TEXT REFERENCES traders(id),
    query_text TEXT NOT NULL,
    query_received_at TEXT DEFAULT CURRENT_TIMESTAMP,
    response_text TEXT,
    response_sent_at TEXT,
    model_inputs TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_TRADER_PAYMENTS = """
CREATE TABLE IF NOT EXISTS trader_payments (
    id TEXT PRIMARY KEY,
    trader_id TEXT REFERENCES traders(id),
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'INR',
    payment_month TEXT NOT NULL,
    razorpay_order_id TEXT,
    razorpay_payment_id TEXT,
    status TEXT DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PAID', 'FAILED', 'REFUNDED')),
    paid_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_FLASH_ALERT_TRIGGERS = """
CREATE TABLE IF NOT EXISTS flash_alert_triggers (
    id TEXT PRIMARY KEY,
    commodity TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_description TEXT,
    price_before REAL,
    price_after REAL,
    arrivals_forecast_mt REAL,
    arrivals_actual_mt REAL,
    alert_sent INTEGER DEFAULT 0,
    report_id TEXT REFERENCES intelligence_reports(id),
    detected_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_TRADER_INTELLIGENCE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_traders_mobile ON traders(mobile);",
    "CREATE INDEX IF NOT EXISTS idx_traders_tier ON traders(subscription_tier);",
    "CREATE INDEX IF NOT EXISTS idx_traders_status ON traders(subscription_status);",
    "CREATE INDEX IF NOT EXISTS idx_reports_type_date ON intelligence_reports(report_type, report_date);",
    "CREATE INDEX IF NOT EXISTS idx_reports_commodity ON intelligence_reports(commodity);",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_report ON report_deliveries(report_id);",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_trader ON report_deliveries(trader_id);",
    "CREATE INDEX IF NOT EXISTS idx_trader_payments_trader ON trader_payments(trader_id);",
]


# ---------------------------------------------------------------------------
# Mango crop expansion schema (migration 007).
#
# SQLite mirrors of the 4 new mango tables defined in SDD Section 3 +
# Section 7.1 + Section 4.3:
#   * mango_phenology_log    (3.3)
#   * mango_belt_data        (3.5)
#   * agents                 (7.1)
#   * forex_rates            (4.3)
#
# Plus column-level ALTERs for the existing farmers / farm_plots /
# price_history_training tables (3.1, 3.2, 3.4). SQLite cannot add CHECK
# constraints via ALTER, so the bearing_year and preferred_language CHECKs
# are enforced application-side; the columns themselves are added with
# the documented defaults.
#
# Type translation matches the existing pattern:
#   uuid         -> TEXT
#   numeric      -> REAL
#   integer      -> INTEGER
#   boolean      -> INTEGER (0/1)
#   date / tstz  -> TEXT  (ISO)
#   text[]       -> TEXT  (JSON-encoded list)
# ---------------------------------------------------------------------------

CREATE_MANGO_PHENOLOGY_LOG = """
CREATE TABLE IF NOT EXISTS mango_phenology_log (
    id TEXT PRIMARY KEY,
    plot_id TEXT REFERENCES farm_plots(id),
    season_label TEXT NOT NULL,
    bearing_year TEXT CHECK (bearing_year IN ('ON','OFF','UNKNOWN')),
    flowering_start_date TEXT,
    flowering_peak_date TEXT,
    flowering_end_date TEXT,
    flowering_intensity_pct REAL,
    frost_events_count INTEGER DEFAULT 0,
    rain_during_flowering_mm REAL DEFAULT 0,
    fruit_set_date TEXT,
    fruit_set_pct REAL,
    heat_stress_events_count INTEGER DEFAULT 0,
    predicted_yield_kg_per_tree REAL,
    actual_yield_kg_per_tree REAL,
    harvest_start_date TEXT,
    harvest_end_date TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (plot_id, season_label)
);
"""

CREATE_MANGO_BELT_DATA = """
CREATE TABLE IF NOT EXISTS mango_belt_data (
    id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    variety TEXT NOT NULL,
    fetch_date TEXT NOT NULL,
    season_label TEXT,
    total_fields_detected INTEGER,
    total_area_acres REAL,
    bearing_year TEXT,
    harvest_week_start TEXT,
    harvest_week_end TEXT,
    fields_harvesting INTEGER,
    estimated_volume_mt REAL,
    health_pct_good REAL,
    flowering_pct REAL,
    fruit_set_pct REAL,
    data_source TEXT DEFAULT 'AMED',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_AGENTS = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mobile TEXT NOT NULL UNIQUE,
    email TEXT,
    districts TEXT NOT NULL,
    region TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_FOREX_RATES = """
CREATE TABLE IF NOT EXISTS forex_rates (
    id TEXT PRIMARY KEY,
    rate_date TEXT UNIQUE NOT NULL,
    usd_inr_rate REAL NOT NULL,
    source TEXT DEFAULT 'RBI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_MANGO_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_phenology_plot ON mango_phenology_log(plot_id);",
    "CREATE INDEX IF NOT EXISTS idx_phenology_season ON mango_phenology_log(season_label);",
    "CREATE INDEX IF NOT EXISTS idx_mango_belt_region ON mango_belt_data(region, variety, fetch_date);",
]


# Columns added by migration 007 to the pre-existing tables. ALTER each only
# when the column is missing; SQLite cannot declare CHECK in ALTER so the
# bearing_year and preferred_language value lists are enforced app-side.
_FARMERS_MANGO_COLUMNS = (
    ("region", "TEXT"),
    ("preferred_language", "TEXT DEFAULT 'Marathi'"),
)

_FARM_PLOTS_MANGO_COLUMNS = (
    ("bearing_year", "TEXT DEFAULT 'UNKNOWN'"),
    ("bearing_confidence", "REAL"),
    ("last_bearing_detection_date", "TEXT"),
    ("flowering_detected", "INTEGER DEFAULT 0"),
    ("flowering_detected_date", "TEXT"),
    ("fruit_set_detected", "INTEGER DEFAULT 0"),
    ("fruit_set_detected_date", "TEXT"),
    ("crop_region", "TEXT"),
    ("tree_count", "INTEGER"),
    ("tree_age_years", "INTEGER"),
)

_PRICE_HISTORY_TRAINING_MANGO_COLUMNS = (
    ("bearing_year_flag", "INTEGER DEFAULT 0"),
    ("export_demand_proxy", "REAL"),
    ("flowering_weather_score", "REAL"),
    ("variety", "TEXT"),
)


# Stable IDs so the same seed run is deterministic across test invocations.
AGENT_TASGAON_ID = "a1111111-1111-1111-1111-111111111111"
AGENT_KONKAN_ID = "a2222222-2222-2222-2222-222222222222"
AGENT_NASHIK_ID = "a3333333-3333-3333-3333-333333333333"
AGENT_VIDARBHA_ID = "a4444444-4444-4444-4444-444444444444"

SEED_AGENTS = [
    {
        "id": AGENT_TASGAON_ID,
        "name": "Vikram Patil",
        "mobile": "9871100001",
        "email": None,
        "districts": ["Sangli", "Kolhapur", "Satara"],
        "region": "West Maharashtra",
    },
    {
        "id": AGENT_KONKAN_ID,
        "name": "Nitin Pawar",
        "mobile": "9871100002",
        "email": None,
        "districts": ["Ratnagiri", "Sindhudurg", "Raigad"],
        "region": "Konkan",
    },
    {
        "id": AGENT_NASHIK_ID,
        "name": "Sachin Joshi",
        "mobile": "9871100003",
        "email": None,
        "districts": ["Nashik", "Aurangabad", "Jalna"],
        "region": "Marathwada",
    },
    {
        "id": AGENT_VIDARBHA_ID,
        "name": "Manoj Deshpande",
        "mobile": "9871100004",
        "email": None,
        "districts": ["Nagpur", "Amravati", "Wardha"],
        "region": "Vidarbha",
    },
]


# Stable UUIDs so the same seed run is deterministic across test invocations.
SEED_TRADER_BASIC_ID = "t1111111-1111-1111-1111-111111111111"
SEED_TRADER_STANDARD_ID = "t2222222-2222-2222-2222-222222222222"
SEED_TRADER_PREMIUM_ID = "t3333333-3333-3333-3333-333333333333"
SEED_TRADER_TRIAL_STANDARD_ID = "t4444444-4444-4444-4444-444444444444"
SEED_TRADER_TRIAL_BASIC_ID = "t5555555-5555-5555-5555-555555555555"

SEED_TRADER_MOBILES = {
    SEED_TRADER_BASIC_ID: "9870000001",
    SEED_TRADER_STANDARD_ID: "9870000002",
    SEED_TRADER_PREMIUM_ID: "9870000003",
    SEED_TRADER_TRIAL_STANDARD_ID: "9870000004",
    SEED_TRADER_TRIAL_BASIC_ID: "9870000005",
}


def _trader_seed_rows() -> List[dict]:
    """Build the 5 deterministic test trader rows.

    Trial traders get trial_ends_at computed as now + 4 weeks Python-side
    (Postgres-side default is INTERVAL '4 weeks' on the live DB).
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    trial_end_iso = (now + timedelta(weeks=4)).isoformat()
    return [
        {
            "id": SEED_TRADER_BASIC_ID,
            "full_name": "Ramesh Pawar",
            "mobile": "9870000001",
            "business_name": "Tasgaon APMC",
            "location": "Tasgaon",
            "district": "Tasgaon",
            "commodities": json.dumps(["Dry Grapes"]),
            "subscription_tier": "BASIC",
            "subscription_status": "ACTIVE",
            "trial_started_at": now_iso,
            "trial_ends_at": trial_end_iso,
            "subscription_started_at": now_iso,
            "monthly_amount": 3000.0,
        },
        {
            "id": SEED_TRADER_STANDARD_ID,
            "full_name": "Suresh Bhandari",
            "mobile": "9870000002",
            "business_name": "Bhandari & Sons Exports",
            "location": "Nashik",
            "district": "Nashik",
            "commodities": json.dumps(["Dry Grapes", "Pomegranate"]),
            "subscription_tier": "STANDARD",
            "subscription_status": "ACTIVE",
            "trial_started_at": now_iso,
            "trial_ends_at": trial_end_iso,
            "subscription_started_at": now_iso,
            "monthly_amount": 7000.0,
        },
        {
            "id": SEED_TRADER_PREMIUM_ID,
            "full_name": "Vijay Joshi",
            "mobile": "9870000003",
            "business_name": "V Joshi Exporters",
            "location": "Ratnagiri",
            "district": "Ratnagiri",
            "commodities": json.dumps(["Mango", "Alphonso"]),
            "subscription_tier": "PREMIUM",
            "subscription_status": "ACTIVE",
            "trial_started_at": now_iso,
            "trial_ends_at": trial_end_iso,
            "subscription_started_at": now_iso,
            "monthly_amount": 15000.0,
        },
        {
            "id": SEED_TRADER_TRIAL_STANDARD_ID,
            "full_name": "Anita Deshmukh",
            "mobile": "9870000004",
            "business_name": "Deshmukh Trading",
            "location": "Aurangabad",
            "district": "Aurangabad",
            "commodities": json.dumps(["Mango", "Kesar"]),
            "subscription_tier": "STANDARD",
            "subscription_status": "TRIAL",
            "trial_started_at": now_iso,
            "trial_ends_at": trial_end_iso,
            "subscription_started_at": None,
            "monthly_amount": 0.0,
        },
        {
            "id": SEED_TRADER_TRIAL_BASIC_ID,
            "full_name": "Prakash Patil",
            "mobile": "9870000005",
            "business_name": "Patil Bros",
            "location": "Solapur",
            "district": "Solapur",
            "commodities": json.dumps(["Pomegranate"]),
            "subscription_tier": "BASIC",
            "subscription_status": "TRIAL",
            "trial_started_at": now_iso,
            "trial_ends_at": trial_end_iso,
            "subscription_started_at": None,
            "monthly_amount": 0.0,
        },
    ]


# Columns added to farmers by migration 005. Used to ALTER existing local
# tables that pre-date this migration.
_FARMER_HARVEST_COLUMNS = (
    ("harvest_actuals_collected", "INTEGER DEFAULT 0"),
    ("harvest_actuals_collected_at", "TEXT"),
    ("harvest_collection_attempts", "INTEGER DEFAULT 0"),
    ("harvest_collection_attempted_at", "TEXT"),
    ("harvest_collection_status", "TEXT DEFAULT 'PENDING'"),
)


# Stable UUIDs so the same seed run is deterministic across test invocations.
SEED_FARMER_GRAPES_ID = "11111111-1111-1111-1111-111111111111"
SEED_FARMER_POMEGRANATE_ID = "22222222-2222-2222-2222-222222222222"
SEED_PLOT_GRAPES_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SEED_PLOT_POMEGRANATE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SEED_SESSION_GRAPES_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

SEED_FARMERS = [
    {
        "id": SEED_FARMER_GRAPES_ID,
        "farmer_full_name": "Ramesh Patil",
        "mobile_number": "9876543210",
        "village": "Tasgaon",
        "taluka": "Tasgaon",
        "district": "Sangli",
    },
    {
        "id": SEED_FARMER_POMEGRANATE_ID,
        "farmer_full_name": "Suresh Jadhav",
        "mobile_number": "9876543211",
        "village": "Mohol",
        "taluka": "Mohol",
        "district": "Solapur",
    },
]

SEED_PLOTS = [
    {
        "id": SEED_PLOT_GRAPES_ID,
        "farmer_id": SEED_FARMER_GRAPES_ID,
        "current_crop": "Grapes",
    },
    {
        "id": SEED_PLOT_POMEGRANATE_ID,
        "farmer_id": SEED_FARMER_POMEGRANATE_ID,
        "current_crop": "Pomegranate",
    },
]


# (season_label, season_year_start, total_area_acres,
#  harvest_start, harvest_peak, harvest_end,
#  estimated_total_volume_mt, avg_price_modal_kg)
SEED_HISTORY: List[Tuple[str, int, float, str, str, str, float, float]] = [
    ("2022-23", 2022, 7890.0, "2023-04-01", "2023-04-21", "2023-05-15", 11420.0, 118.50),
    ("2023-24", 2023, 8012.0, "2024-04-05", "2024-04-19", "2024-05-10", 11680.0, 134.20),
    ("2024-25", 2024, 8180.0, "2025-03-28", "2025-04-14", "2025-05-08", 11890.0, 298.40),
]

REGION = "Tasgaon_Sangli_belt"
CROP = "Grapes"


def _create_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(CREATE_AMED_READINGS)
    cur.execute(CREATE_AMED_BELT_DATA)
    cur.execute(CREATE_AMED_HISTORY)
    for stmt in CREATE_INDEXES:
        cur.execute(stmt)
    conn.commit()


def ensure_variety_collection_schema(conn: sqlite3.Connection) -> None:
    """Create the local-SQLite mirrors of farmers / farm_plots /
    spray_advisories / whatsapp_sessions / variety_responses needed for the
    variety collection flow (migration 004).

    Safe to call repeatedly: every CREATE uses IF NOT EXISTS.
    """
    cur = conn.cursor()
    cur.execute(CREATE_FARMERS)
    cur.execute(CREATE_FARM_PLOTS)
    cur.execute(CREATE_SPRAY_ADVISORIES)
    cur.execute(CREATE_WHATSAPP_SESSIONS)
    cur.execute(CREATE_VARIETY_RESPONSES)
    for stmt in CREATE_VARIETY_INDEXES:
        cur.execute(stmt)
    conn.commit()


def ensure_harvest_actuals_schema(conn: sqlite3.Connection) -> None:
    """Create / extend the SQLite mirror of migration 005.

    Creates the ``farm_harvest_actuals`` table and patches the existing
    ``farmers`` table with the migration-005 state-tracking columns when
    they are missing (legacy DBs that pre-date this migration).

    Safe to call repeatedly: every CREATE uses IF NOT EXISTS and the
    ALTER calls are guarded by a PRAGMA-table_info check.
    """
    cur = conn.cursor()
    cur.execute(CREATE_FARM_HARVEST_ACTUALS)
    for stmt in CREATE_HARVEST_INDEXES:
        cur.execute(stmt)

    # If the farmers table already exists without the migration-005
    # columns, add them in place. SQLite ignores DEFAULT on ALTER TABLE
    # ADD COLUMN for some old versions; we wrap each ALTER in try/except.
    cur.execute("PRAGMA table_info(farmers);")
    existing = {row[1] for row in cur.fetchall()}
    for col_name, col_decl in _FARMER_HARVEST_COLUMNS:
        if col_name in existing:
            continue
        try:
            cur.execute(f"ALTER TABLE farmers ADD COLUMN {col_name} {col_decl};")
        except sqlite3.OperationalError:
            # Older SQLite versions can reject DEFAULT on ALTER TABLE;
            # retry without the DEFAULT clause.
            bare = col_decl.split(" DEFAULT ")[0]
            try:
                cur.execute(
                    f"ALTER TABLE farmers ADD COLUMN {col_name} {bare};"
                )
            except sqlite3.OperationalError:
                pass
    conn.commit()


def ensure_trader_intelligence_schema(conn: sqlite3.Connection) -> None:
    """Create the local-SQLite mirrors of the 6 trader-platform tables
    introduced by migration 006.

    Safe to call repeatedly: every CREATE uses IF NOT EXISTS.

    Notes:
        * traders.referred_by is preserved as a TEXT column but the self-FK
          to traders(id) from the Postgres migration is intentionally NOT
          enforced here (SQLite cannot add a self-FK after CREATE TABLE).
        * Postgres array columns (text[]) and jsonb columns are stored as
          TEXT containing JSON-encoded payloads; the Pythonside seed encodes
          via json.dumps and downstream readers should json.loads.
    """
    cur = conn.cursor()
    cur.execute(CREATE_TRADERS)
    cur.execute(CREATE_INTELLIGENCE_REPORTS)
    cur.execute(CREATE_REPORT_DELIVERIES)
    cur.execute(CREATE_TRADER_QUERIES)
    cur.execute(CREATE_TRADER_PAYMENTS)
    cur.execute(CREATE_FLASH_ALERT_TRIGGERS)
    for stmt in CREATE_TRADER_INTELLIGENCE_INDEXES:
        cur.execute(stmt)
    conn.commit()


def _add_missing_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: Iterable[Tuple[str, str]],
) -> None:
    """ALTER ``table`` to add any of ``columns`` that are missing.

    SQLite's ALTER TABLE ADD COLUMN is strict — older versions reject the
    DEFAULT clause, so we retry without DEFAULT on OperationalError.
    No-ops if the table itself does not exist (legacy DBs may skip a table).
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?;",
        (table,),
    )
    if cur.fetchone() is None:
        return
    cur.execute(f"PRAGMA table_info({table});")
    existing = {row[1] for row in cur.fetchall()}
    for col_name, col_decl in columns:
        if col_name in existing:
            continue
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_decl};")
        except sqlite3.OperationalError:
            bare = col_decl.split(" DEFAULT ")[0]
            try:
                cur.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {bare};"
                )
            except sqlite3.OperationalError:
                # If even the bare form fails, skip — the migration is
                # idempotent and the caller is responsible for legacy schemas.
                pass


def ensure_mango_schema(conn: sqlite3.Connection) -> None:
    """Create the local-SQLite mirrors of the 4 new mango tables from
    migration 007 and ALTER farmers / farm_plots / price_history_training
    with the new mango columns.

    Safe to call repeatedly: every CREATE uses IF NOT EXISTS and each
    ALTER is gated by a PRAGMA table_info check.
    """
    cur = conn.cursor()
    cur.execute(CREATE_MANGO_PHENOLOGY_LOG)
    cur.execute(CREATE_MANGO_BELT_DATA)
    cur.execute(CREATE_AGENTS)
    cur.execute(CREATE_FOREX_RATES)
    for stmt in CREATE_MANGO_INDEXES:
        cur.execute(stmt)

    # Best-effort ALTERs on existing tables. price_history_training only
    # exists in the live Postgres mirror but we attempt it anyway in case
    # downstream tests stub it locally.
    _add_missing_columns(conn, "farmers", _FARMERS_MANGO_COLUMNS)
    _add_missing_columns(conn, "farm_plots", _FARM_PLOTS_MANGO_COLUMNS)
    _add_missing_columns(
        conn, "price_history_training", _PRICE_HISTORY_TRAINING_MANGO_COLUMNS
    )

    # Backfill default for any pre-existing farmer rows seeded before the
    # preferred_language column existed (matches the Postgres UPDATE).
    try:
        cur.execute(
            "UPDATE farmers SET preferred_language = 'Marathi' "
            "WHERE preferred_language IS NULL;"
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()


def _seed_agents(conn: sqlite3.Connection) -> int:
    """Insert the 4 deterministic territory agents from SDD Section 7.1.

    Idempotent via INSERT OR IGNORE on the PRIMARY KEY / UNIQUE columns.
    Returns the number of agent rows inserted on this call.
    """
    cur = conn.cursor()
    inserted = 0
    for agent in SEED_AGENTS:
        cur.execute(
            """
            INSERT OR IGNORE INTO agents (
                id, name, mobile, email, districts, region, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent["id"],
                agent["name"],
                agent["mobile"],
                agent["email"],
                json.dumps(agent["districts"]),
                agent["region"],
                1,
            ),
        )
        inserted += cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()
    return inserted


def _seed_trader_intelligence(conn: sqlite3.Connection) -> int:
    """Insert the 5 deterministic test trader rows from SDD Section 10 Agent 1.

    Idempotent via INSERT OR IGNORE on the PRIMARY KEY / UNIQUE columns.
    Returns the number of trader rows inserted on this call.
    """
    cur = conn.cursor()
    inserted = 0
    for trader in _trader_seed_rows():
        cur.execute(
            """
            INSERT OR IGNORE INTO traders (
                id, full_name, mobile, business_name, location, district,
                commodities, subscription_tier, subscription_status,
                trial_started_at, trial_ends_at, subscription_started_at,
                monthly_amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trader["id"],
                trader["full_name"],
                trader["mobile"],
                trader["business_name"],
                trader["location"],
                trader["district"],
                trader["commodities"],
                trader["subscription_tier"],
                trader["subscription_status"],
                trader["trial_started_at"],
                trader["trial_ends_at"],
                trader["subscription_started_at"],
                trader["monthly_amount"],
            ),
        )
        inserted += cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()
    return inserted


def _seed_variety_collection(conn: sqlite3.Connection) -> int:
    """Insert the 2 farmer rows, 2 farm_plots rows, and 1 empty
    whatsapp_sessions row used by the variety collection tests.

    Idempotent via INSERT OR IGNORE on the PRIMARY KEY / UNIQUE columns.
    Returns the number of farmer rows inserted on this call.
    """
    cur = conn.cursor()
    inserted_farmers = 0
    for farmer in SEED_FARMERS:
        cur.execute(
            """
            INSERT OR IGNORE INTO farmers (
                id, farmer_full_name, mobile_number,
                village, taluka, district
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                farmer["id"],
                farmer["farmer_full_name"],
                farmer["mobile_number"],
                farmer["village"],
                farmer["taluka"],
                farmer["district"],
            ),
        )
        inserted_farmers += cur.rowcount if cur.rowcount > 0 else 0

    for plot in SEED_PLOTS:
        cur.execute(
            """
            INSERT OR IGNORE INTO farm_plots (
                id, farmer_id, current_crop
            ) VALUES (?, ?, ?)
            """,
            (plot["id"], plot["farmer_id"], plot["current_crop"]),
        )

    # One empty session for the Grapes farmer so downstream agents have a
    # baseline row to update.
    cur.execute(
        """
        INSERT OR IGNORE INTO whatsapp_sessions (
            id, mobile_number, farmer_id, current_step, collection_flow
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            SEED_SESSION_GRAPES_ID,
            SEED_FARMERS[0]["mobile_number"],
            SEED_FARMERS[0]["id"],
            None,
            "booking",
        ),
    )

    conn.commit()
    return inserted_farmers


def _seed_history(conn: sqlite3.Connection) -> int:
    """Insert the 3 seed rows. Idempotent — uses an explicit existence check
    on (region, season_label, crop_type) so it also works against legacy
    databases that have amed_history.id declared as INTEGER PRIMARY KEY
    AUTOINCREMENT (where binding a uuid string to id raises a datatype
    mismatch). New databases get a uuid-style TEXT id; legacy ones let the
    autoincrement assign one.
    """
    cur = conn.cursor()
    # Probe the id column's declared type so we can pick the right code path.
    cur.execute("PRAGMA table_info(amed_history);")
    id_type = ""
    for col in cur.fetchall():
        if col[1] == "id":
            id_type = (col[2] or "").upper()
            break
    legacy_int_pk = id_type.startswith("INT")

    inserted = 0
    for row in SEED_HISTORY:
        (
            season_label,
            season_year_start,
            total_area_acres,
            harvest_start,
            harvest_peak,
            harvest_end,
            volume_mt,
            avg_price,
        ) = row

        cur.execute(
            "SELECT 1 FROM amed_history WHERE region = ? AND season_label = ? AND crop_type = ? LIMIT 1;",
            (REGION, season_label, CROP),
        )
        if cur.fetchone() is not None:
            continue  # Already seeded — skip.

        if legacy_int_pk:
            cur.execute(
                """
                INSERT INTO amed_history (
                    region, season_label, season_year_start, crop_type,
                    total_area_acres, harvest_start_date, harvest_peak_date,
                    harvest_end_date, estimated_total_volume_mt, avg_price_modal_kg
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    REGION,
                    season_label,
                    season_year_start,
                    CROP,
                    total_area_acres,
                    harvest_start,
                    harvest_peak,
                    harvest_end,
                    volume_mt,
                    avg_price,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO amed_history (
                    id, region, season_label, season_year_start, crop_type,
                    total_area_acres, harvest_start_date, harvest_peak_date,
                    harvest_end_date, estimated_total_volume_mt, avg_price_modal_kg
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    REGION,
                    season_label,
                    season_year_start,
                    CROP,
                    total_area_acres,
                    harvest_start,
                    harvest_peak,
                    harvest_end,
                    volume_mt,
                    avg_price,
                ),
            )
        inserted += cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()
    return inserted


CREATE_MODEL_REGISTRY = """
CREATE TABLE IF NOT EXISTS model_registry (
    id TEXT PRIMARY KEY,
    commodity TEXT NOT NULL,
    variety TEXT,
    model_version TEXT NOT NULL,
    model_type TEXT,
    mape REAL,
    mae REAL,
    training_rows INTEGER,
    training_date_start TEXT,
    training_date_end TEXT,
    retrain_trigger TEXT,
    is_active INTEGER DEFAULT 1,
    pickle_path TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

CREATE_CRON_RUN_LOG = """
CREATE TABLE IF NOT EXISTS cron_run_log (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok','skipped','error')),
    reason TEXT,
    metadata TEXT DEFAULT '{}',
    fired_at TEXT DEFAULT (datetime('now'))
)
"""

CREATE_CT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_model_registry_active ON model_registry(commodity, variety, is_active)",
    "CREATE INDEX IF NOT EXISTS idx_model_registry_commodity_created ON model_registry(commodity, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_cron_run_log_job_fired ON cron_run_log(job_id, fired_at DESC)",
]


def ensure_continuous_training_schema(conn: sqlite3.Connection) -> None:
    """SQLite mirror of migration 008 (model_registry + cron_run_log).

    Safe to call repeatedly; every CREATE uses IF NOT EXISTS.
    """
    cur = conn.cursor()
    cur.execute(CREATE_MODEL_REGISTRY)
    cur.execute(CREATE_CRON_RUN_LOG)
    for stmt in CREATE_CT_INDEXES:
        cur.execute(stmt)
    conn.commit()


def build_local_db(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Create (or refresh) the local SQLite DB. Returns the path used."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        # Enforce FK constraints so the variety_responses FK checks behave like
        # Postgres. Has to be set per-connection in SQLite.
        conn.execute("PRAGMA foreign_keys = ON;")
        _create_schema(conn)
        ensure_variety_collection_schema(conn)
        ensure_harvest_actuals_schema(conn)
        ensure_trader_intelligence_schema(conn)
        ensure_mango_schema(conn)
        ensure_continuous_training_schema(conn)
        inserted = _seed_history(conn)
        inserted_farmers = _seed_variety_collection(conn)
        inserted_traders = _seed_trader_intelligence(conn)
        inserted_agents = _seed_agents(conn)
        print(f"[ok] Schema ready at {db_path}")
        print(f"[ok] Seeded {inserted} new amed_history row(s) (already-present rows ignored)")
        print(f"[ok] Seeded {inserted_farmers} new farmer row(s) (already-present rows ignored)")
        print(f"[ok] Seeded {inserted_traders} new trader row(s) (already-present rows ignored)")
        print(f"[ok] Seeded {inserted_agents} new agent row(s) (already-present rows ignored)")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM amed_history;")
        total = cur.fetchone()[0]
        print(f"[ok] amed_history total rows: {total}")
        cur.execute("SELECT COUNT(*) FROM farmers;")
        print(f"[ok] farmers total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM farm_plots;")
        print(f"[ok] farm_plots total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM whatsapp_sessions;")
        print(f"[ok] whatsapp_sessions total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM variety_responses;")
        print(f"[ok] variety_responses total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM farm_harvest_actuals;")
        print(f"[ok] farm_harvest_actuals total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM traders;")
        print(f"[ok] traders total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM intelligence_reports;")
        print(f"[ok] intelligence_reports total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM report_deliveries;")
        print(f"[ok] report_deliveries total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM trader_queries;")
        print(f"[ok] trader_queries total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM trader_payments;")
        print(f"[ok] trader_payments total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM flash_alert_triggers;")
        print(f"[ok] flash_alert_triggers total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM agents;")
        print(f"[ok] agents total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM mango_phenology_log;")
        print(f"[ok] mango_phenology_log total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM mango_belt_data;")
        print(f"[ok] mango_belt_data total rows: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM forex_rates;")
        print(f"[ok] forex_rates total rows: {cur.fetchone()[0]}")
    finally:
        conn.close()
    return db_path


def main(argv: Iterable[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    target = Path(argv[0]) if argv else DEFAULT_DB_PATH
    build_local_db(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
