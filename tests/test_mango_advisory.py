"""Tests for the mango-specific advisory engine.

Covers variety-config completeness, the 4 advisory-type triggers,
bearing-year yield adjustment, Alphonso export premium, Konkan winter
frost alerts, heat-stress alerts, prompt structure, language hint, and
the crop-aware dispatcher.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pytest

from pipelines import advisory_engine
from pipelines.advisory_engine import (
    generate_advisory_for_plot,
    generate_mango_advisory,
    get_variety_config,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _reset_advisory_cache():
    advisory_engine._clear_config_cache()
    yield
    advisory_engine._clear_config_cache()


def _make_plot(
    variety: str = "Alphonso",
    bearing_year: str = "ON",
    region: str = "Ratnagiri",
    tree_count: int = 100,
    crop: str = "Mango",
):
    return {
        "id": "plot-mango-1",
        "current_crop": crop,
        "current_crop_variety": variety,
        "variety_source": "farmer_reported",
        "bearing_year": bearing_year,
        "bearing_confidence": 0.82,
        "crop_region": region,
        "tree_count": tree_count,
        "tree_age_years": 12,
        "area_acres": 2.0,
        "self_reported_acres": 2.0,
    }


def _make_amed(harvest_date: str = "2026-04-25"):
    return {
        "crop_type": "Mango",
        "crop_confidence": 0.91,
        "field_size_acres": 2.0,
        "sowing_date": "2014-06-01",
        "harvest_date_predicted": harvest_date,
        "growth_stage": "fruit_development",
        "tree_count": 100,
        "tree_age_years": 12,
    }


def _make_sentinel(ndvi: float = 0.70, ndvi_prev: float = 0.72):
    return {
        "ndvi": ndvi,
        "ndvi_previous_month": ndvi_prev,
        "reci": 1.6,
        "ndwi": 0.18,
        "lai": 3.4,
        "prithvi_phenology": "fruit_dev",
        "prithvi_stress": "low",
    }


def _make_market(current_price: float = 200.0):
    return {
        "current_price": current_price,
        "predictions": [205.0, 208.0, 207.0],
        "confidence": 0.74,
        "supply_pressure": "medium",
        "predicted_yield_kg_per_tree": 80.0,
    }


def _make_weather(min_temp: float = 22.0, in_jan: bool = False):
    base_date = "2026-01-10" if in_jan else "2026-04-30"
    return {
        "min_temp": min_temp,
        "forecast_7day": [
            {"date": base_date, "temp_max": 32.0, "temp_min": min_temp, "rain_mm": 0.0},
        ],
    }


def _make_phenology(**overrides):
    base = {
        "flowering_detected": False,
        "fruit_set_detected": False,
        "heat_stress_events_count": 0,
        "frost_events_count": 0,
        "predicted_yield_kg_per_tree": 80.0,
    }
    base.update(overrides)
    return base


def _run(plot, *, sentinel=None, weather=None, phenology=None, market=None,
         amed=None, reference_date=None, llm_client=None):
    variety = plot.get("current_crop_variety")
    vc = get_variety_config("Mango", variety)
    return generate_mango_advisory(
        plot=plot,
        amed=amed or _make_amed(),
        sentinel=sentinel or _make_sentinel(),
        weather=weather or _make_weather(),
        phenology=phenology or _make_phenology(),
        variety_config=vc,
        price_pred={},
        market=market or _make_market(),
        llm_client=llm_client,
        reference_date=reference_date,
    )


# --------------------------------------------------------------------------- #
# 1. Variety config
# --------------------------------------------------------------------------- #

def test_variety_config_has_full_mango_block():
    alphonso = get_variety_config("Mango", "Alphonso")
    assert alphonso["language_hint"] == "Konkani"
    assert alphonso["gi_tagged"] is True
    assert alphonso["price_premium_pct"] == 60
    assert alphonso["harvest_months"] == [4, 5]
    assert alphonso["maturity_days_from_fruit_set"] == 110
    assert alphonso["weight_target_grams"] == 250
    assert alphonso["export_quality"] is True

    kesar = get_variety_config("Mango", "Kesar")
    assert kesar["harvest_months"] == [5, 6]
    assert kesar["mandi_primary"] == "Junagadh APMC"
    assert kesar["price_premium_pct"] == 40

    for v in ("Dasheri", "Totapuri", "Banganapalli", "default"):
        cfg = get_variety_config("Mango", v)
        for key in (
            "harvest_months", "maturity_days_from_fruit_set",
            "brix_target_min", "brix_target_max",
            "weight_target_grams", "export_quality", "gi_tagged",
            "mandi_primary", "mandi_secondary",
            "price_premium_pct", "spray_schedule",
            "alternate_bearing", "language_hint",
        ):
            assert key in cfg, f"{v} missing {key}"


# --------------------------------------------------------------------------- #
# 2. Advisory-type detection
# --------------------------------------------------------------------------- #

def test_advisory_type_flowering():
    plot = _make_plot()
    sentinel = _make_sentinel(ndvi=0.55, ndvi_prev=0.70)  # drop 0.15
    result = _run(
        plot,
        sentinel=sentinel,
        reference_date=_dt.date(2026, 1, 15),
    )
    assert result["advisory_type"] == "FLOWERING"


def test_advisory_type_fruit_set():
    plot = _make_plot()
    phen = _make_phenology(fruit_set_detected=True, fruit_set_date="2026-02-10")
    result = _run(
        plot,
        phenology=phen,
        reference_date=_dt.date(2026, 3, 1),
    )
    assert result["advisory_type"] == "FRUIT_SET"
    assert result["fruit_set_detected"] is True


def test_advisory_type_harvest_readiness():
    plot = _make_plot()
    phen = _make_phenology(days_to_harvest=14)
    result = _run(
        plot,
        phenology=phen,
        reference_date=_dt.date(2026, 4, 10),
    )
    assert result["advisory_type"] == "HARVEST_READINESS"


def test_advisory_type_fruit_development_default():
    plot = _make_plot()
    # Neutral: no flowering trigger (month=March), no fruit set, no near harvest
    result = _run(
        plot,
        sentinel=_make_sentinel(ndvi=0.72, ndvi_prev=0.71),
        phenology=_make_phenology(),
        reference_date=_dt.date(2026, 3, 5),
    )
    assert result["advisory_type"] == "FRUIT_DEVELOPMENT"


# --------------------------------------------------------------------------- #
# 3. Bearing-year yield adjustment
# --------------------------------------------------------------------------- #

def test_bearing_off_year_reduces_yield_to_45pct():
    plot = _make_plot(bearing_year="OFF")
    market = _make_market()
    market["predicted_yield_kg_per_tree"] = 100.0
    result = _run(plot, market=market, reference_date=_dt.date(2026, 3, 5))
    # 100 * 0.45 = 45
    assert result["predicted_yield_kg_per_tree"] == pytest.approx(45.0, rel=1e-3)
    assert result["bearing_year"] == "OFF"


def test_bearing_unknown_year_reduces_yield_to_75pct():
    plot = _make_plot(bearing_year="UNKNOWN")
    market = _make_market()
    market["predicted_yield_kg_per_tree"] = 100.0
    result = _run(plot, market=market, reference_date=_dt.date(2026, 3, 5))
    assert result["predicted_yield_kg_per_tree"] == pytest.approx(75.0, rel=1e-3)


# --------------------------------------------------------------------------- #
# 4. Export premium (Alphonso)
# --------------------------------------------------------------------------- #

def test_export_season_premium_alphonso():
    plot = _make_plot(variety="Alphonso")
    amed = _make_amed(harvest_date="2026-04-25")
    result = _run(
        plot,
        amed=amed,
        reference_date=_dt.date(2026, 4, 10),
    )
    # Alphonso premium 60% + export 30% = 90%
    assert result["export_premium_applied"] is True
    assert result["premium_pct"] == pytest.approx(90.0, rel=1e-3)


def test_export_premium_skipped_for_kesar():
    plot = _make_plot(variety="Kesar")
    amed = _make_amed(harvest_date="2026-04-25")
    result = _run(
        plot,
        amed=amed,
        reference_date=_dt.date(2026, 4, 10),
    )
    assert result["export_premium_applied"] is False


# --------------------------------------------------------------------------- #
# 5. Frost & heat-stress alerts
# --------------------------------------------------------------------------- #

def test_frost_risk_alert_konkan_winter():
    plot = _make_plot()
    weather = {
        "min_temp": 5.0,
        "forecast_7day": [
            {"date": "2026-01-12", "temp_max": 22.0, "temp_min": 5.0, "rain_mm": 0.0},
        ],
    }
    result = _run(
        plot,
        weather=weather,
        reference_date=_dt.date(2026, 1, 12),
    )
    codes = [a["code"] for a in result["alerts"]]
    assert "FROST_RISK" in codes


def test_heat_stress_alert_when_events_present():
    plot = _make_plot()
    phen = _make_phenology(heat_stress_events_count=3)
    result = _run(
        plot,
        phenology=phen,
        reference_date=_dt.date(2026, 4, 10),
    )
    codes = [a["code"] for a in result["alerts"]]
    assert "HEAT_STRESS_RISK" in codes


# --------------------------------------------------------------------------- #
# 6. Claude prompt structure + language hint
# --------------------------------------------------------------------------- #

def test_claude_prompt_includes_variety_block():
    plot = _make_plot(variety="Alphonso")
    result = _run(plot, reference_date=_dt.date(2026, 3, 5))
    prompt = result["prompt"]
    assert "FARM DATA (AMED confirmed)" in prompt
    assert "Alphonso" in prompt
    assert "PHENOLOGY THIS SEASON" in prompt


def test_konkani_language_for_alphonso():
    plot = _make_plot(variety="Alphonso")
    result = _run(plot, reference_date=_dt.date(2026, 3, 5))
    assert "Konkani" in result["prompt"]


# --------------------------------------------------------------------------- #
# 7. Dispatcher
# --------------------------------------------------------------------------- #

def test_advisory_for_plot_dispatches_mango():
    plot = _make_plot(variety="Alphonso", crop="Mango")
    market_data = _make_market()
    market_data["harvest_window"] = {
        "start": "2026-04-25",
        "end": "2026-05-10",
        "confidence": 0.7,
    }
    result = generate_advisory_for_plot(
        plot=plot,
        amed=_make_amed(),
        sentinel=_make_sentinel(),
        weather=_make_weather(),
        phenology=_make_phenology(),
        market_data=market_data,
        reference_date=_dt.date(2026, 3, 5),
    )
    # Mango branch keys
    assert "advisory_type" in result
    assert "bearing_year" in result
    assert "predicted_yield_kg_per_tree" in result
    assert result["variety"] == "Alphonso"


def test_advisory_for_plot_falls_back_to_default():
    plot = {
        "id": "plot-x",
        "current_crop": "Wheat",
        "current_crop_variety": "HD-2967",
        "self_reported_acres": 1.0,
        "area_acres": 1.0,
    }
    market = _make_market()
    result = generate_advisory_for_plot(
        plot=plot,
        amed={"crop_type": "Wheat"},
        sentinel=_make_sentinel(),
        weather=_make_weather(),
        phenology={},
        market_data=market,
    )
    # Generic generate_advisory returns these keys.
    assert "brix_estimate" in result
    assert "revenue_potential" in result
    assert "advisory_type" not in result
