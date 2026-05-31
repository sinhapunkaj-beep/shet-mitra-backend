"""Tests for the harvest-collection trigger and its internal endpoint."""

from __future__ import annotations

import importlib
import sqlite3
import sys
import types
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from scripts.seed_local_sqlite import (  # noqa: E402
    SEED_FARMER_GRAPES_ID,
    SEED_FARMER_POMEGRANATE_ID,
    SEED_PLOT_GRAPES_ID,
    SEED_PLOT_POMEGRANATE_ID,
    ensure_variety_collection_schema,
    ensure_harvest_actuals_schema,
    _seed_variety_collection,
)


SEASON = "2026-rabi"


@pytest.fixture
def fixed_clock():
    return datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "harvest_trigger.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
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
        )
        ensure_variety_collection_schema(conn)
        ensure_harvest_actuals_schema(conn)
        _seed_variety_collection(conn)
    finally:
        conn.close()
    return str(db_path)


@pytest.fixture
def stub_webhook(monkeypatch):
    """Install a stub ``api.webhooks_harvest.start_harvest_collection``."""
    calls: list[dict] = []

    def _stub(
        farmer_id,
        plot_id,
        crop,
        variety,
        season_label,
        amed_predicted_yield_kg=None,
        amed_predicted_grade=None,
    ):
        calls.append(
            {
                "farmer_id": farmer_id,
                "plot_id": plot_id,
                "crop": crop,
                "variety": variety,
                "season_label": season_label,
                "amed_predicted_yield_kg": amed_predicted_yield_kg,
                "amed_predicted_grade": amed_predicted_grade,
            }
        )
        return {"session_id": "harvest-sess-001", "actual_id": "actual-001"}

    api_pkg = sys.modules.get("api")
    if api_pkg is None:
        api_pkg = types.ModuleType("api")
        api_pkg.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "api", api_pkg)

    fake = types.ModuleType("api.webhooks_harvest")
    fake.start_harvest_collection = _stub  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "api.webhooks_harvest", fake)
    return calls


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _set_farmer(db_path: str, farmer_id: str, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [farmer_id]
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            f"UPDATE farmers SET {assignments} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def _get_farmer(db_path: str, farmer_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT harvest_actuals_collected,
                   harvest_collection_attempts,
                   harvest_collection_status,
                   harvest_collection_attempted_at
              FROM farmers
             WHERE id = ?
            """,
            (farmer_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    return {
        "harvest_actuals_collected": bool(row[0]),
        "harvest_collection_attempts": row[1],
        "harvest_collection_status": row[2],
        "harvest_collection_attempted_at": row[3],
    }


def _insert_amed(
    db_path: str,
    plot_id: str,
    *,
    crop: str = "Grapes",
    harvest_date_predicted: str | None = "2026-05-01",
    fetch_date: str = "2026-04-15",
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO amed_readings (
                id, plot_id, fetch_date,
                crop_type_detected, crop_type_confidence,
                field_size_acres_amed,
                harvest_date_predicted
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                plot_id,
                fetch_date,
                crop,
                0.91,
                3.0,
                harvest_date_predicted,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trigger decision-tree tests
# ---------------------------------------------------------------------------
def test_skipped_when_already_collected(seeded_db, stub_webhook, fixed_clock):
    from pipelines.harvest_trigger import trigger_harvest_collection_if_needed

    _set_farmer(seeded_db, SEED_FARMER_GRAPES_ID, harvest_actuals_collected=1)
    _insert_amed(seeded_db, SEED_PLOT_GRAPES_ID)

    decision = trigger_harvest_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        season_label=SEASON,
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "already_collected"
    assert stub_webhook == []


def test_max_retries_flags_failed(seeded_db, stub_webhook, fixed_clock):
    from pipelines.harvest_trigger import trigger_harvest_collection_if_needed

    _set_farmer(seeded_db, SEED_FARMER_GRAPES_ID, harvest_collection_attempts=3)
    _insert_amed(seeded_db, SEED_PLOT_GRAPES_ID)

    decision = trigger_harvest_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        season_label=SEASON,
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "max_retries"
    assert stub_webhook == []
    farmer = _get_farmer(seeded_db, SEED_FARMER_GRAPES_ID)
    assert farmer["harvest_collection_status"] == "FAILED"


def test_no_harvest_date_skip(seeded_db, stub_webhook, fixed_clock):
    from pipelines.harvest_trigger import trigger_harvest_collection_if_needed

    # No amed row at all for this plot.
    decision = trigger_harvest_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        season_label=SEASON,
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "no_harvest_date"
    assert stub_webhook == []


def test_too_early_skip(seeded_db, stub_webhook, fixed_clock):
    from pipelines.harvest_trigger import trigger_harvest_collection_if_needed

    # harvest predicted for 2026-05-30, today is 2026-05-31 -> within grace.
    _insert_amed(
        seeded_db,
        SEED_PLOT_GRAPES_ID,
        harvest_date_predicted="2026-05-30",
    )
    decision = trigger_harvest_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        season_label=SEASON,
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "too_early"
    assert stub_webhook == []


def test_too_late_skip(seeded_db, stub_webhook, fixed_clock):
    from pipelines.harvest_trigger import trigger_harvest_collection_if_needed

    # Harvest 100 days before today -> past 60-day deadline.
    long_ago = (fixed_clock.date() - timedelta(days=100)).isoformat()
    _insert_amed(
        seeded_db,
        SEED_PLOT_GRAPES_ID,
        harvest_date_predicted=long_ago,
    )
    decision = trigger_harvest_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        season_label=SEASON,
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "too_late"
    assert stub_webhook == []


def test_too_soon_skip(seeded_db, stub_webhook, fixed_clock):
    from pipelines.harvest_trigger import trigger_harvest_collection_if_needed

    # Predicted 30 days before today -> inside the 7..60 day window.
    harvest = (fixed_clock.date() - timedelta(days=30)).isoformat()
    _insert_amed(
        seeded_db,
        SEED_PLOT_GRAPES_ID,
        harvest_date_predicted=harvest,
    )
    recent = (fixed_clock - timedelta(hours=12)).isoformat()
    _set_farmer(
        seeded_db,
        SEED_FARMER_GRAPES_ID,
        harvest_collection_attempts=1,
        harvest_collection_attempted_at=recent,
    )
    decision = trigger_harvest_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        season_label=SEASON,
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "too_soon"
    assert stub_webhook == []
    farmer = _get_farmer(seeded_db, SEED_FARMER_GRAPES_ID)
    assert farmer["harvest_collection_attempts"] == 1


def test_happy_path_sent(seeded_db, stub_webhook, fixed_clock):
    from pipelines.harvest_trigger import trigger_harvest_collection_if_needed

    # 30 days post-harvest -> inside the active window.
    harvest = (fixed_clock.date() - timedelta(days=30)).isoformat()
    _insert_amed(
        seeded_db,
        SEED_PLOT_GRAPES_ID,
        harvest_date_predicted=harvest,
    )
    decision = trigger_harvest_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        season_label=SEASON,
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "sent"
    assert decision["session_id"] == "harvest-sess-001"
    assert len(stub_webhook) == 1
    call = stub_webhook[0]
    assert call["farmer_id"] == SEED_FARMER_GRAPES_ID
    assert call["plot_id"] == SEED_PLOT_GRAPES_ID
    assert call["crop"] == "Grapes"
    assert call["season_label"] == SEASON
    farmer = _get_farmer(seeded_db, SEED_FARMER_GRAPES_ID)
    assert farmer["harvest_collection_attempts"] == 1
    assert farmer["harvest_collection_status"] == "AWAITING_REPLY"
    stored = datetime.fromisoformat(
        farmer["harvest_collection_attempted_at"].replace("Z", "+00:00")
    )
    assert stored == fixed_clock


# ---------------------------------------------------------------------------
# Internal endpoint test
# ---------------------------------------------------------------------------
@pytest.fixture
def fastapi_client(seeded_db, stub_webhook, monkeypatch):
    monkeypatch.setenv("SHETMITRA_DB_PATH", seeded_db)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)

    import main as main_mod
    importlib.reload(main_mod)

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        yield client


def test_internal_endpoint_returns_decision(seeded_db, fastapi_client, fixed_clock):
    # Insert an AMED row 30 days post-harvest so the trigger fires.
    harvest = (date.today() - timedelta(days=30)).isoformat()
    _insert_amed(
        seeded_db,
        SEED_PLOT_GRAPES_ID,
        harvest_date_predicted=harvest,
    )
    resp = fastapi_client.post(
        "/internal/trigger-harvest-collection",
        json={
            "farmer_id": SEED_FARMER_GRAPES_ID,
            "plot_id": SEED_PLOT_GRAPES_ID,
            "season_label": SEASON,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "action" in body
    assert body["action"] == "sent"
    assert body.get("session_id") == "harvest-sess-001"
