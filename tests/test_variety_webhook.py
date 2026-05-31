"""End-to-end tests for the WhatsApp variety-collection flow (Agent 2)."""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import (  # noqa: E402
    webhooks_aisensy,
    webhooks_variety,
    whatsapp_db,
    whatsapp_sender,
)


# ---------------------------------------------------------------------------
# Local minimal seed. We attempt scripts.seed_local_sqlite.seed(tmp_path) first
# (Agent 1's contract), and fall back to this in-test schema/seed so this
# module is runnable even if Agent 1's seed function isn't named exactly seed().
# ---------------------------------------------------------------------------
SEED_FARMER_ID = "11111111-1111-1111-1111-111111111111"
SEED_PLOT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SEED_AMED_ID = "33333333-3333-3333-3333-333333333333"
SEED_MOBILE = "9876543210"
SEED_NAME = "Ramesh Patil"
SEED_VILLAGE = "Tasgaon"
SEED_AMED_ACRES = 3.1  # tests can construct mismatches relative to this.


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS — mirrors migration 004."""
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS farmers (
            id TEXT PRIMARY KEY,
            farmer_full_name TEXT,
            mobile_number TEXT UNIQUE,
            alternate_mobile TEXT,
            village TEXT,
            taluka TEXT,
            amed_variety_collected INTEGER DEFAULT 0,
            amed_variety_collected_at TEXT,
            variety_collection_attempts INTEGER DEFAULT 0,
            variety_collection_status TEXT DEFAULT 'PENDING',
            variety_collection_attempted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS farm_plots (
            id TEXT PRIMARY KEY,
            farmer_id TEXT REFERENCES farmers(id),
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
            status TEXT DEFAULT 'IN_PROGRESS',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def _seed_grapes_farmer(conn: sqlite3.Connection) -> None:
    """Fallback seed used only when Agent 1's seeder is absent or naming differs."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO farmers
            (id, farmer_full_name, mobile_number, village, taluka,
             amed_variety_collected, variety_collection_attempts,
             variety_collection_status)
        VALUES (?, ?, ?, ?, ?, 0, 0, 'PENDING')
        """,
        (SEED_FARMER_ID, SEED_NAME, SEED_MOBILE, SEED_VILLAGE, "Tasgaon"),
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO farm_plots
            (id, farmer_id, current_crop, variety_source)
        VALUES (?, ?, 'Grapes', 'amed_hint')
        """,
        (SEED_PLOT_ID, SEED_FARMER_ID),
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO amed_readings
            (id, plot_id, fetch_date, crop_type_detected,
             crop_type_confidence, field_size_acres_amed)
        VALUES (?, ?, ?, 'Grapes', 0.92, ?)
        """,
        (
            SEED_AMED_ID,
            SEED_PLOT_ID,
            "2026-05-31",
            SEED_AMED_ACRES,
        ),
    )
    conn.commit()


def _build_seeded_db(db_path: Path) -> None:
    """Build a fully-seeded test database.

    Tries Agent 1's ``seed_local_sqlite.seed()`` first (per spec), falls
    back to ``build_local_db()`` (what the agent currently exposes), and
    finally to an in-test ``_seed_grapes_farmer`` if neither populates
    the farmers table. This keeps the test isolated from Agent 1's exact
    function naming while still using their seed when available.
    """
    seeded_by_agent1 = False
    try:
        from scripts import seed_local_sqlite  # type: ignore

        if hasattr(seed_local_sqlite, "seed"):
            seed_local_sqlite.seed(db_path)
            seeded_by_agent1 = True
        elif hasattr(seed_local_sqlite, "build_local_db"):
            seed_local_sqlite.build_local_db(db_path)
            seeded_by_agent1 = True
    except Exception:
        pass

    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        # If Agent 1's seed didn't insert our reference farmer (e.g. ran but
        # used different IDs), force-populate so the tests still pass.
        cur = conn.execute(
            "SELECT 1 FROM farmers WHERE id = ?", (SEED_FARMER_ID,)
        )
        if cur.fetchone() is None:
            _seed_grapes_farmer(conn)
        # Agent 1's seed populates farmers/farm_plots/whatsapp_sessions but
        # leaves amed_readings empty. Ensure a Grapes reading exists for the
        # mismatch-percentage tests.
        cur = conn.execute(
            "SELECT 1 FROM amed_readings WHERE plot_id = ?", (SEED_PLOT_ID,)
        )
        if cur.fetchone() is None:
            conn.execute(
                """
                INSERT INTO amed_readings
                    (id, plot_id, fetch_date, crop_type_detected,
                     crop_type_confidence, field_size_acres_amed)
                VALUES (?, ?, ?, 'Grapes', 0.92, ?)
                """,
                (
                    SEED_AMED_ID,
                    SEED_PLOT_ID,
                    "2026-05-31",
                    SEED_AMED_ACRES,
                ),
            )
            conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    _build_seeded_db(db_path)
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


@pytest.fixture()
def env_clean(monkeypatch):
    monkeypatch.delenv("PANKAJ_ALERT_MOBILE", raising=False)
    yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def _trigger(amed_acres: Optional[float] = SEED_AMED_ACRES) -> dict:
    return webhooks_variety.start_variety_collection(
        farmer_id=SEED_FARMER_ID,
        plot_id=SEED_PLOT_ID,
        amed_crop="Grapes",
        amed_confidence=0.92,
        amed_acres=amed_acres,
    )


def test_initial_trigger_sends_marathi_message(seeded_db, mock_sender, env_clean):
    result = _trigger()
    messages = mock_sender.read_messages()
    assert len(messages) == 1
    assert messages[0]["to"] == SEED_MOBILE
    assert "Thompson Seedless" in messages[0]["body"]
    assert "Grapes" in messages[0]["body"]
    session = whatsapp_db.get_session_by_mobile(SEED_MOBILE)
    assert session["current_step"] == webhooks_variety.Step.VARIETY
    assert session["collection_flow"] == "variety_collection"
    assert result["variety_response_id"]


def test_full_happy_path_no_mismatch(seeded_db, mock_sender, env_clean):
    _trigger()
    # 1) variety
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "Thompson Seedless")
    # 2) name (same as registered — no farmer update)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_NAME)
    # 3) phone matching the registered number
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_MOBILE)
    # 4) village (same)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_VILLAGE)
    # 5) acres within 20% of AMED (3.0)
    final = webhooks_variety.handle_incoming_message(SEED_MOBILE, "3.2")
    assert final["complete"] is True
    assert final["agent_required"] is False
    assert final["next_step"] == webhooks_variety.Step.COMPLETE

    farmer = whatsapp_db.get_farmer_by_id(SEED_FARMER_ID)
    assert farmer["amed_variety_collected"] in (1, True)
    assert farmer["variety_collection_status"] == "COMPLETE"
    plot = whatsapp_db.get_plot_by_id(SEED_PLOT_ID)
    assert plot["current_crop_variety"] == "Thompson Seedless"
    assert plot["self_reported_acres"] == pytest.approx(3.2)
    assert plot["amed_crop_verified"] in (1, True)
    vr = whatsapp_db.latest_variety_response_for_farmer(SEED_FARMER_ID)
    assert vr["status"] == "COMPLETE"
    assert vr["variety_reported"] == "Thompson Seedless"

    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("सर्व माहिती नोंदवली" in b for b in bodies)
    assert any("Thompson Seedless" in b for b in bodies)


def test_mismatch_path_farmer_accepts_amed(seeded_db, mock_sender, env_clean):
    _trigger()
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "Thompson Seedless")
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_NAME)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_MOBILE)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_VILLAGE)
    # 6.0 vs AMED 3.0 => 100% mismatch
    mismatch = webhooks_variety.handle_incoming_message(SEED_MOBILE, "6.0")
    assert mismatch["next_step"] == webhooks_variety.Step.MISMATCH
    final = webhooks_variety.handle_incoming_message(SEED_MOBILE, "2")
    assert final["complete"] is True
    assert final["agent_required"] is False
    plot = whatsapp_db.get_plot_by_id(SEED_PLOT_ID)
    assert plot["self_reported_acres"] == pytest.approx(SEED_AMED_ACRES)
    assert plot["area_mismatch_pct"] == pytest.approx(0)
    vr = whatsapp_db.latest_variety_response_for_farmer(SEED_FARMER_ID)
    assert vr["status"] == "COMPLETE"
    assert vr["mismatch_resolution"] == "farmer_accepted_amed"


def test_mismatch_path_farmer_keeps_own(seeded_db, mock_sender, env_clean):
    _trigger()
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "Thompson Seedless")
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_NAME)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_MOBILE)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_VILLAGE)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "6.0")
    final = webhooks_variety.handle_incoming_message(SEED_MOBILE, "1")
    assert final["complete"] is True
    assert final["agent_required"] is True

    farmer = whatsapp_db.get_farmer_by_id(SEED_FARMER_ID)
    assert farmer["variety_collection_status"] == "AGENT_REQUIRED"
    vr = whatsapp_db.latest_variety_response_for_farmer(SEED_FARMER_ID)
    assert vr["mismatch_resolution"] == "farmer_confirmed_own"
    assert vr["status"] == "AGENT_REQUIRED"

    messages = mock_sender.read_messages()
    pankaj_alert = [m for m in messages if "Agent verification needed" in m["body"]]
    assert pankaj_alert, "Expected a Pankaj alert message in the outbox"
    assert pankaj_alert[0]["to"] == "9999999999"


def test_mismatch_path_farmer_unsure(seeded_db, mock_sender, env_clean):
    _trigger()
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "Thompson Seedless")
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_NAME)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_MOBILE)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_VILLAGE)
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "6.0")
    final = webhooks_variety.handle_incoming_message(SEED_MOBILE, "3")
    assert final["agent_required"] is True
    farmer = whatsapp_db.get_farmer_by_id(SEED_FARMER_ID)
    assert farmer["variety_collection_status"] == "AGENT_REQUIRED"
    vr = whatsapp_db.latest_variety_response_for_farmer(SEED_FARMER_ID)
    assert vr["mismatch_resolution"] == "farmer_unsure"
    messages = mock_sender.read_messages()
    assert any("Agent verification needed" in m["body"] for m in messages)


def test_invalid_variety_reasks(seeded_db, mock_sender, env_clean):
    _trigger()
    # purely numeric variety - should re-ask
    r = webhooks_variety.handle_incoming_message(SEED_MOBILE, "12345")
    assert r["next_step"] == webhooks_variety.Step.VARIETY
    assert r["complete"] is False
    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("Please send the variety" in b for b in bodies)
    session = whatsapp_db.get_session_by_mobile(SEED_MOBILE)
    assert session["current_step"] == webhooks_variety.Step.VARIETY

    # empty variety - should also re-ask
    r2 = webhooks_variety.handle_incoming_message(SEED_MOBILE, "   ")
    assert r2["next_step"] == webhooks_variety.Step.VARIETY


def test_invalid_phone_reasks(seeded_db, mock_sender, env_clean):
    _trigger()
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "Thompson Seedless")
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_NAME)
    r = webhooks_variety.handle_incoming_message(SEED_MOBILE, "98765")
    assert r["next_step"] == webhooks_variety.Step.PHONE
    r2 = webhooks_variety.handle_incoming_message(SEED_MOBILE, "abcdefghij")
    assert r2["next_step"] == webhooks_variety.Step.PHONE
    session = whatsapp_db.get_session_by_mobile(SEED_MOBILE)
    assert session["current_step"] == webhooks_variety.Step.PHONE


def test_alternate_mobile_saved_when_phone_differs(seeded_db, mock_sender, env_clean):
    _trigger()
    webhooks_variety.handle_incoming_message(SEED_MOBILE, "Thompson Seedless")
    webhooks_variety.handle_incoming_message(SEED_MOBILE, SEED_NAME)
    alt = "9123456789"
    webhooks_variety.handle_incoming_message(SEED_MOBILE, alt)
    farmer = whatsapp_db.get_farmer_by_id(SEED_FARMER_ID)
    assert farmer["mobile_number"] == SEED_MOBILE
    assert farmer["alternate_mobile"] == alt


def test_aisensy_routes_to_variety_flow(seeded_db, mock_sender, env_clean):
    # Lazy import so seeded_db's set_db_path is already applied.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(webhooks_aisensy.router)
    app.include_router(webhooks_variety.router)

    _trigger()
    # Drain the trigger message so we can assert on follow-ups only.
    mock_sender.outbox_path.write_text("", encoding="utf-8")

    client = TestClient(app)
    response = client.post(
        "/webhooks/aisensy/incoming",
        json={
            "contacts": [{"wa_id": "91" + SEED_MOBILE}],
            "messages": [
                {
                    "id": "wamid.test-1",
                    "from": "91" + SEED_MOBILE,
                    "text": {"body": "Thompson Seedless"},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "routed"
    assert data["flow"] == "variety_collection"
    assert data["next_step"] == webhooks_variety.Step.NAME
    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("Now send your full name" in b for b in bodies)


def test_aisensy_no_session_returns_menu(seeded_db, mock_sender, env_clean):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(webhooks_aisensy.router)

    client = TestClient(app)
    response = client.post(
        "/webhooks/aisensy/incoming",
        json={
            "contacts": [{"wa_id": "915550001111"}],
            "messages": [
                {
                    "id": "wamid.unknown",
                    "from": "915550001111",
                    "text": {"body": "Hello"},
                }
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "no_active_session"
    assert data["menu_sent"] is True
    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("ShetMitra" in b for b in bodies)


def test_session_expiry_marks_abandoned(seeded_db, mock_sender, env_clean):
    # Insert an already-expired session + a matching in-progress variety_response
    response_id = whatsapp_db.create_variety_response(
        farmer_id=SEED_FARMER_ID,
        plot_id=SEED_PLOT_ID,
        amed_crop_detected="Grapes",
        amed_confidence=0.92,
    )
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(str(seeded_db))
    try:
        # Agent 1's seed may already have inserted a session row for SEED_MOBILE
        # in 'booking' flow. Clear it so we can install our expired session.
        conn.execute(
            "DELETE FROM whatsapp_sessions WHERE mobile_number = ?",
            (SEED_MOBILE,),
        )
        conn.execute(
            """
            INSERT INTO whatsapp_sessions
                (id, mobile_number, farmer_id, current_step,
                 collection_flow, session_data,
                 created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                SEED_MOBILE,
                SEED_FARMER_ID,
                webhooks_variety.Step.NAME,
                "variety_collection",
                "{}",
                expired,
                expired,
                expired,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert whatsapp_db.get_active_session(SEED_MOBILE) is None
    vr = whatsapp_db.get_variety_response(response_id)
    assert vr["status"] == "ABANDONED"
