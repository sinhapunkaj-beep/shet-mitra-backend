"""Unit tests for Agent 4 mango phenology deliverables.

Covers:
  * Pure-logic helpers in ``pipelines.mango_phenology``.
  * The mango branch wired into ``pipelines.ndvi_pipeline.AmedSentinelPipeline``.

All tests are stdlib + pytest + unittest.mock — no third-party fixtures.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Make the project root importable when pytest is launched from a parent dir.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines import mango_phenology  # noqa: E402
from pipelines.ndvi_pipeline import AmedSentinelPipeline  # noqa: E402


# ---------------------------------------------------------------------------
# detect_bearing_year
# ---------------------------------------------------------------------------

def test_bearing_detection_off_year():
    bearing, conf = mango_phenology.detect_bearing_year(0.80)
    assert bearing == "OFF"
    assert conf >= 0.85


def test_bearing_detection_on_year():
    bearing, conf = mango_phenology.detect_bearing_year(0.55)
    assert bearing == "ON"
    assert conf >= 0.85


def test_bearing_detection_neutral():
    bearing, conf = mango_phenology.detect_bearing_year(0.68)
    assert bearing == "UNKNOWN"
    assert 0.0 < conf <= 0.6


# ---------------------------------------------------------------------------
# detect_flowering
# ---------------------------------------------------------------------------

def test_flowering_detect_in_january():
    # Series: Dec NDVI 0.70 -> Jan NDVI 0.55 (drop of 0.15, mid-range).
    series = [
        (date(2025, 12, 15), 0.70),
        (date(2026, 1, 15), 0.55),
    ]
    flowered, peak = mango_phenology.detect_flowering(series, current_month=1)
    assert flowered is True
    assert peak == date(2026, 1, 15)


def test_flowering_not_detected_in_april():
    # Same drop magnitude but current_month=4 → outside flowering window.
    series = [
        (date(2026, 3, 15), 0.70),
        (date(2026, 4, 15), 0.55),
    ]
    flowered, peak = mango_phenology.detect_flowering(series, current_month=4)
    assert flowered is False
    assert peak is None


# ---------------------------------------------------------------------------
# detect_fruit_set
# ---------------------------------------------------------------------------

def test_fruit_set_detection():
    # NDVI recovers from 0.55 -> 0.62 between Feb and Mar with rising RECI.
    series = [
        (date(2026, 2, 15), 0.55),
        (date(2026, 3, 15), 0.62),
    ]
    detected, fset_date = mango_phenology.detect_fruit_set(series, reci_trend=0.12)
    assert detected is True
    assert fset_date == date(2026, 3, 15)


# ---------------------------------------------------------------------------
# detect_heat_stress
# ---------------------------------------------------------------------------

def test_heat_stress_counter():
    temps = [
        (date(2026, 3, 1), 38.5),
        (date(2026, 3, 2), 39.1),
        (date(2026, 3, 3), 40.2),
        (date(2026, 3, 4), 38.8),
        (date(2026, 3, 5), 41.0),
        # Out-of-window event — must be ignored.
        (date(2026, 5, 1), 42.0),
        # Below threshold — must be ignored.
        (date(2026, 3, 6), 37.5),
    ]
    assert mango_phenology.detect_heat_stress(temps) == 5


# ---------------------------------------------------------------------------
# assess_water_status
# ---------------------------------------------------------------------------

def test_water_status_drought():
    assert mango_phenology.assess_water_status(-0.30) == "drought"


def test_water_status_waterlog():
    assert mango_phenology.assess_water_status(0.45) == "waterlogged"


# ---------------------------------------------------------------------------
# bearing_year_yield_multiplier
# ---------------------------------------------------------------------------

def test_bearing_yield_multiplier():
    assert mango_phenology.bearing_year_yield_multiplier("ON") == 1.0
    assert mango_phenology.bearing_year_yield_multiplier("OFF") == 0.45
    assert mango_phenology.bearing_year_yield_multiplier("UNKNOWN") == 0.75


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

def test_pipeline_adds_mango_phenology_to_result():
    """A Mango plot run through ``AmedSentinelPipeline`` exposes
    ``mango_phenology`` in the result dict."""
    mock_client = MagicMock()
    mock_client.get_field_data = MagicMock(
        return_value={
            "field_id": "amed_mango_1",
            "crop_type": "Mango",
            "crop_variety_hint": "Alphonso",
            "crop_confidence": 0.92,
            "field_size_acres": 3.0,
            "sowing_date": "2025-08-01",
            "harvest_date_predicted": "2026-04-22",
            "growth_stage": "flowering",
            "growth_stage_confidence": 0.88,
            "irrigation_detected": True,
            "last_event": "irrigation",
            "last_event_date": "2026-04-01",
            "source": "AMED",
        }
    )
    mock_client.get_belt_data = MagicMock(
        return_value={
            "region": "Konkan",
            "crop_type": "Mango",
            "total_fields_detected": 1240,
            "total_area_acres": 4820,
            "harvest_forecast": [],
            "source": "AMED",
        }
    )

    # Use a non-existent DB path so persistence is a no-op (still safe).
    pipeline = AmedSentinelPipeline(
        amed_client=mock_client,
        db_path="/tmp/__nonexistent_shetmitra_test.db",
    )

    plot = {
        "id": "plot_mango_001",
        "current_crop": "Mango",
        "area_acres": 3.0,
        "aug_sep_ndvi_mean": 0.78,  # OFF bearing year
        "monthly_ndvi": [
            (date(2025, 12, 15), 0.70),
            (date(2026, 1, 15), 0.55),
            (date(2026, 2, 15), 0.58),
            (date(2026, 3, 15), 0.64),
        ],
        "weather_temps": [
            (date(2026, 3, 1), 39.0),
            (date(2026, 3, 2), 38.5),
            (date(2026, 3, 3), 40.0),
        ],
    }

    result = asyncio.run(pipeline.run_full_pipeline("farmer_001", plot))

    assert "mango_phenology" in result
    mango = result["mango_phenology"]
    assert mango is not None
    assert mango["bearing_year"] == "OFF"
    assert mango["bearing_confidence"] >= 0.85
    assert mango["heat_stress_events_count"] == 3
    assert mango["water_status"] in {"drought", "normal", "waterlogged"}
