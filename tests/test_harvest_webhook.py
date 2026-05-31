"""End-to-end tests for the WhatsApp harvest-outcome collection flow."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import (  # noqa: E402
    webhooks_harvest,
    whatsapp_db,
    whatsapp_sender,
)


SEED_FARMER_ID = "11111111-1111-1111-1111-111111111111"
SEED_PLOT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SEED_MOBILE = "9876543210"
SEED_NAME = "Ramesh Patil"
SEED_SEASON = "2026-rabi"
SEED_CROP = "Grapes"
SEED_VARIETY = "Thompson Seedless"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Mirror of migrations 004 + 005 for the test DB."""
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS farmers (
            id TEXT PRIMARY KEY,
            farmer_full_name TEXT,
            mobile_number TEXT UNIQUE,
            village TEXT,
            taluka TEXT,
            amed_variety_collected INTEGER DEFAULT 0,
            variety_collection_attempts INTEGER DEFAULT 0,
            variety_collection_status TEXT DEFAULT 'PENDING',
            harvest_actuals_collected INTEGER DEFAULT 0,
            harvest_actuals_collected_at TEXT,
            harvest_collection_attempts INTEGER DEFAULT 0,
            harvest_collection_attempted_at TEXT,
            harvest_collection_status TEXT DEFAULT 'PENDING'
        );

        CREATE TABLE IF NOT EXISTS farm_plots (
            id TEXT PRIMARY KEY,
            farmer_id TEXT REFERENCES farmers(id),
            current_crop TEXT,
            current_crop_variety TEXT,
            self_reported_acres REAL,
            amed_crop_verified INTEGER DEFAULT 0,
            area_mismatch_pct REAL,
            variety_source TEXT DEFAULT 'farmer_reported'
        );

        CREATE TABLE IF NOT EXISTS amed_readings (
            id TEXT PRIMARY KEY,
            plot_id TEXT,
            fetch_date TEXT NOT NULL,
            crop_type_detected TEXT,
            crop_type_confidence REAL,
            field_size_acres_amed REAL,
            harvest_date_predicted TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS whatsapp_sessions (
            id TEXT PRIMARY KEY,
            mobile_number TEXT UNIQUE NOT NULL,
            farmer_id TEXT REFERENCES farmers(id),
            current_step TEXT,
            collection_flow TEXT DEFAULT 'booking',
            session_data TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            expires_at TEXT
        );

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
            grade TEXT,
            sold_date TEXT,
            buyer_type TEXT,
            reported_via TEXT DEFAULT 'whatsapp',
            amed_predicted_yield_kg REAL,
            amed_predicted_grade TEXT,
            yield_accuracy_pct REAL,
            raw_response TEXT,
            collection_started_at TEXT,
            collection_completed_at TEXT,
            status TEXT DEFAULT 'IN_PROGRESS',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (farmer_id, plot_id, season_label, crop_type)
        );
        """
    )
    conn.commit()


def _seed(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO farmers
            (id, farmer_full_name, mobile_number, village, taluka,
             harvest_actuals_collected, harvest_collection_attempts,
             harvest_collection_status)
        VALUES (?, ?, ?, 'Tasgaon', 'Tasgaon', 0, 0, 'PENDING')
        """,
        (SEED_FARMER_ID, SEED_NAME, SEED_MOBILE),
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO farm_plots
            (id, farmer_id, current_crop, current_crop_variety, self_reported_acres)
        VALUES (?, ?, ?, ?, 3.0)
        """,
        (SEED_PLOT_ID, SEED_FARMER_ID, SEED_CROP, SEED_VARIETY),
    )
    conn.commit()


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "harvest_test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        _seed(conn)
    finally:
        conn.close()
    whatsapp_db.set_db_path(db_path)
    yield db_path
    whatsapp_db.reset_db_path()


@pytest.fixture()
def mock_sender(tmp_path: Path) -> whatsapp_sender.MockSender:
    outbox = tmp_path / "outbox.jsonl"
    sender = whatsapp_sender.MockSender(outbox_path=outbox)
    whatsapp_sender.set_sender(sender)
    yield sender
    whatsapp_sender.reset_sender()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _start(amed_predicted_yield_kg=None, amed_predicted_grade=None) -> dict:
    return webhooks_harvest.start_harvest_collection(
        farmer_id=SEED_FARMER_ID,
        plot_id=SEED_PLOT_ID,
        crop=SEED_CROP,
        variety=SEED_VARIETY,
        season_label=SEED_SEASON,
        amed_predicted_yield_kg=amed_predicted_yield_kg,
        amed_predicted_grade=amed_predicted_grade,
    )


def _read_actual(db_path: Path) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM farm_harvest_actuals WHERE farmer_id = ? LIMIT 1",
            (SEED_FARMER_ID,),
        )
        row = cur.fetchone()
        return {k: row[k] for k in row.keys()} if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_initial_trigger_sends_marathi_message(seeded_db, mock_sender):
    result = _start()
    messages = mock_sender.read_messages()
    # Initial kickoff + Q1.
    assert len(messages) == 2
    assert messages[0]["to"] == SEED_MOBILE
    body0 = messages[0]["body"]
    assert "हंगामात" in body0
    assert "What was your harvest this season" in body0
    assert "Total yield kg/acre" in body0
    body1 = messages[1]["body"]
    assert "kg/acre" in body1
    session = whatsapp_db.get_session_by_mobile(SEED_MOBILE)
    assert session["current_step"] == webhooks_harvest.Step.YIELD
    assert session["collection_flow"] == "harvest_actuals"
    assert result["actual_id"]


def test_full_happy_path(seeded_db, mock_sender):
    _start()
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "1200")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "85.5")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "A")
    final = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "today")

    assert final["complete"] is True
    assert final["next_step"] == webhooks_harvest.Step.COMPLETE

    actual = _read_actual(seeded_db)
    assert actual is not None
    assert actual["status"] == "COMPLETE"
    assert actual["yield_per_acre_kg"] == pytest.approx(1200.0)
    # Plot has 3 acres -> total = 1200 * 3 = 3600.
    assert actual["total_yield_kg"] == pytest.approx(3600.0)
    assert actual["selling_price_inr_per_kg"] == pytest.approx(85.5)
    assert actual["grade"] == "A"
    assert actual["sold_date"]  # set
    assert actual["collection_completed_at"] is not None

    farmer = whatsapp_db.get_farmer_by_id(SEED_FARMER_ID)
    assert farmer["harvest_actuals_collected"] in (1, True)
    assert farmer["harvest_collection_status"] == "COMPLETE"

    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("Season data saved" in b or "हंगामाची माहिती नोंदवली" in b for b in bodies)


def test_invalid_yield_reasks(seeded_db, mock_sender):
    _start()
    # Below the per-acre floor of 50 — re-ask.
    r = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "5")
    assert r["next_step"] == webhooks_harvest.Step.YIELD
    assert r["complete"] is False
    # Non-numeric — re-ask.
    r2 = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "abc")
    assert r2["next_step"] == webhooks_harvest.Step.YIELD
    session = whatsapp_db.get_session_by_mobile(SEED_MOBILE)
    assert session["current_step"] == webhooks_harvest.Step.YIELD


def test_invalid_grade_reasks(seeded_db, mock_sender):
    _start()
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "1200")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "85")
    r = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "????")
    assert r["next_step"] == webhooks_harvest.Step.GRADE
    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("Please send grade" in b for b in bodies)


def test_marathi_grade_input_accepted(seeded_db, mock_sender):
    _start()
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "1200")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "90")
    r = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "ए")
    assert r["next_step"] == webhooks_harvest.Step.SOLD_DATE
    actual = _read_actual(seeded_db)
    assert actual["grade"] == "A"


def test_date_relative_today_yesterday(seeded_db, mock_sender):
    _start()
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "1200")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "80")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "B")
    r = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "yesterday")
    assert r["complete"] is True
    actual = _read_actual(seeded_db)
    assert actual["sold_date"] is not None
    # The ISO date stored must be yesterday's, not today's.
    from datetime import date, timedelta
    assert actual["sold_date"] == (date.today() - timedelta(days=1)).isoformat()


def test_date_unparseable_reasks(seeded_db, mock_sender):
    _start()
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "1200")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "80")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "A")
    r = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "sometime soon")
    assert r["next_step"] == webhooks_harvest.Step.SOLD_DATE
    assert r["complete"] is False
    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("DD-MM-YYYY" in b for b in bodies)


def test_yield_accuracy_pct_computed(seeded_db, mock_sender):
    # AMED predicted 1000 kg per acre; farmer reports 1200 -> 80% accuracy.
    _start(amed_predicted_yield_kg=1000.0, amed_predicted_grade="A")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "1200")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "85")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "A")
    final = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "today")
    assert final["complete"] is True
    # accuracy_pct = (1 - |1200 - 1000| / 1000) * 100 = 80.0
    actual = _read_actual(seeded_db)
    assert actual["yield_accuracy_pct"] == pytest.approx(80.0)
    assert final["yield_accuracy_pct"] == pytest.approx(80.0)


def test_date_iso_format_accepted(seeded_db, mock_sender):
    _start()
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "1200")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "90")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "C")
    r = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "2026-05-15")
    assert r["complete"] is True
    actual = _read_actual(seeded_db)
    assert actual["sold_date"] == "2026-05-15"


def test_date_dd_mm_yyyy_accepted(seeded_db, mock_sender):
    _start()
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "1200")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "90")
    webhooks_harvest.handle_incoming_message(SEED_MOBILE, "A")
    r = webhooks_harvest.handle_incoming_message(SEED_MOBILE, "15-05-2026")
    assert r["complete"] is True
    actual = _read_actual(seeded_db)
    assert actual["sold_date"] == "2026-05-15"
