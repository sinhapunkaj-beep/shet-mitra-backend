"""
Tests for the AMED migration schema, exercised against a local SQLite mirror.

These tests deliberately avoid the production Supabase Postgres instance.
They verify:
  * the three new tables are created with the expected columns
  * the three seed rows are present in amed_history with correct values
  * the UNIQUE(region, season_label, crop_type) constraint blocks duplicates
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

# Make repo root importable so `scripts.seed_local_sqlite` resolves.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.seed_local_sqlite import build_local_db  # noqa: E402

EXPECTED_TABLES = {"amed_readings", "amed_belt_data", "amed_history"}

EXPECTED_COLUMNS = {
    "amed_readings": {
        "id",
        "plot_id",
        "fetch_date",
        "crop_type_detected",
        "crop_type_confidence",
        "field_size_acres_amed",
        "sowing_date",
        "harvest_date_predicted",
        "growth_stage",
        "growth_stage_confidence",
        "irrigation_detected",
        "last_event",
        "last_event_date",
        "data_refresh_date",
        "use_mock",
        "raw_response",
        "created_at",
    },
    "amed_belt_data": {
        "id",
        "region",
        "fetch_date",
        "crop_type",
        "total_fields_detected",
        "total_area_acres",
        "harvest_week_start",
        "harvest_week_end",
        "fields_harvesting",
        "estimated_volume_mt",
        "health_pct_good",
        "health_pct_moderate",
        "health_pct_stressed",
        "health_pct_critical",
        "data_refresh_date",
        "raw_response",
        "created_at",
    },
    "amed_history": {
        "id",
        "region",
        "season_label",
        "season_year_start",
        "crop_type",
        "total_area_acres",
        "harvest_start_date",
        "harvest_peak_date",
        "harvest_end_date",
        "estimated_total_volume_mt",
        "avg_price_modal_kg",
        "created_at",
    },
}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Build a fresh local SQLite DB in a temp dir for each test."""
    target = tmp_path / "test.db"
    build_local_db(target)
    return target


@pytest.fixture()
def conn(db_path: Path):
    c = sqlite3.connect(str(db_path))
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


def test_three_tables_exist(conn):
    tables = _table_names(conn)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_table_has_expected_columns(conn, table):
    cols = _columns(conn, table)
    expected = EXPECTED_COLUMNS[table]
    missing = expected - cols
    assert not missing, f"{table} missing columns: {missing}"


def test_amed_history_has_three_seed_rows(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM amed_history
        WHERE region = 'Tasgaon_Sangli_belt' AND crop_type = 'Grapes';
        """
    )
    assert cur.fetchone()[0] == 3


def test_amed_history_values_match_sdd(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT season_label, total_area_acres, estimated_total_volume_mt,
               avg_price_modal_kg, harvest_start_date, harvest_peak_date,
               harvest_end_date
        FROM amed_history
        WHERE region = 'Tasgaon_Sangli_belt' AND crop_type = 'Grapes'
        ORDER BY season_label;
        """
    )
    rows = cur.fetchall()
    assert len(rows) == 3

    by_season = {r[0]: r for r in rows}

    # 2022-23
    r = by_season["2022-23"]
    assert r[1] == pytest.approx(7890.0)
    assert r[2] == pytest.approx(11420.0)
    assert r[3] == pytest.approx(118.50)
    assert r[4] == "2023-04-01"
    assert r[5] == "2023-04-21"
    assert r[6] == "2023-05-15"

    # 2023-24
    r = by_season["2023-24"]
    assert r[1] == pytest.approx(8012.0)
    assert r[2] == pytest.approx(11680.0)
    assert r[3] == pytest.approx(134.20)
    assert r[4] == "2024-04-05"
    assert r[5] == "2024-04-19"
    assert r[6] == "2024-05-10"

    # 2024-25 — the SDD-flagged price-spike season
    r = by_season["2024-25"]
    assert r[1] == pytest.approx(8180.0)
    assert r[2] == pytest.approx(11890.0)
    assert r[3] == pytest.approx(298.40), "2024-25 price must match SDD exactly"
    assert r[4] == "2025-03-28"
    assert r[5] == "2025-04-14"
    assert r[6] == "2025-05-08"


def test_unique_constraint_blocks_duplicates(conn):
    """Inserting a duplicate (region, season_label, crop_type) must fail."""
    cur = conn.cursor()
    with pytest.raises(sqlite3.IntegrityError):
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
                "Tasgaon_Sangli_belt",
                "2024-25",
                2024,
                "Grapes",
                9999.0,
                "2025-03-28",
                "2025-04-14",
                "2025-05-08",
                12345.0,
                999.99,
            ),
        )
        conn.commit()


def test_seed_is_idempotent(tmp_path):
    """Running build_local_db twice must not duplicate the seed rows."""
    target = tmp_path / "test.db"
    build_local_db(target)
    build_local_db(target)
    conn = sqlite3.connect(str(target))
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM amed_history;")
        assert cur.fetchone()[0] == 3
    finally:
        conn.close()
