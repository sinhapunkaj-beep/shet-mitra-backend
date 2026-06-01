"""tests/test_advisory_jh_routing.py — SDD §6 + §8 (JH region routing).

Verifies that the advisory engine routes Jharkhand mango plots to the
variety-specific config (Mallika / Amrapali / Jardalu / Himsagar /
Langra_JH), forces the language_hint to Hindi, and uses the JH-specific
spray schedules already present in data/variety_config.json.

All side effects (LLM calls, DB) are mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines import advisory_engine, i18n  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _base_plot(variety: str, region_code: str | None = "JH") -> dict:
    return {
        "id": "plot-jh-1",
        "farmer_id": "farmer-jh-1",
        "current_crop": "Mango",
        "current_crop_variety": variety,
        "crop_region": "Jharkhand",
        "region": "JH",
        "region_code": region_code,
        "tree_count": 100,
        "tree_age_years": 10,
        "bearing_year": "ON",
        "self_reported_acres": 5.0,
    }


def _base_amed() -> dict:
    return {
        "crop_type": "Mango",
        "crop_confidence": 0.93,
        "field_size_acres": 5.0,
        "harvest_date_predicted": "2026-05-20",
    }


def _base_market() -> dict:
    return {
        "current_price": 50.0,
        "predictions": [52.0, 54.0, 55.0],
        "confidence": 0.8,
        "predicted_yield_kg_per_tree": 30.0,
    }


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_jh_mallika_routes_hindi_and_jh_spray_schedule():
    plot = _base_plot("Mallika")
    out = advisory_engine.generate_advisory_for_plot(
        plot=plot,
        amed=_base_amed(),
        sentinel={"ndvi": 0.65, "reci": 1.2},
        weather={},
        phenology={},
        market_data=_base_market(),
    )

    # Hindi language wired in.
    assert out["language"] == "Hindi"
    assert out["region_code"] == "JH"
    # JH-specific spray schedule from data/variety_config.json.
    assert out["spray_schedule"] == "mango_mallika_jharkhand"
    # The Claude prompt must say "Respond in Hindi" (variety_config.language_hint).
    assert "Hindi" in out["prompt"]


def test_jh_jardalu_routes_with_gi_spray_schedule():
    """Jardalu is the GI variety — it has its own spray schedule."""
    plot = _base_plot("Jardalu")
    out = advisory_engine.generate_advisory_for_plot(
        plot=plot,
        amed=_base_amed(),
        sentinel={"ndvi": 0.7, "reci": 1.3},
        weather={},
        phenology={},
        market_data=_base_market(),
    )
    assert out["language"] == "Hindi"
    assert out["spray_schedule"] == "mango_jardalu_gi"
    assert out["variety"] == "Jardalu"


def test_jh_amrapali_routes_correctly():
    plot = _base_plot("Amrapali")
    out = advisory_engine.generate_advisory_for_plot(
        plot=plot,
        amed=_base_amed(),
        sentinel={"ndvi": 0.6, "reci": 1.1},
        weather={},
        phenology={},
        market_data=_base_market(),
    )
    assert out["language"] == "Hindi"
    assert out["spray_schedule"] == "mango_amrapali_jharkhand"


def test_jh_variety_inferred_when_region_code_missing_on_plot():
    """Even if the plot did not carry region_code, a JH variety like
    Himsagar must still route to the Hindi/JH branch (via variety_config)."""
    plot = _base_plot("Himsagar", region_code=None)
    plot.pop("region", None)
    out = advisory_engine.generate_advisory_for_plot(
        plot=plot,
        amed={"crop_type": "Mango"},
        sentinel={},
        weather={},
        phenology={},
        market_data=_base_market(),
    )
    assert out["region_code"] == "JH"
    assert out["language"] == "Hindi"
    assert out["spray_schedule"] == "mango_himsagar_bengal"


def test_maharashtra_alphonso_unchanged_no_regression():
    """SDD §6: existing Maharashtra path must not regress — Alphonso keeps
    its Konkani language hint and standard_mango_alphonso spray schedule.
    """
    plot = {
        "id": "plot-mh-1",
        "farmer_id": "farmer-mh-1",
        "current_crop": "Mango",
        "current_crop_variety": "Alphonso",
        "crop_region": "Ratnagiri",
        "region_code": "MH",
        "tree_count": 50,
        "bearing_year": "ON",
    }
    out = advisory_engine.generate_advisory_for_plot(
        plot=plot,
        amed={"crop_type": "Mango", "field_size_acres": 3.0,
              "harvest_date_predicted": "2026-04-30"},
        sentinel={"ndvi": 0.7},
        weather={},
        phenology={},
        market_data={"current_price": 100.0},
    )
    # MH branch never adds the region_code/language keys.
    assert out.get("region_code") != "JH"
    assert out.get("language") != "Hindi"
    assert out["spray_schedule"] == "standard_mango_alphonso"


def test_jh_langra_variety_uses_jh_spray():
    plot = _base_plot("Langra_JH")
    out = advisory_engine.generate_advisory_for_plot(
        plot=plot,
        amed=_base_amed(),
        sentinel={"ndvi": 0.55, "reci": 0.9},
        weather={},
        phenology={},
        market_data=_base_market(),
    )
    assert out["language"] == "Hindi"
    assert out["spray_schedule"] == "mango_langra_jharkhand"


def test_jh_language_resolution_uses_i18n_language_for_region():
    """The Hindi label comes from pipelines.i18n.language_for_region('JH')."""
    assert i18n.language_for_region("JH") == "Hindi"
    plot = _base_plot("Mallika")
    out = advisory_engine.generate_advisory_for_plot(
        plot=plot, amed=_base_amed(), sentinel={}, weather={},
        phenology={}, market_data=_base_market(),
    )
    assert out["language"] == i18n.language_for_region("JH")
