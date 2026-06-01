"""tests/test_hindi_webhooks.py — JH farmers get Hindi variety + harvest prompts.

Verifies that webhooks_variety + webhooks_harvest route their farmer-
facing prompts through pipelines.i18n.get_message with language resolved
from the farmer's region_code (Hindi for JH, Marathi for MH).

The DB lookup is bypassed via api.whatsapp_sender._FARMER_REGION_CACHE
priming so we never need a SQLite or Supabase connection.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import (  # noqa: E402
    webhooks_harvest,
    webhooks_variety,
    whatsapp_db,
    whatsapp_sender,
)
from pipelines import i18n  # noqa: E402


SEED_FARMER_ID = "22222222-2222-2222-2222-222222222222"
SEED_PLOT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SEED_MOBILE = "9123456780"
SEED_NAME = "Ravi Kumar"
SEED_VILLAGE = "Mahagama"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS farmers (
            id TEXT PRIMARY KEY,
            farmer_full_name TEXT,
            mobile_number TEXT UNIQUE,
            alternate_mobile TEXT,
            village TEXT,
            taluka TEXT,
            region_code TEXT,
            amed_variety_collected INTEGER DEFAULT 0,
            amed_variety_collected_at TEXT,
            variety_collection_attempts INTEGER DEFAULT 0,
            variety_collection_status TEXT DEFAULT 'PENDING',
            variety_collection_attempted_at TEXT,
            harvest_actuals_collected INTEGER DEFAULT 0,
            harvest_actuals_collected_at TEXT,
            harvest_collection_attempts INTEGER DEFAULT 0,
            harvest_collection_status TEXT DEFAULT 'PENDING'
        );
        CREATE TABLE IF NOT EXISTS farm_plots (
            id TEXT PRIMARY KEY,
            farmer_id TEXT,
            current_crop TEXT,
            current_crop_variety TEXT,
            self_reported_acres REAL,
            amed_crop_verified INTEGER DEFAULT 0,
            amed_verification_date TEXT,
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
            farmer_id TEXT,
            current_step TEXT,
            collection_flow TEXT DEFAULT 'booking',
            session_data TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS variety_responses (
            id TEXT PRIMARY KEY,
            farmer_id TEXT,
            plot_id TEXT,
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
            status TEXT DEFAULT 'IN_PROGRESS',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS farm_harvest_actuals (
            id TEXT PRIMARY KEY,
            farmer_id TEXT NOT NULL,
            plot_id TEXT,
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
        CREATE TABLE IF NOT EXISTS regions (
            region_code TEXT PRIMARY KEY,
            whatsapp_sender_name TEXT
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO regions (region_code, whatsapp_sender_name) "
        "VALUES ('JH', 'Bagaan Sathi')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO regions (region_code, whatsapp_sender_name) "
        "VALUES ('MH', 'ShetMitra')"
    )
    conn.commit()


def _seed_jh_farmer(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO farmers
            (id, farmer_full_name, mobile_number, village, taluka, region_code)
        VALUES (?, ?, ?, ?, ?, 'JH')
        """,
        (SEED_FARMER_ID, SEED_NAME, SEED_MOBILE, SEED_VILLAGE, "Godda"),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO farm_plots
            (id, farmer_id, current_crop, current_crop_variety,
             self_reported_acres)
        VALUES (?, ?, 'Mango', 'Jardalu', 5.0)
        """,
        (SEED_PLOT_ID, SEED_FARMER_ID),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO amed_readings
            (id, plot_id, fetch_date, crop_type_detected,
             crop_type_confidence, field_size_acres_amed)
        VALUES ('amed-jh-1', ?, '2026-05-31', 'Mango', 0.93, 5.0)
        """,
        (SEED_PLOT_ID,),
    )
    conn.commit()


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_jh.db"
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        _seed_jh_farmer(conn)
    finally:
        conn.close()
    whatsapp_db.set_db_path(db_path)
    whatsapp_sender.reset_region_cache()
    yield db_path
    whatsapp_db.reset_db_path()
    whatsapp_sender.reset_region_cache()


@pytest.fixture()
def mock_sender(tmp_path: Path):
    outbox = tmp_path / "outbox.jsonl"
    sender = whatsapp_sender.MockSender(outbox_path=outbox)
    whatsapp_sender.set_sender(sender)
    yield sender
    whatsapp_sender.reset_sender()


@pytest.fixture()
def env_clean(monkeypatch):
    monkeypatch.delenv("PANKAJ_ALERT_MOBILE", raising=False)
    yield


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_jh_farmer_variety_trigger_uses_hindi_template(
    seeded_db, mock_sender, env_clean
):
    """The initial variety-collection message for a JH farmer must use the
    Hindi 'satellite_detected' template from data/translations/hindi.json."""
    webhooks_variety.start_variety_collection(
        farmer_id=SEED_FARMER_ID,
        plot_id=SEED_PLOT_ID,
        amed_crop="Mango",
        amed_confidence=0.93,
        amed_acres=5.0,
    )
    messages = mock_sender.read_messages()
    assert len(messages) == 1
    body = messages[0]["body"]
    # Hindi 'satellite_detected' for crop=Mango.
    expected = i18n.get_message("satellite_detected", "Hindi", crop="Mango")
    assert expected in body, (
        f"Hindi 'satellite_detected' template not found in body. "
        f"Expected substring: {expected!r}\nGot body: {body!r}"
    )
    # And it must NOT include the Marathi-only phrase.
    assert "तुमच्या शेताबद्दल" not in body


def test_jh_farmer_variety_saved_uses_hindi_template(
    seeded_db, mock_sender, env_clean
):
    """After a JH farmer sends their variety, the 'variety_saved' reply
    must be in Hindi (किस्म दर्ज) rather than Marathi (नोंदवले)."""
    webhooks_variety.start_variety_collection(
        farmer_id=SEED_FARMER_ID,
        plot_id=SEED_PLOT_ID,
        amed_crop="Mango",
        amed_confidence=0.93,
        amed_acres=5.0,
    )
    # Drain the trigger message so we can assert on the variety-saved one.
    mock_sender.outbox_path.write_text("", encoding="utf-8")
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "Jardalu")
    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("दर्ज हो गई" in b for b in bodies), (
        f"expected Hindi 'दर्ज हो गई' (variety_saved) in {bodies}"
    )
    # No Marathi 'नोंदवले' regression.
    assert not any("नोंदवले" in b for b in bodies)


def test_jh_farmer_harvest_initial_uses_hindi_template(
    seeded_db, mock_sender, env_clean
):
    """The harvest-outcome initial message for a JH farmer must use the
    Hindi 'harvest_yield_question' template."""
    webhooks_harvest.start_harvest_collection(
        farmer_id=SEED_FARMER_ID,
        plot_id=SEED_PLOT_ID,
        crop="Mango",
        variety="Jardalu",
        season_label="2026-rabi",
    )
    messages = mock_sender.read_messages()
    # initial + Q1
    assert len(messages) == 2
    body0 = messages[0]["body"]
    expected = i18n.get_message("harvest_yield_question", "Hindi")
    assert expected in body0, (
        f"Hindi 'harvest_yield_question' not found in initial. "
        f"Expected: {expected!r}\nGot: {body0!r}"
    )
    # Marathi-specific phrase must NOT appear.
    assert "हंगामात तुम्हाला" not in body0


def test_mh_farmer_still_gets_marathi_no_regression(tmp_path):
    """SDD §6 regression guard — a farmer with region_code='MH' must still
    see the Marathi templates. We use a fresh DB so the JH seed above
    doesn't leak."""
    db = tmp_path / "test_mh.db"
    conn = sqlite3.connect(str(db))
    try:
        _ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO farmers "
            "(id, farmer_full_name, mobile_number, village, taluka, region_code) "
            "VALUES (?, 'Ramesh', '9000000001', 'Tasgaon', 'Tasgaon', 'MH')",
            ("mh-farmer-1",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO farm_plots "
            "(id, farmer_id, current_crop, current_crop_variety) "
            "VALUES ('mh-plot-1', 'mh-farmer-1', 'Grapes', 'Thompson Seedless')"
        )
        conn.commit()
    finally:
        conn.close()
    whatsapp_db.set_db_path(db)
    whatsapp_sender.reset_region_cache()
    outbox = tmp_path / "outbox.jsonl"
    sender = whatsapp_sender.MockSender(outbox_path=outbox)
    whatsapp_sender.set_sender(sender)
    try:
        webhooks_variety.start_variety_collection(
            farmer_id="mh-farmer-1",
            plot_id="mh-plot-1",
            amed_crop="Grapes",
            amed_confidence=0.92,
            amed_acres=3.0,
        )
        bodies = [m["body"] for m in sender.read_messages()]
        # Marathi-only phrasing — confirms no regression.
        assert any("तुमच्या शेताबद्दल" in b for b in bodies), (
            f"MH farmer lost the Marathi greeting: {bodies}"
        )
        # And it must NOT have switched to Hindi.
        assert not any(
            i18n.get_message("satellite_detected", "Hindi", crop="Grapes") in b
            for b in bodies
        )
    finally:
        whatsapp_sender.reset_sender()
        whatsapp_db.reset_db_path()
        whatsapp_sender.reset_region_cache()
