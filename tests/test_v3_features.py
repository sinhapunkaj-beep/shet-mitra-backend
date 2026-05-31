"""Tests for v3 model improvements:
  - AMED forecast-lookahead lag features (lag1 / lag2)
  - commodity-specific seasonal features
  - outlier_flag is per-mandi
  - V3 pickles exist on disk
  - weekly report surfaces disruption note
"""

from __future__ import annotations

import pickle
from pathlib import Path

import joblib
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parent.parent


def test_v3_feature_lists_include_new_columns():
    from scripts.train_price_model import V3_FEATURES, V3_EXTRA_FEATURES

    for col in (
        "amed_belt_volume_mt_lag1",
        "amed_belt_volume_mt_lag2",
        "amed_fields_harvesting",
        "harvest_season_flag",
        "weeks_from_season_peak",
        "prev_year_same_week_price",
        "outlier_flag",
    ):
        assert col in V3_FEATURES, f"{col} missing from V3_FEATURES"
    assert set(V3_EXTRA_FEATURES) <= set(V3_FEATURES)


def test_pomegranate_harvest_season_flag_lights_in_winter_months():
    from scripts.train_price_model import engineer_features
    df = pd.DataFrame({
        "commodity": ["Pomegranate"] * 6,
        "mandi": ["Solapur"] * 6,
        "date": pd.to_datetime([
            "2024-01-15", "2024-03-15", "2024-05-15",
            "2024-08-15", "2024-11-15", "2024-12-15",
        ]),
        "price_modal_kg": [50.0] * 6,
        "arrivals_qty": [10.0] * 6,
        "amed_belt_volume_mt": [100.0] * 6,
        "amed_fields_harvesting": [50] * 6,
        "amed_health_pct_good": [0.63] * 6,
        "amed_season_timing_dev": [0.0] * 6,
    })
    out = engineer_features(df)
    flag = out["harvest_season_flag"].tolist()
    # Aug, Nov, Dec, Jan are in-season; Mar, May are not.
    assert flag == [1, 0, 0, 1, 1, 1]


def test_weeks_from_peak_is_zero_at_november():
    from scripts.train_price_model import engineer_features
    df = pd.DataFrame({
        "commodity": ["Pomegranate"] * 2,
        "mandi": ["Solapur"] * 2,
        "date": pd.to_datetime(["2024-11-15", "2024-05-15"]),
        "price_modal_kg": [50.0, 50.0],
        "arrivals_qty": [10.0, 10.0],
        "amed_belt_volume_mt": [100.0, 100.0],
        "amed_fields_harvesting": [50, 50],
        "amed_health_pct_good": [0.63, 0.63],
        "amed_season_timing_dev": [0.0, 0.0],
    })
    out = engineer_features(df).set_index("date")
    # Nov 15 is ISO week 46 = peak_iso_week, distance should be 0.
    assert int(out.loc[pd.Timestamp("2024-11-15"), "weeks_from_season_peak"]) == 0
    # May 15 is ISO week 20, distance should be >= 20.
    assert int(out.loc[pd.Timestamp("2024-05-15"), "weeks_from_season_peak"]) >= 20


def test_outlier_flag_is_computed_per_mandi():
    """Crossing a mandi boundary must NOT trigger an outlier."""
    from scripts.train_price_model import engineer_features
    df = pd.DataFrame({
        "commodity": ["Pomegranate"] * 4,
        "mandi": ["Solapur", "Solapur", "Nashik", "Nashik"],
        "date": pd.to_datetime([
            "2024-01-01", "2024-01-08",
            "2024-01-15", "2024-01-22",
        ]),
        # Solapur 50 -> 52 (no spike); Nashik first row would jump 52 -> 200
        # if computed across the boundary. Per-mandi it is the first row
        # for Nashik (no prior) and 200 -> 210 within Nashik.
        "price_modal_kg": [50.0, 52.0, 200.0, 210.0],
        "arrivals_qty": [10.0] * 4,
        "amed_belt_volume_mt": [100.0] * 4,
        "amed_fields_harvesting": [50] * 4,
        "amed_health_pct_good": [0.63] * 4,
        "amed_season_timing_dev": [0.0] * 4,
    })
    out = engineer_features(df)
    assert int(out["outlier_flag"].sum()) == 0


def test_outlier_flag_fires_inside_mandi():
    from scripts.train_price_model import engineer_features
    df = pd.DataFrame({
        "commodity": ["Pomegranate"] * 3,
        "mandi": ["Solapur"] * 3,
        "date": pd.to_datetime(["2024-01-01", "2024-01-08", "2024-01-15"]),
        "price_modal_kg": [50.0, 200.0, 210.0],   # 4x jump on row 2
        "arrivals_qty": [10.0, 10.0, 10.0],
        "amed_belt_volume_mt": [100.0, 100.0, 100.0],
        "amed_fields_harvesting": [50, 50, 50],
        "amed_health_pct_good": [0.63, 0.63, 0.63],
        "amed_season_timing_dev": [0.0, 0.0, 0.0],
    })
    out = engineer_features(df)
    assert int(out["outlier_flag"].iloc[1]) == 1


def test_v3_pickles_exist_after_training():
    pom = ROOT / "data" / "models" / "arima_pomegranate_v3.pkl"
    grape = ROOT / "data" / "models" / "arima_dry_grapes_v3.pkl"
    assert pom.exists(), "Run scripts/train_price_model.py --train-v3 Pomegranate"
    assert grape.exists(), "Run scripts/train_price_model.py --dry-grapes-ab"
    pp = joblib.load(pom)
    gp = joblib.load(grape)
    assert pp["version"] == "v3"
    assert gp["version"] == "v3"
    assert pp["mape"] < 25.0   # v3 must at least be better than baseline 88%
    assert gp["mape"] < 17.0   # v3 must beat the v2 MAPE of 17.34%


def test_weekly_report_includes_disruption_note():
    from pipelines.report_generator import generate_weekly_report_content
    signal = {
        "signal": "HOLD",
        "rationale": "Unusual market conditions detected.",
        "entry_range": [100, 110],
        "target": 0, "stop": 0, "horizon_days": 7,
        "unusual_market_conditions": True,
        "disruption_message": (
            "Unusual market conditions detected. "
            "Price forecast suspended. Monitor daily."
        ),
        "disruption_delta_pct": 22.4,
    }
    content = generate_weekly_report_content(
        "Dry Grapes", "Tasgaon/Sangli Belt",
        signal, {}, {"current": 175}, {"summary_7day": ""},
    )
    assert "UNUSUAL MARKET CONDITIONS" in content
    assert "Monitor daily" in content
    assert "+22.4%" in content


def test_weekly_report_omits_disruption_note_normally():
    from pipelines.report_generator import generate_weekly_report_content
    signal = {
        "signal": "BUY", "rationale": "Standard signal.",
        "entry_range": [100, 110], "target": 120, "stop": 95, "horizon_days": 5,
        "unusual_market_conditions": False,
    }
    content = generate_weekly_report_content(
        "Dry Grapes", "Tasgaon/Sangli Belt",
        signal, {}, {"current": 105}, {"summary_7day": ""},
    )
    assert "UNUSUAL MARKET CONDITIONS" not in content
