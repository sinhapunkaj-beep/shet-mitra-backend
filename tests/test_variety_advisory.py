"""Tests for the variety-aware advisory engine.

Covers configuration loading, variety lookup fallbacks, revenue
calculations, Claude prompt assembly, the end-to-end orchestrator, and
runtime config reloads when the JSON file's mtime changes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pipelines import advisory_engine
from pipelines.advisory_engine import (
    GLOBAL_FALLBACK_VARIETY_CONFIG,
    build_claude_prompt,
    calculate_revenue_potential,
    estimate_brix_from_reci,
    generate_advisory,
    get_variety_config,
    load_variety_config,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_plot(crop="Grapes", variety="Thompson Seedless"):
    return {
        "id": "plot-1",
        "current_crop": crop,
        "current_crop_variety": variety,
        "variety_source": "farmer_reported",
        "self_reported_acres": 2.0,
        "area_acres": 2.0,
    }


def _make_amed():
    return {
        "crop_type": "Grapes",
        "crop_confidence": 0.92,
        "field_size_acres": 2.1,
        "sowing_date": "2025-12-01",
        "harvest_date_predicted": "2026-04-15",
        "growth_stage": "veraison",
        "last_event_date": "2026-04-25",
        "avg_harvest_date_3yr": "2026-04-12",
    }


def _make_sentinel():
    return {
        "ndvi": 0.72,
        "reci": 1.8,
        "ndwi": 0.18,
        "lai": 3.1,
        "npp": 1180.0,
        "npp_anomaly": 4.5,
    }


def _make_market():
    return {
        "current_price": 100.0,
        "predictions": [102.0, 104.0, 103.5],
        "confidence": 0.78,
        "supply_pressure": "medium",
        "predicted_yield_kg_per_acre": 1000.0,
        "belt": {
            "belt_volume_mt": 680.0,
            "belt_volume_next_mt": 540.0,
            "health_pct_good": 63,
            "fields_harvesting": 142,
        },
    }


def _make_weather():
    return {
        "forecast_7day": [
            {"date": "2026-04-30", "temp_max": 35.2, "temp_min": 22.1, "rain_mm": 0.0},
            {"date": "2026-05-01", "temp_max": 36.0, "temp_min": 22.5, "rain_mm": 1.2},
        ]
    }


@pytest.fixture(autouse=True)
def _reset_advisory_cache():
    """Drop the mtime cache so each test sees a clean slate."""
    advisory_engine._clear_config_cache()
    yield
    advisory_engine._clear_config_cache()


# --------------------------------------------------------------------------- #
# Config-loading tests
# --------------------------------------------------------------------------- #

def test_variety_config_loads():
    config = load_variety_config()
    assert "Grapes" in config
    assert "Pomegranate" in config
    assert "Mango" in config
    assert config["Grapes"]["Thompson Seedless"]["brix_target_min"] == 18
    assert config["Pomegranate"]["Bhagwa"]["price_premium_pct"] == 20
    assert config["Mango"]["Alphonso"]["price_premium_pct"] == 60


def test_get_variety_config_known_variety():
    sharad = get_variety_config("Grapes", "Sharad Seedless")
    assert sharad["brix_target_min"] == 20
    assert sharad["brix_target_max"] == 24
    assert sharad["maturity_days"] == 145
    assert sharad["spray_schedule"] == "standard_grape_sharad"
    # And definitely not the default block.
    default = load_variety_config()["Grapes"]["default"]
    assert sharad != default


def test_get_variety_config_falls_back_to_default():
    fallback = get_variety_config("Grapes", "Unknown Variety")
    default = load_variety_config()["Grapes"]["default"]
    assert fallback == default

    none_variety = get_variety_config("Grapes", None)
    assert none_variety == default


def test_get_variety_config_global_fallback():
    out = get_variety_config("Wheat", "Anything")
    assert out["mandi_grade_premium"] is False
    assert out["price_premium_pct"] == 0
    assert out == GLOBAL_FALLBACK_VARIETY_CONFIG


# --------------------------------------------------------------------------- #
# Brix estimation
# --------------------------------------------------------------------------- #

def test_estimate_brix_from_reci_linear_and_clamped():
    # Linear midpoint: reci=0.5 -> 8.0
    assert estimate_brix_from_reci(0.5) == pytest.approx(8.0)
    # Very low reci is clamped to 4.
    assert estimate_brix_from_reci(-5) == pytest.approx(4.0)
    # Very high reci is clamped to 30.
    assert estimate_brix_from_reci(10) == pytest.approx(30.0)
    # None falls back to reci=1.0 path -> 8 + 0.5*15 = 15.5.
    assert estimate_brix_from_reci(None) == pytest.approx(15.5)


# --------------------------------------------------------------------------- #
# Revenue tests
# --------------------------------------------------------------------------- #

def test_revenue_calculation_with_premium():
    alphonso = get_variety_config("Mango", "Alphonso")
    result = calculate_revenue_potential(
        predicted_yield_kg_per_acre=1000.0,
        area_acres=2.0,
        base_modal_price=100.0,
        variety_config=alphonso,
    )
    assert result["is_premium_variety"] is True
    assert result["base_price_kg"] == pytest.approx(100.0)
    assert result["expected_price_kg"] == pytest.approx(160.0)
    assert result["premium_pct"] == pytest.approx(60.0)
    # 1000 * 100 * 1.60 * 2 = 320_000
    assert result["revenue_potential_inr"] == pytest.approx(320_000.0)


def test_revenue_calculation_no_premium():
    grapes_default = get_variety_config("Grapes", None)  # Grapes default
    assert grapes_default["mandi_grade_premium"] is False
    result = calculate_revenue_potential(
        predicted_yield_kg_per_acre=1500.0,
        area_acres=3.0,
        base_modal_price=80.0,
        variety_config=grapes_default,
    )
    assert result["is_premium_variety"] is False
    assert result["base_price_kg"] == pytest.approx(80.0)
    assert result["expected_price_kg"] == pytest.approx(80.0)
    # yield * base * area = 1500 * 80 * 3 = 360_000
    assert result["revenue_potential_inr"] == pytest.approx(360_000.0)


# --------------------------------------------------------------------------- #
# Prompt tests
# --------------------------------------------------------------------------- #

def test_claude_prompt_includes_variety_block():
    plot = _make_plot("Grapes", "Thompson Seedless")
    variety_config = get_variety_config("Grapes", "Thompson Seedless")
    prompt = build_claude_prompt(
        plot=plot,
        amed=_make_amed(),
        sentinel=_make_sentinel(),
        harvest_window={"start": "2026-04-15", "end": "2026-04-22", "confidence": 0.85},
        market=_make_market(),
        weather=_make_weather(),
        variety_config=variety_config,
    )
    assert "VARIETY DETAILS:" in prompt
    assert "Thompson Seedless" in prompt
    assert "Provide variety-specific advice for" in prompt
    # The trailer should be the very last line.
    assert prompt.rstrip().endswith("Provide variety-specific advice for Thompson Seedless.")


# --------------------------------------------------------------------------- #
# Orchestrator tests
# --------------------------------------------------------------------------- #

REQUIRED_ADVISORY_KEYS = {
    "variety",
    "variety_source",
    "variety_config",
    "brix_estimate",
    "brix_target_min",
    "brix_target_max",
    "maturity_days",
    "spray_schedule",
    "revenue_potential",
    "harvest_window",
    "narrative",
    "prompt",
}


def test_generate_advisory_full_path():
    plot = _make_plot("Grapes", "Thompson Seedless")
    advisory = generate_advisory(
        plot=plot,
        amed=_make_amed(),
        sentinel=_make_sentinel(),
        harvest_window={"start": "2026-04-15", "end": "2026-04-22", "confidence": 0.85},
        market=_make_market(),
        weather=_make_weather(),
    )
    assert REQUIRED_ADVISORY_KEYS.issubset(advisory.keys())
    assert advisory["variety"] == "Thompson Seedless"
    assert advisory["variety_source"] == "farmer_reported"
    assert advisory["brix_target_min"] == 18
    assert advisory["brix_target_max"] == 22
    assert advisory["maturity_days"] == 135
    assert advisory["spray_schedule"] == "standard_grape_thompson"
    assert advisory["revenue_potential"]["is_premium_variety"] is True
    # 1000 * 100 * 1.15 * 2 = 230_000
    assert advisory["revenue_potential"]["revenue_potential_inr"] == pytest.approx(230_000.0)
    assert isinstance(advisory["narrative"], str) and advisory["narrative"].strip()
    assert "VARIETY DETAILS:" in advisory["prompt"]


class _MockLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: str | None = None

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


def test_generate_advisory_with_mock_llm():
    plot = _make_plot("Mango", "Alphonso")
    client = _MockLLMClient("MOCK ADVICE 42")
    advisory = generate_advisory(
        plot=plot,
        amed={"crop_type": "Mango", "crop_confidence": 0.9, "field_size_acres": 2.0},
        sentinel=_make_sentinel(),
        harvest_window={"start": "2026-05-01", "end": "2026-05-10", "confidence": 0.8},
        market=_make_market(),
        weather=_make_weather(),
        llm_client=client,
    )
    assert advisory["narrative"] == "MOCK ADVICE 42"
    assert client.last_prompt is not None
    assert "Alphonso" in client.last_prompt


# --------------------------------------------------------------------------- #
# mtime reload test
# --------------------------------------------------------------------------- #

def test_config_reloads_on_mtime_change(tmp_path: Path):
    config_path = tmp_path / "variety_config.json"
    original = {
        "Grapes": {
            "Thompson Seedless": {
                "brix_target_min": 18,
                "brix_target_max": 22,
                "maturity_days": 135,
                "spray_schedule": "standard_grape_thompson",
                "mandi_grade_premium": True,
                "price_premium_pct": 15,
            },
            "default": {
                "brix_target_min": 16,
                "brix_target_max": 20,
                "maturity_days": 135,
                "spray_schedule": "standard_grape_generic",
                "mandi_grade_premium": False,
                "price_premium_pct": 0,
            },
        }
    }
    config_path.write_text(json.dumps(original), encoding="utf-8")

    first = load_variety_config(config_path)
    assert first["Grapes"]["Thompson Seedless"]["brix_target_min"] == 18

    # Wait a tick so the mtime is guaranteed to advance even on coarse FS clocks.
    time.sleep(0.05)
    updated = json.loads(json.dumps(original))
    updated["Grapes"]["Thompson Seedless"]["brix_target_min"] = 21
    config_path.write_text(json.dumps(updated), encoding="utf-8")
    # Force a definitively newer mtime regardless of FS resolution.
    new_mtime = config_path.stat().st_mtime + 1.0
    import os
    os.utime(config_path, (new_mtime, new_mtime))

    second = load_variety_config(config_path)
    assert second["Grapes"]["Thompson Seedless"]["brix_target_min"] == 21
    # And the resolver picks up the new value.
    resolved = get_variety_config(
        "Grapes", "Thompson Seedless", config_path=config_path
    )
    assert resolved["brix_target_min"] == 21
