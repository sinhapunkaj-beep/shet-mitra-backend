"""Tests for pipelines/signal_engine.py - SDD 5.1."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.signal_engine import (  # noqa: E402
    calculate_fair_value,
    generate_signal,
)


# --------------------------------------------------------------------------- #
# Provider factories
# --------------------------------------------------------------------------- #
def _stub_belt(volume_mt: float = 800.0, health_pct_good: float = 85.0) -> dict:
    return {
        "health_pct_good": health_pct_good,
        "estimated_volume_mt": volume_mt,
        "fields_harvesting": 12,
        "total_area_acres": 100.0,
    }


def _stub_historical(week_avg: float = 130.0, avg_volume_mt: float = 1000.0,
                     seasonal_index: float = 1.0) -> dict:
    return {
        "week_avg": week_avg,
        "avg_volume_mt": avg_volume_mt,
        "seasonal_index": seasonal_index,
    }


def _stub_weather() -> dict:
    return {"summary_7day": "Clear with brief showers possible Thu-Fri"}


def _stub_market_provider(current_price: float):
    def _mp(commodity: str) -> float:  # noqa: ARG001
        return current_price
    return _mp


def _stub_price_predictor(day1: float, day3: float, day7: float,
                          confidence: float = 0.7):
    def _pp(commodity, **kwargs):  # noqa: ARG001
        return {"day1": day1, "day3": day3, "day7": day7,
                "confidence_day3": confidence}
    return _pp


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_buy_signal_when_discount_above_10():
    """Discount > 10% AND day3 > current * 1.05 -> BUY."""

    current = 100.0
    # Need fair_value such that discount > 10%. With historical_avg=130
    # quality=0.85, seasonal=1.0, supply=800 vs hist_avg_vol=1000:
    # supply_correction = 1 - 0.0001*(800-1000) = 1.02
    # fair_value ~= 130 * 1.0 * (0.9 + 0.2*0.85) * 1.02 ~= 141.5
    # discount ~= (141.5-100)/141.5 ~= 29% > 10
    result = generate_signal(
        "Dry Grapes",
        belt_provider=lambda c: _stub_belt(volume_mt=800.0),
        historical_provider=lambda c: _stub_historical(),
        weather_provider=lambda c: _stub_weather(),
        market_provider=_stub_market_provider(current),
        price_predictor=_stub_price_predictor(day1=102, day3=107, day7=112),
    )
    assert result["signal"] == "BUY", (
        f"expected BUY got {result['signal']} (discount={result['discount_pct']:.1f})"
    )
    assert result["discount_pct"] > 10
    assert "below fair value" in result["rationale"].lower()


def test_sell_signal_when_surplus_above_15pct():
    """day3 < current*0.97 AND volume > hist_avg * 1.15 -> SELL."""

    current = 130.0
    # Use volume = 1200 vs avg 1000 -> +20% surplus.
    # quality high so fair_value stays close to current; avoid BUY branch.
    result = generate_signal(
        "Dry Grapes",
        belt_provider=lambda c: _stub_belt(volume_mt=1200.0, health_pct_good=80.0),
        historical_provider=lambda c: _stub_historical(
            week_avg=130.0, avg_volume_mt=1000.0
        ),
        weather_provider=lambda c: _stub_weather(),
        market_provider=_stub_market_provider(current),
        price_predictor=_stub_price_predictor(day1=128, day3=120, day7=115),
    )
    assert result["signal"] == "SELL", (
        f"expected SELL got {result['signal']} "
        f"(day3={120}, current={current}, discount={result['discount_pct']:.1f})"
    )
    assert "above-average supply" in result["rationale"].lower()


def test_hold_signal_neutral():
    """Small movements with normal supply -> HOLD."""

    current = 130.0
    result = generate_signal(
        "Dry Grapes",
        belt_provider=lambda c: _stub_belt(volume_mt=1000.0, health_pct_good=70.0),
        historical_provider=lambda c: _stub_historical(
            week_avg=130.0, avg_volume_mt=1000.0
        ),
        weather_provider=lambda c: _stub_weather(),
        market_provider=_stub_market_provider(current),
        price_predictor=_stub_price_predictor(day1=131, day3=131, day7=131),
    )
    assert result["signal"] == "HOLD", (
        f"expected HOLD got {result['signal']} "
        f"(discount={result['discount_pct']:.1f})"
    )
    assert "no strong directional signal" in result["rationale"].lower()


def test_calculate_fair_value_premium_quality():
    """Higher quality_adj should produce a higher fair value than the base."""

    base = calculate_fair_value(
        historical_avg=100.0,
        quality_adj=0.5,
        supply_adj=1000.0,
        seasonal_factor=1.0,
        historical_avg_volume_mt=1000.0,
    )
    premium = calculate_fair_value(
        historical_avg=100.0,
        quality_adj=0.9,
        supply_adj=1000.0,
        seasonal_factor=1.0,
        historical_avg_volume_mt=1000.0,
    )
    assert premium > base
    assert premium > 100.0, (
        f"premium-quality fair value should be > historical avg "
        f"(got {premium})"
    )


def test_signal_dict_shape():
    """The returned dict must contain all 9 SDD keys."""

    result = generate_signal(
        "Dry Grapes",
        belt_provider=lambda c: _stub_belt(),
        historical_provider=lambda c: _stub_historical(),
        weather_provider=lambda c: _stub_weather(),
        market_provider=_stub_market_provider(120.0),
        price_predictor=_stub_price_predictor(120, 121, 122),
    )
    expected = {
        "signal", "rationale", "entry_range", "target", "stop",
        "horizon_days", "confidence", "fair_value", "discount_pct",
    }
    assert expected.issubset(result.keys()), (
        f"missing keys: {expected - set(result.keys())}"
    )
    assert isinstance(result["entry_range"], list) and len(result["entry_range"]) == 2
