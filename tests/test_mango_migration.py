"""
Tests for the mango crop expansion migration (007) and its local SQLite mirror.

We do not touch production Supabase. We:
  * sanity-check the migration SQL file contains all new table names plus
    the bearing_year CHECK literal
  * build a fresh SQLite mirror via scripts.seed_local_sqlite.build_local_db
  * assert all 4 new mango tables exist
  * assert the new columns landed on farmers / farm_plots
  * assert the 4 territory agents were seeded with one per region
  * exercise the UNIQUE(mobile) constraint on agents
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
    AGENT_KONKAN_ID,
    AGENT_NASHIK_ID,
    AGENT_TASGAON_ID,
    AGENT_VIDARBHA_ID,
    build_local_db,
)

MIGRATION_PATH = REPO_ROOT / "migrations" / "007_mango_crop_expansion.sql"


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


def test_007_sql_idempotent():
    """The migration must contain all 4 new tables, the bearing_year CHECK
    literal, the UPDATE backfill, and avoid destructive verbs."""
    assert MIGRATION_PATH.exists(), f"Migration file not found: {MIGRATION_PATH}"
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    # All 4 new tables.
    for table in (
        "mango_phenology_log",
        "mango_belt_data",
        "agents",
        "forex_rates",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, (
            f"Missing CREATE TABLE IF NOT EXISTS for {table}"
        )

    # bearing_year CHECK literal must appear (it is the load-bearing constraint
    # for alternate-bearing yield adjustment in the ML / advisory engines).
    assert "bearing_year IN ('ON','OFF','UNKNOWN')" in sql, (
        "bearing_year CHECK literal missing from migration"
    )

    # preferred_language CHECK literal must appear.
    assert (
        "preferred_language IN ('Marathi','English','Konkani','Hindi')" in sql
    ), "preferred_language CHECK literal missing from migration"

    # The Postgres backfill UPDATE for preferred_language must be present.
    assert (
        "UPDATE farmers SET preferred_language = 'Marathi'" in sql
    ), "farmers preferred_language backfill UPDATE missing"

    # ADD COLUMN IF NOT EXISTS for the critical new columns.
    for col in (
        "bearing_year",
        "flowering_detected",
        "fruit_set_detected",
        "tree_count",
        "tree_age_years",
        "crop_region",
        "region",
        "preferred_language",
        "bearing_year_flag",
        "export_demand_proxy",
        "flowering_weather_score",
        "variety",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in sql, (
            f"Missing ADD COLUMN IF NOT EXISTS for {col}"
        )

    # CHECK adds on existing tables must be wrapped in duplicate_object guards.
    assert "duplicate_object" in sql, (
        "CHECK constraint adds on existing tables must swallow duplicate_object"
    )

    # Idempotency hygiene: no destructive verbs.
    upper = sql.upper()
    assert "DROP TABLE" not in upper
    assert "TRUNCATE" not in upper
    assert "DELETE FROM" not in upper


# ---------------------------------------------------------------------------
# 2. SQLite mirror has all 4 new mango tables
# ---------------------------------------------------------------------------


def test_local_sqlite_has_mango_tables(conn):
    tables = _table_names(conn)
    expected = {"mango_phenology_log", "mango_belt_data", "agents", "forex_rates"}
    missing = expected - tables
    assert not missing, f"Missing mango tables: {missing}"

    phenology_cols = _columns(conn, "mango_phenology_log")
    required = {
        "id",
        "plot_id",
        "season_label",
        "bearing_year",
        "flowering_start_date",
        "flowering_peak_date",
        "flowering_end_date",
        "flowering_intensity_pct",
        "frost_events_count",
        "rain_during_flowering_mm",
        "fruit_set_date",
        "fruit_set_pct",
        "heat_stress_events_count",
        "predicted_yield_kg_per_tree",
        "actual_yield_kg_per_tree",
        "harvest_start_date",
        "harvest_end_date",
    }
    missing_cols = required - phenology_cols
    assert not missing_cols, f"mango_phenology_log missing columns: {missing_cols}"

    belt_cols = _columns(conn, "mango_belt_data")
    required_belt = {
        "id",
        "region",
        "variety",
        "fetch_date",
        "season_label",
        "total_fields_detected",
        "total_area_acres",
        "bearing_year",
        "harvest_week_start",
        "harvest_week_end",
        "fields_harvesting",
        "estimated_volume_mt",
        "health_pct_good",
        "flowering_pct",
        "fruit_set_pct",
        "data_source",
    }
    missing_belt = required_belt - belt_cols
    assert not missing_belt, f"mango_belt_data missing columns: {missing_belt}"

    forex_cols = _columns(conn, "forex_rates")
    assert {"id", "rate_date", "usd_inr_rate", "source"} <= forex_cols


# ---------------------------------------------------------------------------
# 3. farmers + farm_plots have the new mango columns
# ---------------------------------------------------------------------------


def test_farmers_has_new_columns(conn):
    cols = _columns(conn, "farmers")
    assert "region" in cols, "farmers.region column missing"
    assert "preferred_language" in cols, "farmers.preferred_language column missing"


def test_farm_plots_has_bearing_columns(conn):
    cols = _columns(conn, "farm_plots")
    for required in (
        "bearing_year",
        "flowering_detected",
        "fruit_set_detected",
        "tree_count",
    ):
        assert required in cols, f"farm_plots.{required} missing"


# ---------------------------------------------------------------------------
# 4. Seeded agents
# ---------------------------------------------------------------------------


def test_seed_inserts_four_agents(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM agents;")
    assert cur.fetchone()[0] == 4

    # Exactly one agent per region.
    cur.execute(
        "SELECT region, COUNT(*) FROM agents GROUP BY region ORDER BY region;"
    )
    by_region = {row[0]: row[1] for row in cur.fetchall()}
    assert by_region.get("Konkan") == 1
    assert by_region.get("Vidarbha") == 1
    assert by_region.get("Marathwada") == 1
    assert by_region.get("West Maharashtra") == 1

    # Each well-known agent is present by UUID with the expected territory.
    expected = {
        AGENT_TASGAON_ID: (
            "Vikram Patil",
            "9871100001",
            ["Sangli", "Kolhapur", "Satara"],
            "West Maharashtra",
        ),
        AGENT_KONKAN_ID: (
            "Nitin Pawar",
            "9871100002",
            ["Ratnagiri", "Sindhudurg", "Raigad"],
            "Konkan",
        ),
        AGENT_NASHIK_ID: (
            "Sachin Joshi",
            "9871100003",
            ["Nashik", "Aurangabad", "Jalna"],
            "Marathwada",
        ),
        AGENT_VIDARBHA_ID: (
            "Manoj Deshpande",
            "9871100004",
            ["Nagpur", "Amravati", "Wardha"],
            "Vidarbha",
        ),
    }
    for aid, (name, mobile, districts, region) in expected.items():
        cur.execute(
            "SELECT name, mobile, districts, region FROM agents WHERE id = ?;",
            (aid,),
        )
        row = cur.fetchone()
        assert row is not None, f"agent {aid} not seeded"
        assert row[0] == name
        assert row[1] == mobile
        assert json.loads(row[2]) == districts
        assert row[3] == region


# ---------------------------------------------------------------------------
# 5. agents.mobile UNIQUE
# ---------------------------------------------------------------------------


def test_agents_mobile_unique(conn):
    cur = conn.cursor()
    # 9871100001 is already seeded for the Tasgaon agent; a second insert
    # for the same mobile must fail with IntegrityError.
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            """
            INSERT INTO agents (
                id, name, mobile, districts, region, is_active
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "a9999999-9999-9999-9999-999999999999",
                "Duplicate Mobile Tester",
                "9871100001",
                json.dumps(["Pune"]),
                "Other",
                1,
            ),
        )
        conn.commit()
