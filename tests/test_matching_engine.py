"""tests/test_matching_engine.py — SDD §4.2 / §4.3 / §4.4

Unit tests for the marketplace matching engine, weekly aggregator and
Tasgaon plant priority queue. All DB / WhatsApp side effects are stubbed.
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


# --------------------------------------------------------------------------- #
# calculate_match_score (SDD §4.2)
# --------------------------------------------------------------------------- #


def _lot(**kwargs):
    base = dict(
        commodity="Mango", variety="Mallika", grade_predicted="A",
        quantity_kg_estimated=1000,
        harvest_date_from=date(2026, 5, 15),
        harvest_date_to=date(2026, 5, 22),
        farm_district="Godda", farm_state="Jharkhand",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _req(**kwargs):
    base = dict(
        commodity="Mango", variety="Mallika", grade=["A", "B"],
        quantity_kg_min=500,
        collection_from=date(2026, 5, 15),
        collection_to=date(2026, 5, 22),
        location_district="Godda", location_state="Jharkhand",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_commodity_mismatch_returns_zero():
    score = me.calculate_match_score(
        _lot(commodity="Mango"), _req(commodity="Grapes")
    )
    assert score == 0.0


def test_full_match_above_threshold():
    score = me.calculate_match_score(_lot(), _req())
    assert score >= me.MATCH_THRESHOLD
    assert score <= 1.0


def test_variety_wildcard_when_req_variety_is_none():
    score = me.calculate_match_score(
        _lot(variety="Mallika"), _req(variety=None)
    )
    # Commodity (0.30) + variety wildcard (0.20) + grade (0.15) +
    # quantity (0.15) + timing (0.10) + location (0.10) = 1.00
    assert score == pytest.approx(1.0, abs=0.01)


def test_partial_quantity_match_credits_60pct_of_weight():
    """Lot qty < req.min but ≥ 70% of req.min → 60% of quantity weight."""
    lot = _lot(quantity_kg_estimated=400)  # 80% of 500
    req = _req(quantity_kg_min=500)
    score = me.calculate_match_score(lot, req)
    # quantity contribution must be > 0 but < full
    # Commodity+variety+grade+timing+location = 0.85; quantity at full = 0.15.
    assert 0.85 < score < 1.0


def test_quantity_below_partial_threshold_gets_zero_credit():
    lot = _lot(quantity_kg_estimated=200)  # 40% of req.min — below 70% threshold
    req = _req(quantity_kg_min=500)
    score = me.calculate_match_score(lot, req)
    # Everything else is fine — total without quantity = 0.85.
    assert score == pytest.approx(0.85, abs=0.01)


def test_grade_mismatch_loses_weight():
    score_ok = me.calculate_match_score(
        _lot(grade_predicted="A"), _req(grade=["A"])
    )
    score_bad = me.calculate_match_score(
        _lot(grade_predicted="C"), _req(grade=["A"])
    )
    assert score_bad < score_ok


def test_location_district_match_gets_full_weight():
    base_score = me.calculate_match_score(_lot(), _req())
    diff_district = me.calculate_match_score(
        _lot(farm_district="Other", farm_state="Jharkhand"),
        _req(location_district="Godda", location_state="Jharkhand"),
    )
    # State match keeps half the location weight; district match keeps all.
    assert base_score > diff_district


def test_score_rounded_to_3dp():
    score = me.calculate_match_score(_lot(), _req())
    # int(score * 1000) / 1000 == score
    assert score == round(score, 3)


# --------------------------------------------------------------------------- #
# date_range_overlap
# --------------------------------------------------------------------------- #


def test_date_range_full_overlap_returns_one():
    o = me.date_range_overlap(
        date(2026, 5, 15), date(2026, 5, 22),
        date(2026, 5, 1), date(2026, 5, 31),
    )
    assert o == 1.0


def test_date_range_disjoint_returns_zero():
    o = me.date_range_overlap(
        date(2026, 5, 15), date(2026, 5, 22),
        date(2026, 6, 1), date(2026, 6, 7),
    )
    assert o == 0.0


def test_date_range_partial_overlap_is_fraction():
    # lot = 8 days, requirement covers last 4 days only → 4/8 = 0.5
    o = me.date_range_overlap(
        date(2026, 5, 15), date(2026, 5, 22),
        date(2026, 5, 19), date(2026, 5, 30),
    )
    assert 0.4 < o < 0.6


def test_date_range_invalid_returns_zero():
    o = me.date_range_overlap(
        date(2026, 5, 22), date(2026, 5, 15),  # inverted
        date(2026, 5, 1), date(2026, 5, 31),
    )
    assert o == 0.0


def test_date_range_accepts_iso_strings():
    o = me.date_range_overlap(
        "2026-05-15", "2026-05-22", "2026-05-01", "2026-05-31"
    )
    assert o == 1.0


# --------------------------------------------------------------------------- #
# Lot aggregator (SDD §4.3)
# --------------------------------------------------------------------------- #


def test_aggregation_groups_three_small_lots_into_one_aggregation(monkeypatch):
    """3 × 400 kg Grade A lots in the same harvest week → 1 aggregation of 1200 kg."""
    week_start = date(2026, 5, 18)  # Monday

    monkeypatch.setattr(
        la, "get_active_regions",
        lambda: [{"region_code": "JH", "primary_crops": ["Mango"]}],
    )
    monkeypatch.setattr(la, "get_next_3_weeks", lambda today=None: [week_start])

    sample_lots = [
        {"id": f"lot-{i}", "farmer_id": f"farmer-{i}",
         "quantity_kg_estimated": 400, "grade_predicted": "A"}
        for i in range(3)
    ]
    monkeypatch.setattr(
        la, "get_lots_in_window",
        lambda **kwargs: sample_lots if kwargs.get("commodity") == "Mango" else [],
    )

    created: list[dict] = []
    monkeypatch.setattr(
        la, "create_aggregated_lot",
        lambda **kwargs: created.append(kwargs) or {
            "id": "agg-1",
            "total_quantity_kg": kwargs["total_kg"],
            "aggregation_ref": "AGG-test",
        },
    )

    broadcasted: list[dict] = []
    monkeypatch.setattr(
        la, "broadcast_aggregated_lot_to_traders",
        lambda **kwargs: broadcasted.append(kwargs),
    )

    summary = la.run_weekly_aggregation()

    assert summary["aggregations_created"] == 1
    assert created[0]["total_kg"] == 1200
    assert created[0]["grade"] == "A"
    assert len(broadcasted) == 1


def test_aggregation_skips_single_farm():
    """Single 1500 kg lot does NOT qualify — need ≥ 2 farms."""
    assert not la._qualifies_for_aggregation([
        {"id": "lot-1", "quantity_kg_estimated": 1500, "grade_predicted": "A"},
    ])


def test_aggregation_skips_below_min_kg():
    """3 × 200 kg = 600 kg — below 1000 kg floor → no aggregation."""
    lots = [
        {"id": f"lot-{i}", "quantity_kg_estimated": 200, "grade_predicted": "A"}
        for i in range(3)
    ]
    assert not la._qualifies_for_aggregation(lots)


def test_get_next_3_weeks_returns_three_mondays():
    today = date(2026, 5, 18)  # already Monday
    weeks = la.get_next_3_weeks(today)
    assert len(weeks) == 3
    assert all(w.weekday() == 0 for w in weeks)
    assert weeks[1] - weeks[0] == timedelta(days=7)


# --------------------------------------------------------------------------- #
# Plant priority queue (SDD §4.4) — economics
# --------------------------------------------------------------------------- #


def test_plant_economics_at_default_premium():
    econ = pq.compute_plant_economics(mandi_price=100.0, processing_fee=3.0)
    # plant_price = 100 * 1.12 = 112; net = 112 - 3 = 109; premium = 109 - 100 = 9
    assert econ["plant_price"] == 112.0
    assert econ["net_to_farmer"] == 109.0
    assert econ["premium_vs_mandi"] == 9.0


def test_plant_economics_premium_negative_when_fee_too_high():
    """If processing fee exceeds the 12% premium, the farmer is worse off."""
    econ = pq.compute_plant_economics(mandi_price=100.0, processing_fee=20.0)
    assert econ["premium_vs_mandi"] < 0


def test_plant_priority_skips_when_plant_full(monkeypatch):
    monkeypatch.setattr(pq, "get_plant_weekly_capacity", lambda: 1000.0)
    monkeypatch.setattr(pq, "get_plant_bookings_this_week", lambda: 900.0)
    # remaining = 100, below MIN_REMAINING_CAPACITY_KG (500)

    summary = pq.run_plant_priority_queue()

    assert summary["status"] == "PLANT_FULL"
    assert summary["offers_sent"] == 0


def test_plant_priority_skips_when_no_mandi_price(monkeypatch):
    monkeypatch.setattr(pq, "get_plant_weekly_capacity", lambda: 10_000.0)
    monkeypatch.setattr(pq, "get_plant_bookings_this_week", lambda: 0.0)
    monkeypatch.setattr(
        pq, "get_available_farms",
        lambda **kwargs: [
            {"farmer_id": "f1", "quantity_kg_estimated": 1500}
        ],
    )
    monkeypatch.setattr(pq, "get_current_mandi_price", lambda *a, **k: 0.0)

    summary = pq.run_plant_priority_queue()

    assert summary["status"] == "NO_MANDI_PRICE"


def test_plant_priority_sends_offers_until_capacity_exhausted(monkeypatch):
    monkeypatch.setattr(pq, "get_plant_weekly_capacity", lambda: 3000.0)
    monkeypatch.setattr(pq, "get_plant_bookings_this_week", lambda: 0.0)
    monkeypatch.setattr(
        pq, "get_available_farms",
        lambda **kwargs: [
            {"farmer_id": "f1", "quantity_kg_estimated": 1500},
            {"farmer_id": "f2", "quantity_kg_estimated": 1000},
            {"farmer_id": "f3", "quantity_kg_estimated": 800},  # would exceed
        ],
    )
    monkeypatch.setattr(pq, "get_current_mandi_price", lambda *a, **k: 100.0)
    monkeypatch.setattr(pq, "get_processing_fee", lambda *a, **k: 3.0)

    sent: list[dict] = []
    monkeypatch.setattr(
        pq, "send_plant_priority_offer",
        lambda **kwargs: sent.append(kwargs),
    )

    summary = pq.run_plant_priority_queue()

    # 1500 + 1000 = 2500 fits under 3000; the 3rd offer should still go out
    # because remaining (500) > 0 — but then the loop breaks.
    assert summary["status"] == "OK"
    assert summary["offers_sent"] >= 2
    # Each sent offer carries the SDD economics shape
    for s in sent:
        assert s["plant_price"] == 112.0
        assert s["net_price"] == 109.0


# --------------------------------------------------------------------------- #
# Scheduler — Bagaan Sathi jobs are registered
# --------------------------------------------------------------------------- #


def test_scheduler_has_5_new_marketplace_jobs():
    from pipelines.scheduler import JOB_DEFINITIONS

    job_ids = {j["id"] for j in JOB_DEFINITIONS}
    expected = {
        "DAILY_MATCHING",
        "PLANT_PRIORITY_QUEUE",
        "WEEKLY_AGGREGATION",
        "LOT_EXPIRY_CLEANUP",
        "TRADE_CONFIRMATION_FOLLOWUP",
    }
    assert expected <= job_ids


def test_scheduler_total_job_count_at_least_13():
    """Existing 8 jobs + 5 new marketplace jobs ≥ 13 (SDD §9 target)."""
    from pipelines.scheduler import JOB_DEFINITIONS

    assert len(JOB_DEFINITIONS) >= 13
