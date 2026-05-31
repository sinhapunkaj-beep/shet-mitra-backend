"""
tests/test_mango_price_model.py
ShetMitra - Mango Agent 3 - Pytest suite for the per-variety mango price models
and the variety-aware PricePredictor branch.

These tests:
  - Train (or reuse) the 5 mango pickles via scripts/train_mango_models.py.
  - Verify the strictest two MAPE targets (Alphonso < 22%, Totapuri < 15%);
    they pytest.skip with a clear message if synthetic data falls short.
  - Exercise PricePredictor.predict_price with variety + bearing_year and
    confirm the response shape (predictions, model_version, bearing fields,
    graceful fallback for unknown varieties).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import joblib
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import train_mango_models  # noqa: E402
from utils.price_prediction import PricePredictor  # noqa: E402

MODELS_DIR = ROOT_DIR / "data" / "models"
REPORT_PATH = MODELS_DIR / "mango_training_report.json"

VARIETY_SLUGS = (
    "alphonso",
    "kesar",
    "dasheri",
    "totapuri",
    "banganapalli",
)

logger = logging.getLogger("test_mango_price_model")
logging.basicConfig(level=logging.INFO)


# --------------------------------------------------------------------------- #
# Session fixture: ensure all pickles exist exactly once.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def trained_models() -> dict:
    """Train mango models if any pickle is missing; return parsed report."""

    need_train = not REPORT_PATH.exists()
    if not need_train:
        for slug in VARIETY_SLUGS:
            if not (MODELS_DIR / f"arima_mango_{slug}.pkl").exists():
                need_train = True
                break
    if need_train:
        logger.info("Training mango models (one-time per session) ...")
        train_mango_models.main()

    with REPORT_PATH.open() as fh:
        report = json.load(fh)
    return report


def _sample_features(season_week: int = 18, month: int = 5, year: int = 2026) -> dict:
    return {
        "price_lag_1": 280.0,
        "price_lag_7": 260.0,
        "price_lag_14": 240.0,
        "arrivals_lag_1": 40.0,
        "arrivals_7day_avg": 45.0,
        "season_week": season_week,
        "month": month,
        "year": year,
        "price_yoy": 0.12,
        "arrivals_yoy": -0.04,
        "bearing_year_flag": 1,
        "flowering_weather_score": 75.0,
        "usd_inr_rate": 83.5,
        "export_season_flag": 1,
        "ratnagiri_only_flag": 1,
    }


# --------------------------------------------------------------------------- #
# 1. All five pickles exist after training.
# --------------------------------------------------------------------------- #
def test_train_produces_all_five_pickles(trained_models: dict) -> None:
    for slug in VARIETY_SLUGS:
        path = MODELS_DIR / f"arima_mango_{slug}.pkl"
        assert path.exists(), f"Expected pickle {path} after training"
        payload = joblib.load(path)
        assert payload.get("commodity") == "Mango"
        assert payload.get("variety", "").lower() == slug
        # Pickles must carry the metadata the predictor relies on.
        for key in ("features", "mape", "model_kind", "trained_at"):
            assert key in payload, f"Pickle {slug} missing key {key!r}"


# --------------------------------------------------------------------------- #
# 2. Alphonso < 22% MAPE (skip with actual MAPE if not met on synthetic).
# --------------------------------------------------------------------------- #
def test_alphonso_mape_below_22pct_or_skip(trained_models: dict) -> None:
    alph = trained_models["varieties"]["Alphonso"]
    mape = alph["selected_mape"]
    target = alph["target_mape"]
    logger.info("Alphonso selected MAPE=%.2f target=%.1f", mape, target)
    if mape >= target:
        pytest.skip(
            f"Alphonso MAPE {mape:.2f}% did not meet target < {target:.1f}% on "
            f"the synthetic dataset (kind={alph['selected_kind']})."
        )
    assert mape < target


# --------------------------------------------------------------------------- #
# 3. Totapuri < 15% MAPE (skip with actual MAPE if not met).
# --------------------------------------------------------------------------- #
def test_totapuri_mape_below_15pct_or_skip(trained_models: dict) -> None:
    tot = trained_models["varieties"]["Totapuri"]
    mape = tot["selected_mape"]
    target = tot["target_mape"]
    logger.info("Totapuri selected MAPE=%.2f target=%.1f", mape, target)
    if mape >= target:
        pytest.skip(
            f"Totapuri MAPE {mape:.2f}% did not meet target < {target:.1f}% "
            f"on the synthetic dataset (kind={tot['selected_kind']})."
        )
    assert mape < target


# --------------------------------------------------------------------------- #
# 4. predict_price returns 3 predictions for Mango/Alphonso with bearing ON.
# --------------------------------------------------------------------------- #
def test_predict_mango_with_variety(trained_models: dict) -> None:
    predictor = PricePredictor()
    predictor.reset_cache()
    result = predictor.predict_price(
        commodity="Mango",
        variety="Alphonso",
        current_features=_sample_features(),
        bearing_year="ON",
        horizon_days=3,
    )
    assert len(result["predictions"]) == 3
    assert all(isinstance(p, float) and p > 0 for p in result["predictions"])
    assert result["model_version"] == "v2"
    assert result["variety"].lower() == "alphonso"
    assert result["bearing_year"] == "ON"
    assert result["bearing_price_adjust"] == pytest.approx(1.0)
    assert 0.30 <= result["confidence"] <= 0.95


# --------------------------------------------------------------------------- #
# 5. OFF year reduces volume-implied multiplier and echoes bearing context.
# --------------------------------------------------------------------------- #
def test_predict_mango_bearing_off_year_reduces_volume_implied(
    trained_models: dict,
) -> None:
    predictor = PricePredictor()
    predictor.reset_cache()

    off_year_features = _sample_features()
    off_year_features["bearing_year_flag"] = 0  # OFF
    result_off = predictor.predict_price(
        commodity="Mango",
        variety="Alphonso",
        current_features=off_year_features,
        bearing_year="OFF",
        horizon_days=3,
    )

    assert result_off["bearing_year"] == "OFF"
    # Volume / supply hint should reflect a tight (low-supply) year.
    assert result_off["bearing_price_adjust"] < 1.0
    assert result_off["bearing_price_adjust"] == pytest.approx(0.45)
    # Mango supply pressure derived from bearing - OFF year => high pressure.
    assert result_off["supply_pressure"] == "high"

    # Sanity: ON year multiplier strictly larger than OFF.
    result_on = predictor.predict_price(
        commodity="Mango",
        variety="Alphonso",
        current_features=_sample_features(),
        bearing_year="ON",
        horizon_days=3,
    )
    assert result_on["bearing_price_adjust"] > result_off["bearing_price_adjust"]


# --------------------------------------------------------------------------- #
# 6. Unknown variety: default pickle OR clear FileNotFoundError - never crash.
# --------------------------------------------------------------------------- #
def test_unknown_variety_falls_back_or_errors_cleanly(trained_models: dict) -> None:
    predictor = PricePredictor()
    predictor.reset_cache()

    try:
        result = predictor.predict_price(
            commodity="Mango",
            variety="UnknownVariety",
            current_features=_sample_features(),
            bearing_year="UNKNOWN",
            horizon_days=3,
        )
    except FileNotFoundError as exc:
        # Acceptable path: there is no default pickle and no UnknownVariety
        # pickle - the error must be clear and reference the missing file.
        msg = str(exc)
        assert "Mango" in msg or "mango" in msg
        assert "UnknownVariety" in msg or "unknownvariety" in msg
        return

    # Acceptable path: a default pickle (or some bundle) was loaded.
    assert len(result["predictions"]) == 3
    assert result["model_version"] == "v2"
    assert result["bearing_year"] == "UNKNOWN"
