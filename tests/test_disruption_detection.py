"""Tests for the 4-step Dry Grapes accuracy improvement:
  - spike-window exclusion in train_price_model
  - 15% disruption detection in utils/price_prediction
  - signal_engine forces HOLD with trader message on disruption
  - USDA world raisin production CSV is wired correctly
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Spike exclusion
# ---------------------------------------------------------------------------
def test_apply_spike_exclusion_drops_mar_may_2025_rows():
    from scripts.train_price_model import _apply_spike_exclusion

    df = pd.DataFrame({
        "date": pd.to_datetime([
            "2024-12-01", "2025-01-15", "2025-03-15", "2025-04-15",
            "2025-05-15", "2025-06-15", "2026-01-01",
        ]),
        "commodity": "Dry_Grapes",
        "price_modal_kg": [120, 130, 200, 280, 250, 150, 180],
    })
    out, dropped = _apply_spike_exclusion(df, "Dry_Grapes")
    keep_dates = out["date"].dt.strftime("%Y-%m-%d").tolist()
    assert "2025-03-15" not in keep_dates
    assert "2025-04-15" not in keep_dates
    assert "2025-05-15" not in keep_dates
    assert "2024-12-01" in keep_dates  # outside the window
    assert "2026-01-01" in keep_dates
    assert dropped >= 3


def test_apply_spike_exclusion_drops_extreme_pct_change_anywhere():
    """A >50% 4-week pct change outside the static window should ALSO be dropped."""
    from scripts.train_price_model import _apply_spike_exclusion

    df = pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=8, freq="W"),
        "commodity": "Dry_Grapes",
        "price_modal_kg": [100, 100, 100, 100, 180, 100, 100, 100],  # +80% week 5
    })
    out, dropped = _apply_spike_exclusion(df, "Dry_Grapes")
    assert dropped >= 1


def test_apply_spike_exclusion_no_op_for_pomegranate():
    """Pomegranate has no static spike window — should drop only by pct change."""
    from scripts.train_price_model import _apply_spike_exclusion

    df = pd.DataFrame({
        "date": pd.date_range("2025-03-01", periods=4, freq="W"),
        "commodity": "Pomegranate",
        "price_modal_kg": [50, 52, 51, 53],
    })
    out, dropped = _apply_spike_exclusion(df, "Pomegranate")
    assert dropped == 0


# ---------------------------------------------------------------------------
# USDA world production wiring
# ---------------------------------------------------------------------------
def test_usda_csv_exists_and_has_expected_shape():
    csv = ROOT / "data" / "usda_world_raisin_production.csv"
    assert csv.exists(), "Run scripts/fetch_usda_raisin_production.py first."
    df = pd.read_csv(csv)
    assert {"year", "world_production_mt", "source"}.issubset(df.columns)
    assert df["year"].min() <= 2015
    assert df["year"].max() >= 2026
    # All MT values are in a sane range (0.9M - 1.6M)
    assert df["world_production_mt"].between(900_000, 1_600_000).all()


def test_join_world_production_adds_column():
    from scripts.train_price_model import _join_world_production

    df = pd.DataFrame({
        "year": [2018, 2020, 2025],
        "commodity": ["Dry_Grapes"] * 3,
        "price_modal_kg": [120, 110, 200],
    })
    out = _join_world_production(df)
    assert "world_production_mt" in out.columns
    # 2018 (Turkey drought) should have lower production than 2025
    val_2018 = float(out.loc[out["year"] == 2018, "world_production_mt"].iloc[0])
    val_2024 = None  # check the file uses real 2018 dip
    assert val_2018 < 1_200_000  # drought year is below average


# ---------------------------------------------------------------------------
# Disruption detection in predict_price
# ---------------------------------------------------------------------------
def _make_predictor_with_stubbed_forecast(day1: float, day3: float = None, day7: float = None):
    """Return a PricePredictor instance whose forecast functions return fixed
    prices, regardless of underlying model. Avoids needing a real pickle.
    """
    from utils.price_prediction import PricePredictor
    import numpy as np

    p = PricePredictor.__new__(PricePredictor)
    # Minimal init: just what predict_price reads.
    p._models_dir = ROOT / "data" / "models"

    class _StubBundle:
        version = "stub"
        model_kind = "random_forest"
        residual_std = 5.0
        mean_price = 150.0
        feature_medians = {"amed_belt_volume_mt": 300.0}
        payload = {"variety": "default", "mape": 0.10}

    def _stub_bundle_for(commodity, variety=None):
        return _StubBundle()

    def _stub_auto_belt(commodity, n_steps=3):
        return [300.0] * n_steps

    def _stub_build_row(*a, **kw):
        return np.zeros(1)

    def _stub_rf(bundle, x):
        return np.array([day1, day3 or day1, day7 or day1])

    p._bundle_for = _stub_bundle_for
    p._auto_belt_volume_forecast = _stub_auto_belt
    p._build_feature_row = _stub_build_row
    p._forecast_random_forest = _stub_rf
    return p


def test_predict_no_disruption_when_change_below_15pct():
    p = _make_predictor_with_stubbed_forecast(day1=105.0)
    r = p.predict_price("Pomegranate", {"price_lag_1": 100.0})
    assert r["unusual_market_conditions"] is False
    assert "confidence_band" in r
    assert "disruption_message" not in r


def test_predict_disruption_when_change_above_15pct():
    p = _make_predictor_with_stubbed_forecast(day1=120.0)  # +20%
    r = p.predict_price("Pomegranate", {"price_lag_1": 100.0})
    assert r["unusual_market_conditions"] is True
    assert r["confidence_band"] == "LOW"
    assert r["confidence"] <= 0.35
    assert r["suggested_signal"] == "HOLD"
    assert "Unusual market conditions" in r["disruption_message"]
    assert "Monitor daily" in r["disruption_message"]
    assert r["disruption_delta_pct"] == pytest.approx(20.0, abs=0.1)


def test_predict_disruption_on_sharp_drop_too():
    """A 20% drop is just as unusual as a 20% rise."""
    p = _make_predictor_with_stubbed_forecast(day1=80.0)  # -20%
    r = p.predict_price("Pomegranate", {"price_lag_1": 100.0})
    assert r["unusual_market_conditions"] is True
    assert r["suggested_signal"] == "HOLD"


# ---------------------------------------------------------------------------
# Signal engine honors the disruption flag
# ---------------------------------------------------------------------------
def test_signal_engine_forces_hold_on_disruption():
    """generate_signal should clamp to HOLD when price predictor flags
    unusual_market_conditions, even if the discount math would otherwise
    suggest BUY/SELL."""

    from pipelines.signal_engine import generate_signal

    def fake_price_predictor(commodity, variety=None, market=None):
        return {
            "day1": 150.0, "day3": 200.0, "day7": 220.0,
            "confidence_day3": 0.85,
            "unusual_market_conditions": True,
            "disruption_message": (
                "Unusual market conditions detected. "
                "Standard forecast suspended. Monitor daily."
            ),
            "disruption_delta_pct": 25.0,
        }

    def fake_market_provider(c, m=None):  # current price 100, predicted 150 = BUY normally
        return 100.0

    out = generate_signal(
        "Dry_Grapes",
        price_predictor=fake_price_predictor,
        belt_provider=lambda c: {"health_pct_good": 0.6, "estimated_volume_mt": 350.0},
        historical_provider=lambda c: {
            "week_avg": 110.0, "avg_volume_mt": 300.0, "seasonal_index": 1.05,
        },
        weather_provider=lambda c: {"summary_7day": ""},
        market_provider=fake_market_provider,
    )
    assert out["signal"] == "HOLD"
    assert out["unusual_market_conditions"] is True
    assert "Unusual market conditions" in out["rationale"]
    assert out["confidence"] <= 0.35
    assert "disruption_message" in out


def test_signal_engine_normal_path_when_no_disruption():
    """Sanity: when the predictor does NOT flag disruption, signal logic runs."""

    from pipelines.signal_engine import generate_signal

    out = generate_signal(
        "Dry_Grapes",
        price_predictor=lambda c, **kw: {
            "day1": 105.0, "day3": 108.0, "day7": 110.0,
            "confidence_day3": 0.75,
            "unusual_market_conditions": False,
        },
        belt_provider=lambda c: {"health_pct_good": 0.6, "estimated_volume_mt": 300.0},
        historical_provider=lambda c: {
            "week_avg": 110.0, "avg_volume_mt": 300.0, "seasonal_index": 1.0,
        },
        weather_provider=lambda c: {"summary_7day": ""},
        market_provider=lambda c, m=None: 100.0,
    )
    assert out["signal"] in {"BUY", "HOLD", "SELL"}
    assert out["unusual_market_conditions"] is False
    assert "disruption_message" not in out
