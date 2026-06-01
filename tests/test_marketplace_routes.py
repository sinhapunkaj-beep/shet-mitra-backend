"""tests/test_marketplace_routes.py — SDD §8

End-to-end tests for the marketplace FastAPI surface. Each test runs
against a fresh tmp SQLite DB with the marketplace tables created
in-memory (mirroring the migration 009 schema for SQLite). The matching
engine is monkeypatched to avoid pulling the full pipeline.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import whatsapp_db


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


_MARKETPLACE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS regions (
      region_code TEXT PRIMARY KEY,
      region_name TEXT,
      whatsapp_sender_name TEXT,
      default_language TEXT,
      primary_crops TEXT,
      primary_mandis TEXT,
      is_active INTEGER DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trader_requirements (
      id TEXT PRIMARY KEY,
      trader_id TEXT,
      region_code TEXT,
      commodity TEXT,
      variety TEXT,
      quantity_kg_min REAL,
      quantity_kg_max REAL,
      grade TEXT,
      price_per_kg_offered REAL,
      collection_from TEXT,
      collection_to TEXT,
      location_district TEXT,
      location_state TEXT,
      farm_pickup INTEGER DEFAULT 1,
      gi_required INTEGER DEFAULT 0,
      status TEXT DEFAULT 'ACTIVE',
      matched_count INTEGER DEFAULT 0,
      created_at TEXT,
      expires_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS farmer_lots (
      id TEXT PRIMARY KEY,
      lot_ref TEXT UNIQUE,
      farmer_id TEXT,
      plot_id TEXT,
      region_code TEXT,
      commodity TEXT,
      variety TEXT,
      quantity_kg_estimated REAL,
      quantity_kg_min_acceptable REAL,
      grade_predicted TEXT,
      brix_estimated_min REAL,
      brix_estimated_max REAL,
      harvest_date_from TEXT,
      harvest_date_to TEXT,
      farm_district TEXT,
      farm_state TEXT,
      centroid_lat REAL,
      centroid_lng REAL,
      satellite_verified INTEGER DEFAULT 1,
      amed_verified INTEGER DEFAULT 0,
      gi_verified INTEGER DEFAULT 0,
      gi_certificate_ref TEXT,
      min_price_per_kg REAL,
      auto_created INTEGER DEFAULT 1,
      status TEXT DEFAULT 'AVAILABLE',
      created_at TEXT,
      expires_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lot_matches (
      id TEXT PRIMARY KEY,
      lot_id TEXT,
      requirement_id TEXT,
      farmer_id TEXT,
      trader_id TEXT,
      region_code TEXT,
      commodity TEXT,
      match_score REAL,
      match_reasons TEXT,
      farmer_notified_at TEXT,
      farmer_response TEXT,
      farmer_counter_price REAL,
      farmer_responded_at TEXT,
      trader_notified_at TEXT,
      trader_response TEXT,
      trader_counter_price REAL,
      connection_made INTEGER DEFAULT 0,
      connection_made_at TEXT,
      created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS farmer_trades (
      id TEXT PRIMARY KEY,
      lot_id TEXT,
      farmer_id TEXT,
      trader_id TEXT,
      match_id TEXT,
      region_code TEXT,
      commodity TEXT,
      variety TEXT,
      quantity_kg_actual REAL,
      price_per_kg_actual REAL,
      total_value REAL,
      mandi_price_same_day REAL,
      premium_achieved_pct REAL,
      trade_date TEXT,
      payment_mode TEXT,
      razorpay_payment_id TEXT,
      platform_fee_pct REAL DEFAULT 2.0,
      platform_fee_amount REAL,
      confirmed_by_farmer INTEGER DEFAULT 0,
      confirmed_by_trader INTEGER DEFAULT 0,
      gi_premium_applied INTEGER DEFAULT 0,
      created_at TEXT
    )
    """,
]


@pytest.fixture
def tmp_db(tmp_path: Path):
    db_path = tmp_path / "marketplace.db"
    conn = sqlite3.connect(str(db_path))
    for ddl in _MARKETPLACE_DDL:
        conn.execute(ddl)
    conn.execute(
        "INSERT INTO regions (region_code, region_name, whatsapp_sender_name, "
        "default_language, primary_crops, primary_mandis, is_active) "
        "VALUES ('MH', 'Maharashtra', 'ShetMitra', 'Marathi', "
        "'[\"Grapes\",\"Mango\"]', '[\"Tasgaon APMC\"]', 1)"
    )
    conn.execute(
        "INSERT INTO regions (region_code, region_name, whatsapp_sender_name, "
        "default_language, primary_crops, primary_mandis, is_active) "
        "VALUES ('JH', 'Jharkhand', 'Bagaan Sathi', 'Hindi', "
        "'[\"Mango\"]', '[\"Bhagalpur APMC\"]', 1)"
    )
    conn.commit()
    conn.close()

    whatsapp_db.set_db_path(db_path)
    yield db_path
    whatsapp_db.reset_db_path()


@pytest.fixture
def client(tmp_db, monkeypatch):
    """FastAPI TestClient with marketplace + regions + gi routers mounted.

    We build a minimal app per-test so we don't drag in the full lifespan
    (scheduler startup, etc.).
    """
    from fastapi import FastAPI

    from routes.marketplace import router as marketplace_router
    from routes.regions import router as regions_router
    from routes.gi import router as gi_router

    # Stub the matching engine so create_lot doesn't import the real one
    # mid-test if it lands on disk.
    import sys
    fake = type(sys)("matching_engine_stub")
    fake.run_daily_matching = lambda: {"status": "stub", "matches": 0}
    monkeypatch.setitem(sys.modules, "pipelines.matching_engine", fake)

    app = FastAPI()
    app.include_router(marketplace_router)
    app.include_router(regions_router)
    app.include_router(gi_router)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# /regions
# --------------------------------------------------------------------------- #


def test_list_regions_returns_seeded_rows(client):
    resp = client.get("/regions")
    assert resp.status_code == 200
    rows = resp.json()
    codes = {r["region_code"] for r in rows}
    assert {"MH", "JH"} <= codes
    senders = {r["whatsapp_sender_name"] for r in rows}
    assert {"ShetMitra", "Bagaan Sathi"} <= senders


# --------------------------------------------------------------------------- #
# /marketplace/lots
# --------------------------------------------------------------------------- #


def test_create_lot_returns_lot_ref(client):
    payload = {
        "farmer_id": "farmer-mh-1",
        "region_code": "MH",
        "commodity": "Grapes",
        "variety": "Thompson Seedless",
        "quantity_kg_estimated": 1200.0,
        "grade_predicted": "A",
        "harvest_date_from": "2026-12-15",
        "harvest_date_to": "2026-12-22",
        "min_price_per_kg": 45.0,
    }
    resp = client.post("/marketplace/lots", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "AVAILABLE"
    assert body["lot_ref"].startswith("LOT-")
    assert "id" in body


def test_list_lots_filters_by_region_and_commodity(client):
    # seed two lots
    client.post("/marketplace/lots", json={
        "farmer_id": "f1", "region_code": "MH", "commodity": "Grapes",
        "quantity_kg_estimated": 1000, "harvest_date_from": "2026-12-01",
        "harvest_date_to": "2026-12-07",
    })
    client.post("/marketplace/lots", json={
        "farmer_id": "f2", "region_code": "JH", "commodity": "Mango",
        "variety": "Jardalu", "quantity_kg_estimated": 800,
        "harvest_date_from": "2026-05-15", "harvest_date_to": "2026-05-22",
    })

    rows_mh = client.get("/marketplace/lots", params={"region": "MH"}).json()
    rows_jh = client.get("/marketplace/lots", params={"region": "JH"}).json()
    assert all(r["region_code"] == "MH" for r in rows_mh)
    assert all(r["region_code"] == "JH" for r in rows_jh)

    mango_only = client.get(
        "/marketplace/lots", params={"commodity": "Mango"}
    ).json()
    assert all(r["commodity"] == "Mango" for r in mango_only)


# --------------------------------------------------------------------------- #
# /marketplace/requirements
# --------------------------------------------------------------------------- #


def test_create_requirement_returns_id(client):
    resp = client.post(
        "/marketplace/requirements",
        json={
            "trader_id": "trader-bhagalpur-1",
            "region_code": "JH",
            "commodity": "Mango",
            "variety": "Jardalu",
            "quantity_kg_min": 500,
            "quantity_kg_max": 2000,
            "grade": ["A"],
            "price_per_kg_offered": 60.0,
            "collection_from": "2026-05-15",
            "collection_to": "2026-06-05",
            "location_district": "Bhagalpur",
            "location_state": "Bihar",
            "gi_required": True,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert "id" in body


# --------------------------------------------------------------------------- #
# /marketplace/matches/{lot_id}/respond
# --------------------------------------------------------------------------- #


def test_match_respond_flow_connects_when_both_accept(client, tmp_db):
    # Manually insert a match row so we can drive the respond endpoint.
    match_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO lot_matches (id, lot_id, requirement_id, farmer_id, "
        "trader_id, region_code, commodity, match_score, created_at) "
        "VALUES (?, 'lot-1', 'req-1', 'farmer-1', 'trader-1', 'MH', 'Grapes', 0.85, '2026-12-01')",
        (match_id,),
    )
    conn.commit()
    conn.close()

    r1 = client.post(
        f"/marketplace/matches/{match_id}/respond",
        json={"actor": "farmer", "response": "ACCEPTED"},
    )
    assert r1.status_code == 200

    r2 = client.post(
        f"/marketplace/matches/{match_id}/respond",
        json={"actor": "trader", "response": "ACCEPTED"},
    )
    assert r2.status_code == 200

    # Check connection_made flipped.
    conn = sqlite3.connect(str(tmp_db))
    cur = conn.execute(
        "SELECT connection_made FROM lot_matches WHERE id = ?", (match_id,)
    )
    row = cur.fetchone()
    conn.close()
    assert row[0] == 1


def test_match_respond_404_for_missing_match(client):
    resp = client.post(
        "/marketplace/matches/does-not-exist/respond",
        json={"actor": "farmer", "response": "ACCEPTED"},
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# /marketplace/trades/confirm
# --------------------------------------------------------------------------- #


def test_confirm_trade_creates_trade_with_platform_fee(client, tmp_db):
    match_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO lot_matches (id, lot_id, requirement_id, farmer_id, "
        "trader_id, region_code, commodity, match_score, created_at) "
        "VALUES (?, 'lot-99', 'req-99', 'farmer-9', 'trader-9', 'MH', 'Grapes', 0.85, '2026-12-01')",
        (match_id,),
    )
    conn.commit()
    conn.close()

    resp = client.post(
        "/marketplace/trades/confirm",
        json={
            "match_id": match_id,
            "actual_price_per_kg": 50.0,
            "actual_quantity_kg": 1000.0,
            "payment_mode": "razorpay",
            "confirmed_by": "both",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_value"] == 50000.0
    assert body["platform_fee_amount"] == 1000.0  # 2% of 50000


# --------------------------------------------------------------------------- #
# /marketplace/analytics
# --------------------------------------------------------------------------- #


def test_analytics_returns_zero_counts_on_empty_db(client):
    resp = client.get("/marketplace/analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_lots"] == 0
    assert body["active_requirements"] == 0
    assert body["trades_completed"] == 0


def test_analytics_reflects_inserted_data(client):
    client.post("/marketplace/lots", json={
        "farmer_id": "f1", "region_code": "MH", "commodity": "Grapes",
        "quantity_kg_estimated": 2000, "harvest_date_from": "2026-12-01",
        "harvest_date_to": "2026-12-07",
    })
    client.post("/marketplace/requirements", json={
        "trader_id": "t1", "region_code": "MH", "commodity": "Grapes",
        "quantity_kg_min": 500, "collection_from": "2026-12-01",
        "collection_to": "2026-12-10",
    })

    body = client.get("/marketplace/analytics").json()
    assert body["active_lots"] == 1
    assert body["total_volume_kg"] == 2000.0
    assert body["active_requirements"] == 1


# --------------------------------------------------------------------------- #
# /internal/run-matching
# --------------------------------------------------------------------------- #


def test_run_matching_invokes_pipeline(client):
    """The endpoint must call into matching_engine and return its summary dict.

    We can't reliably swap the module via sys.modules once another test
    has imported it (Python caches the submodule attribute on the package),
    so we only assert the response shape — the matching engine itself is
    covered by tests/test_matching_engine.py.
    """
    resp = client.post("/internal/run-matching")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
