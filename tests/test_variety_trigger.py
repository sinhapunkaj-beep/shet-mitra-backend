"""Tests for the variety-collection trigger (AGENT 3).

Covers every branch of the decision tree in
``pipelines.variety_trigger.trigger_variety_collection_if_needed`` and
exercises the internal HTTP endpoint mounted in ``main.app``.

Each test gets a fresh seeded SQLite database via Agent 1's
``ensure_variety_collection_schema`` + ``_seed_variety_collection``
helpers, plus a stub ``api.webhooks_variety.start_variety_collection``
installed via ``sys.modules`` so we can assert that the trigger does or
does not invoke Agent 2's send path.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import after sys.path is fixed.
from scripts.seed_local_sqlite import (  # noqa: E402
    SEED_FARMER_GRAPES_ID,
    SEED_FARMER_POMEGRANATE_ID,
    SEED_FARMERS,
    SEED_PLOT_GRAPES_ID,
    ensure_variety_collection_schema,
    _seed_variety_collection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_clock():
    """A frozen UTC datetime that tests can offset against."""
    return datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def seeded_db(tmp_path):
    """Fresh SQLite mirror with two farmers + two plots + one session.

    Also bootstraps ``amed_readings`` so the internal endpoint has
    something to query.
    """
    db_path = tmp_path / "variety_test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # AMED tables (so the endpoint join can run).
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
        _seed_variety_collection(conn)
    finally:
        conn.close()
    return str(db_path)


@pytest.fixture
def stub_webhook(monkeypatch):
    """Install a stub ``api.webhooks_variety.start_variety_collection``.

    Returns the ``calls`` list so tests can assert invocation args.
    """
    calls: list[dict] = []

    def _stub(
        farmer_id,
        plot_id,
        amed_crop,
        amed_confidence,
        amed_acres,
    ):
        calls.append(
            {
                "farmer_id": farmer_id,
                "plot_id": plot_id,
                "amed_crop": amed_crop,
                "amed_confidence": amed_confidence,
                "amed_acres": amed_acres,
            }
        )
        return {"session_id": "test-sess-123", "sent": True}

    # Ensure the api package is importable so ``from api.webhooks_variety
    # import start_variety_collection`` resolves to our stub.
    api_pkg = sys.modules.get("api")
    if api_pkg is None:
        api_pkg = types.ModuleType("api")
        api_pkg.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "api", api_pkg)

    fake_module = types.ModuleType("api.webhooks_variety")
    fake_module.start_variety_collection = _stub  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "api.webhooks_variety", fake_module)
    return calls


# Convenience helpers ------------------------------------------------------


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
            SELECT amed_variety_collected,
                   variety_collection_attempts,
                   variety_collection_status,
                   variety_collection_attempted_at
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
        "amed_variety_collected": bool(row[0]),
        "variety_collection_attempts": row[1],
        "variety_collection_status": row[2],
        "variety_collection_attempted_at": row[3],
    }


def _insert_amed_reading(
    db_path: str,
    plot_id: str,
    crop: str = "Grapes",
    confidence: float = 0.91,
    acres: float = 3.1,
    fetch_date: str = "2026-05-31",
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO amed_readings (
                id, plot_id, fetch_date,
                crop_type_detected, crop_type_confidence,
                field_size_acres_amed
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                plot_id,
                fetch_date,
                crop,
                confidence,
                acres,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _good_amed_data(
    crop: str = "Grapes",
    confidence: float = 0.91,
    acres: float = 3.1,
) -> dict:
    return {
        "crop_type_detected": crop,
        "crop_type_confidence": confidence,
        "field_size_acres": acres,
    }


# ---------------------------------------------------------------------------
# Trigger decision-tree tests
# ---------------------------------------------------------------------------


def test_skipped_when_already_collected(seeded_db, stub_webhook, fixed_clock):
    from pipelines.variety_trigger import trigger_variety_collection_if_needed

    _set_farmer(seeded_db, SEED_FARMER_GRAPES_ID, amed_variety_collected=1)

    decision = trigger_variety_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        amed_data=_good_amed_data(),
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "already_collected"
    assert decision["session_id"] is None
    assert stub_webhook == []


def test_max_retries_flags_agent_required(seeded_db, stub_webhook, fixed_clock):
    from pipelines.variety_trigger import trigger_variety_collection_if_needed

    _set_farmer(seeded_db, SEED_FARMER_GRAPES_ID, variety_collection_attempts=3)

    decision = trigger_variety_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        amed_data=_good_amed_data(),
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "max_retries"
    assert stub_webhook == []

    farmer = _get_farmer(seeded_db, SEED_FARMER_GRAPES_ID)
    assert farmer["variety_collection_status"] == "AGENT_REQUIRED"


def test_low_confidence_skip(seeded_db, stub_webhook, fixed_clock):
    from pipelines.variety_trigger import trigger_variety_collection_if_needed

    decision = trigger_variety_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        amed_data=_good_amed_data(confidence=0.5),
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "low_confidence"
    assert stub_webhook == []
    # No attempt was recorded.
    farmer = _get_farmer(seeded_db, SEED_FARMER_GRAPES_ID)
    assert farmer["variety_collection_attempts"] == 0


def test_no_crop_detected_skip(seeded_db, stub_webhook, fixed_clock):
    from pipelines.variety_trigger import trigger_variety_collection_if_needed

    decision = trigger_variety_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        amed_data={"crop_type_detected": None, "crop_type_confidence": 0.95},
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "no_crop_detected"
    assert stub_webhook == []


def test_too_soon_skip(seeded_db, stub_webhook, fixed_clock):
    from pipelines.variety_trigger import trigger_variety_collection_if_needed

    two_hours_ago = (fixed_clock - timedelta(hours=2)).isoformat()
    _set_farmer(
        seeded_db,
        SEED_FARMER_GRAPES_ID,
        variety_collection_attempted_at=two_hours_ago,
        variety_collection_attempts=1,
    )

    decision = trigger_variety_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        amed_data=_good_amed_data(),
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )
    assert decision["action"] == "too_soon"
    assert stub_webhook == []
    farmer = _get_farmer(seeded_db, SEED_FARMER_GRAPES_ID)
    # Attempt counter must not have moved.
    assert farmer["variety_collection_attempts"] == 1


def test_happy_path_sent(seeded_db, stub_webhook, fixed_clock):
    from pipelines.variety_trigger import trigger_variety_collection_if_needed

    decision = trigger_variety_collection_if_needed(
        farmer_id=SEED_FARMER_GRAPES_ID,
        plot_id=SEED_PLOT_GRAPES_ID,
        amed_data=_good_amed_data(),
        db_path=seeded_db,
        clock=lambda: fixed_clock,
    )

    assert decision["action"] == "sent"
    assert decision["session_id"] == "test-sess-123"
    assert len(stub_webhook) == 1
    call = stub_webhook[0]
    assert call["farmer_id"] == SEED_FARMER_GRAPES_ID
    assert call["plot_id"] == SEED_PLOT_GRAPES_ID
    assert call["amed_crop"] == "Grapes"
    assert pytest.approx(call["amed_confidence"], rel=1e-6) == 0.91
    assert pytest.approx(call["amed_acres"], rel=1e-6) == 3.1

    farmer = _get_farmer(seeded_db, SEED_FARMER_GRAPES_ID)
    assert farmer["variety_collection_attempts"] == 1
    assert farmer["variety_collection_attempted_at"] is not None
    # The stored timestamp should round-trip back to our fixed clock.
    stored = datetime.fromisoformat(
        farmer["variety_collection_attempted_at"].replace("Z", "+00:00")
    )
    assert stored == fixed_clock
    assert farmer["variety_collection_status"] == "AWAITING_REPLY"


# ---------------------------------------------------------------------------
# Internal HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def fastapi_client(seeded_db, stub_webhook, monkeypatch):
    """Build a TestClient against main.app pointing at the seeded DB.

    We point routes.internal at our temp DB via the
    ``SHETMITRA_DB_PATH`` env variable that the endpoint already reads.
    """
    monkeypatch.setenv("SHETMITRA_DB_PATH", seeded_db)
    # No auth in tests.
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)

    # Reload main so the route registrations pick up our stubbed
    # ``api.webhooks_variety``. Without this the FIRST test to import
    # main caches a half-mounted app.
    import main as main_mod
    importlib.reload(main_mod)

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        yield client


def test_internal_endpoint_returns_decision(seeded_db, fastapi_client):
    # Seed a strong AMED reading for the Grapes farmer's plot.
    _insert_amed_reading(
        seeded_db,
        plot_id=SEED_PLOT_GRAPES_ID,
        crop="Grapes",
        confidence=0.91,
        acres=3.1,
    )
    resp = fastapi_client.post(
        "/internal/trigger-variety-collection",
        json={"farmer_id": SEED_FARMER_GRAPES_ID},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "action" in body
    assert body["action"] == "sent"
    assert body.get("session_id") == "test-sess-123"


def test_internal_endpoint_400_when_no_amed(seeded_db, fastapi_client):
    resp = fastapi_client.post(
        "/internal/trigger-variety-collection",
        json={"farmer_id": SEED_FARMER_POMEGRANATE_ID},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "No AMED data" in body.get("detail", "")
