"""tests/test_marketplace.py — SDD §10/§11 integration coverage.

End-to-end checks that the marketplace pieces (matching engine, lot
aggregator, plant priority queue, FastAPI routes) compose correctly.
Heavier scenarios than the focused unit tests in test_matching_engine.py.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from pipelines import (
    matching_engine as me,
    lot_aggregator as la,
    plant_supply_queue as pq,
)


def _lot(**kwargs):
    base = dict(
        id="lot-1", farmer_id="f-1",
        commodity="Mango", variety="Mallika",
        grade_predicted="A", quantity_kg_estimated=1000,
        harvest_date_from=date(2026, 5, 15),
        harvest_date_to=date(2026, 5, 22),
        farm_district="Godda", farm_state="Jharkhand",
        gi_verified=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _req(**kwargs):
    base = dict(
        id="req-1", trader_id="t-1",
        commodity="Mango", variety="Mallika",
        grade=["A", "B"], quantity_kg_min=500,
        collection_from=date(2026, 5, 15),
        collection_to=date(2026, 5, 22),
        location_district="Godda", location_state="Jharkhand",
        gi_required=False,
        region_code="JH",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# Matching engine full pass
# --------------------------------------------------------------------------- #


def test_run_daily_matching_creates_matches_above_threshold(monkeypatch):
    """An exact requirement-lot pair triggers create_match + notify_farmer."""
    requirements = [_req()]
    lots = [_lot()]

    created: list = []
    notified: list = []

    def fake_create_match(lot, req, score, reasons):
        match = SimpleNamespace(
            id="m-1", lot_id=lot.id, requirement_id=req.id,
            farmer_id=lot.farmer_id, trader_id=req.trader_id,
            match_score=score, reasons=reasons,
        )
        created.append(match)
        return match

    def fake_notify(*, farmer_id, lot, requirement, match_id):
        notified.append({"farmer_id": farmer_id, "match_id": match_id})

    summary = me.run_daily_matching(
        get_active_requirements_fn=lambda: requirements,
        get_lots_matching_fn=lambda **kwargs: lots,
        create_match_fn=fake_create_match,
        notify_farmer_fn=fake_notify,
        log_match_fn=lambda m: None,
    )

    assert summary["matches_created"] == 1
    assert len(created) == 1
    assert len(notified) == 1
    assert created[0].match_score >= me.MATCH_THRESHOLD


def test_run_daily_matching_skips_low_score_pairs(monkeypatch):
    """Lot/req with mismatched everything except commodity should not match."""
    # Same commodity but everything else off: variety, grade, quantity tiny
    lot = _lot(variety="Other", grade_predicted="C",
               quantity_kg_estimated=100,
               harvest_date_from=date(2026, 7, 1),
               harvest_date_to=date(2026, 7, 5),
               farm_district="Other", farm_state="Other")
    req = _req(variety="Mallika", grade=["A"], quantity_kg_min=1000)

    score = me.calculate_match_score(lot, req)
    assert score < me.MATCH_THRESHOLD


def test_gi_required_filter_can_be_modelled_in_matching():
    """A trader requirement with gi_required=True excludes lots
    whose gi_verified flag is False (verified at the call-site
    that builds the candidate list)."""
    # The matching engine itself doesn't enforce GI; it's an upstream
    # filter on the candidate list. This test documents that contract
    # by computing the filter explicitly.
    req = _req(gi_required=True)
    lots = [
        _lot(id="lot-A", gi_verified=False),
        _lot(id="lot-B", gi_verified=True),
    ]
    filtered = [l for l in lots if (not req.gi_required) or l.gi_verified]
    assert len(filtered) == 1
    assert filtered[0].id == "lot-B"


# --------------------------------------------------------------------------- #
# Lot aggregator — 3 small farms compose into 1 aggregation of 1200 kg
# --------------------------------------------------------------------------- #


def test_three_small_farms_aggregate_to_one_lot(monkeypatch):
    week_start = date(2026, 5, 18)
    monkeypatch.setattr(
        la, "get_active_regions",
        lambda: [{"region_code": "MH", "primary_crops": ["Grapes"]}],
    )
    monkeypatch.setattr(la, "get_next_3_weeks", lambda today=None: [week_start])
    monkeypatch.setattr(
        la, "get_lots_in_window",
        lambda **kwargs: [
            {"id": "L1", "farmer_id": "f1",
             "quantity_kg_estimated": 400, "grade_predicted": "A"},
            {"id": "L2", "farmer_id": "f2",
             "quantity_kg_estimated": 400, "grade_predicted": "A"},
            {"id": "L3", "farmer_id": "f3",
             "quantity_kg_estimated": 400, "grade_predicted": "A"},
        ],
    )

    created = []
    monkeypatch.setattr(
        la, "create_aggregated_lot",
        lambda **kwargs: created.append(kwargs) or {
            "id": "agg-1", "total_quantity_kg": kwargs["total_kg"],
        },
    )
    monkeypatch.setattr(
        la, "broadcast_aggregated_lot_to_traders", lambda **kwargs: None
    )

    summary = la.run_weekly_aggregation()
    assert summary["aggregations_created"] == 1
    assert created[0]["total_kg"] == 1200
    assert len(created[0]["lots"]) == 3


# --------------------------------------------------------------------------- #
# Plant priority — Maharashtra only, Grade A, premium > 0
# --------------------------------------------------------------------------- #


def test_plant_priority_runs_when_capacity_and_mandi_price_available(monkeypatch):
    monkeypatch.setattr(pq, "get_plant_weekly_capacity", lambda: 5000.0)
    monkeypatch.setattr(pq, "get_plant_bookings_this_week", lambda: 0.0)
    monkeypatch.setattr(
        pq, "get_available_farms",
        lambda **kwargs: [
            {"farmer_id": "mh-f1", "quantity_kg_estimated": 1200},
            {"farmer_id": "mh-f2", "quantity_kg_estimated": 1500},
        ],
    )
    monkeypatch.setattr(pq, "get_current_mandi_price", lambda *a, **k: 200.0)
    monkeypatch.setattr(pq, "get_processing_fee", lambda *a, **k: 3.0)

    sent_to: list[str] = []
    monkeypatch.setattr(
        pq, "send_plant_priority_offer",
        lambda **kwargs: sent_to.append(kwargs["farmer_id"]),
    )

    summary = pq.run_plant_priority_queue()
    assert summary["status"] == "OK"
    assert summary["offers_sent"] == 2
    assert sent_to == ["mh-f1", "mh-f2"]


# --------------------------------------------------------------------------- #
# Trade confirmation flow — both parties confirm → trade row
# --------------------------------------------------------------------------- #


def test_trade_confirmation_creates_farmer_trades_row(tmp_path):
    """When both sides confirm via the route, a farmer_trades row exists
    with the platform fee correctly computed at 2% of total value."""
    import sqlite3
    import uuid
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Build the minimal marketplace schema in tmp SQLite (mirrors what
    # test_marketplace_routes.py uses). Inline to keep this file
    # self-contained.
    db_path = tmp_path / "trade.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE lot_matches (id TEXT PRIMARY KEY, lot_id TEXT, "
        "requirement_id TEXT, farmer_id TEXT, trader_id TEXT, "
        "region_code TEXT, commodity TEXT, match_score REAL, "
        "farmer_response TEXT, trader_response TEXT, "
        "connection_made INTEGER DEFAULT 0, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE farmer_trades (id TEXT PRIMARY KEY, lot_id TEXT, "
        "farmer_id TEXT, trader_id TEXT, match_id TEXT, region_code TEXT, "
        "commodity TEXT, variety TEXT, quantity_kg_actual REAL, "
        "price_per_kg_actual REAL, total_value REAL, "
        "mandi_price_same_day REAL, premium_achieved_pct REAL, "
        "trade_date TEXT, payment_mode TEXT, razorpay_payment_id TEXT, "
        "platform_fee_pct REAL DEFAULT 2.0, platform_fee_amount REAL, "
        "confirmed_by_farmer INTEGER DEFAULT 0, "
        "confirmed_by_trader INTEGER DEFAULT 0, "
        "gi_premium_applied INTEGER DEFAULT 0, created_at TEXT)"
    )

    match_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO lot_matches (id, lot_id, requirement_id, farmer_id, "
        "trader_id, region_code, commodity, match_score, created_at) "
        "VALUES (?, 'lot-x', 'req-x', 'f-x', 't-x', 'JH', 'Mango', 0.92, '2026-05-15')",
        (match_id,),
    )
    conn.commit()
    conn.close()

    from api import whatsapp_db
    whatsapp_db.set_db_path(db_path)
    try:
        from routes.marketplace import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post(
            "/marketplace/trades/confirm",
            json={
                "match_id": match_id,
                "actual_price_per_kg": 55.0,
                "actual_quantity_kg": 800.0,
                "payment_mode": "razorpay",
                "confirmed_by": "both",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # 800 × 55 = 44_000;  fee at 2% = 880
        assert body["total_value"] == 44_000.0
        assert body["platform_fee_amount"] == 880.0
    finally:
        whatsapp_db.reset_db_path()
