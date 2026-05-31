"""
Tests for the trader-intelligence migration (006) and its local SQLite mirror.

We do not touch production Supabase. We:
  * sanity-check the migration SQL parses and contains the key DDL fragments
    (all 6 table names plus the 3 named CHECK constraints we rely on)
  * build a fresh SQLite mirror via scripts.seed_local_sqlite.build_local_db
  * assert all 6 trader tables exist with the expected columns
  * assert the 5 seeded traders are present with the right tier/status mix
  * exercise the UNIQUE(mobile) constraint on traders
  * assert data/razorpay_plans.json is valid and lists the 3 expected plans
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Make repo root importable so scripts.seed_local_sqlite resolves.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.seed_local_sqlite import (  # noqa: E402
    SEED_TRADER_BASIC_ID,
    SEED_TRADER_MOBILES,
    SEED_TRADER_PREMIUM_ID,
    SEED_TRADER_STANDARD_ID,
    SEED_TRADER_TRIAL_BASIC_ID,
    SEED_TRADER_TRIAL_STANDARD_ID,
    build_local_db,
)

MIGRATION_PATH = REPO_ROOT / "migrations" / "006_trader_intelligence.sql"
RAZORPAY_PLANS_PATH = REPO_ROOT / "data" / "razorpay_plans.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    target = tmp_path / "test.db"
    build_local_db(target)
    return target


@pytest.fixture()
def conn(db_path: Path):
    c = sqlite3.connect(str(db_path))
    c.execute("PRAGMA foreign_keys = ON;")
    try:
        yield c
    finally:
        c.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    return {row[1] for row in cur.fetchall()}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# 1. SQL file sanity
# ---------------------------------------------------------------------------


def test_006_sql_idempotent():
    """The migration must contain all 6 table names and the named CHECK
    constraints, and avoid destructive verbs."""
    assert MIGRATION_PATH.exists(), f"Migration file not found: {MIGRATION_PATH}"
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    # All 6 tables.
    for table in (
        "traders",
        "intelligence_reports",
        "report_deliveries",
        "trader_queries",
        "trader_payments",
        "flash_alert_triggers",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, (
            f"Missing CREATE TABLE IF NOT EXISTS for {table}"
        )

    # The 3 named CHECK constraints we rely on.
    assert "CONSTRAINT traders_tier_check" in sql
    assert "CONSTRAINT traders_status_check" in sql
    assert "CONSTRAINT intelligence_reports_type_check" in sql

    # Idempotency hygiene: no destructive verbs.
    upper = sql.upper()
    assert "DROP TABLE" not in upper
    assert "TRUNCATE" not in upper
    assert "DELETE FROM" not in upper


# ---------------------------------------------------------------------------
# 2. SQLite mirror has all 6 trader tables with the expected columns
# ---------------------------------------------------------------------------


def test_local_sqlite_has_trader_tables(conn):
    tables = _table_names(conn)
    expected = {
        "traders",
        "intelligence_reports",
        "report_deliveries",
        "trader_queries",
        "trader_payments",
        "flash_alert_triggers",
    }
    missing = expected - tables
    assert not missing, f"Missing trader tables: {missing}"

    trader_cols = _columns(conn, "traders")
    required_trader_cols = {
        "id",
        "full_name",
        "mobile",
        "business_name",
        "location",
        "district",
        "commodities",
        "subscription_tier",
        "subscription_status",
        "trial_started_at",
        "trial_ends_at",
        "subscription_started_at",
        "monthly_amount",
        "referred_by",
    }
    missing_trader_cols = required_trader_cols - trader_cols
    assert not missing_trader_cols, f"traders missing columns: {missing_trader_cols}"

    report_cols = _columns(conn, "intelligence_reports")
    required_report_cols = {
        "id",
        "report_type",
        "commodity",
        "report_date",
        "content_english",
        "signal",
        "price_forecast_day1",
        "price_forecast_day3",
        "price_forecast_day7",
        "confidence_pct",
        "bearing_year",
        "trigger_event",
    }
    missing_report_cols = required_report_cols - report_cols
    assert not missing_report_cols, (
        f"intelligence_reports missing columns: {missing_report_cols}"
    )

    delivery_cols = _columns(conn, "report_deliveries")
    assert {"report_id", "trader_id", "delivery_status", "aisensy_message_id"} <= delivery_cols

    payment_cols = _columns(conn, "trader_payments")
    assert {"trader_id", "amount", "payment_month", "status"} <= payment_cols

    flash_cols = _columns(conn, "flash_alert_triggers")
    assert {"commodity", "trigger_type", "report_id", "alert_sent"} <= flash_cols


# ---------------------------------------------------------------------------
# 3. Seeded traders
# ---------------------------------------------------------------------------


def test_seed_inserts_five_traders(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM traders;")
    assert cur.fetchone()[0] == 5

    cur.execute(
        """
        SELECT subscription_tier, subscription_status, COUNT(*)
        FROM traders
        GROUP BY subscription_tier, subscription_status
        ORDER BY subscription_tier, subscription_status;
        """
    )
    breakdown = {(row[0], row[1]): row[2] for row in cur.fetchall()}

    # 1 BASIC ACTIVE + 1 STANDARD ACTIVE + 1 PREMIUM ACTIVE + 2 TRIAL.
    assert breakdown.get(("BASIC", "ACTIVE")) == 1
    assert breakdown.get(("STANDARD", "ACTIVE")) == 1
    assert breakdown.get(("PREMIUM", "ACTIVE")) == 1
    trial_count = sum(v for (tier, status), v in breakdown.items() if status == "TRIAL")
    assert trial_count == 2

    # Verify each well-known trader is present by UUID.
    for tid in (
        SEED_TRADER_BASIC_ID,
        SEED_TRADER_STANDARD_ID,
        SEED_TRADER_PREMIUM_ID,
        SEED_TRADER_TRIAL_STANDARD_ID,
        SEED_TRADER_TRIAL_BASIC_ID,
    ):
        cur.execute("SELECT mobile FROM traders WHERE id = ?;", (tid,))
        row = cur.fetchone()
        assert row is not None, f"trader {tid} not seeded"
        assert row[0] == SEED_TRADER_MOBILES[tid]


# ---------------------------------------------------------------------------
# 4. traders.mobile UNIQUE
# ---------------------------------------------------------------------------


def test_traders_mobile_unique(conn):
    cur = conn.cursor()
    # 9870000001 is already seeded for the BASIC trader; a second insert
    # for the same mobile must fail with IntegrityError.
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            """
            INSERT INTO traders (
                id, full_name, mobile, subscription_tier, subscription_status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "t9999999-9999-9999-9999-999999999999",
                "Duplicate Mobile Tester",
                "9870000001",
                "BASIC",
                "TRIAL",
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 5. data/razorpay_plans.json
# ---------------------------------------------------------------------------


def test_razorpay_plans_json_valid():
    assert RAZORPAY_PLANS_PATH.exists(), (
        f"Razorpay plan config not found: {RAZORPAY_PLANS_PATH}"
    )
    payload = json.loads(RAZORPAY_PLANS_PATH.read_text(encoding="utf-8"))
    plans = payload.get("plans")
    assert isinstance(plans, list)
    assert len(plans) == 3

    amounts = {plan["local_id"]: plan["amount_paise"] for plan in plans}
    assert amounts["trader_basic"] == 300000
    assert amounts["trader_standard"] == 700000
    assert amounts["trader_premium"] == 1500000

    for plan in plans:
        assert plan["interval"] == "monthly"
        assert plan["description"].startswith("ShetMitra Trader Intelligence")
