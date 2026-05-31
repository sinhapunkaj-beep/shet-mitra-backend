"""Tests for the AMED client facade and mock backend.

Covers the four scenarios called out in the Agent 1 brief:
    1. Field query returns a recognised crop and a valid harvest date.
    2. Belt query returns the canonical Tasgaon volume forecast.
    3. The mock/live switch behaves correctly, including the
       live-fail fallback to mock when AMED_USE_MOCK=false but the
       live client cannot answer.
    4. Field-level results are deterministic given a plot_id.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from geo.amed_client import AMEDClient

TASGAON_BBOX = {
    "north": 17.2,
    "south": 16.8,
    "east": 74.8,
    "west": 74.3,
}


@pytest.fixture(autouse=True)
def _force_mock_by_default(monkeypatch):
    """Default every test to AMED_USE_MOCK=true unless it overrides."""
    monkeypatch.setenv("AMED_USE_MOCK", "true")
    monkeypatch.setenv("AMED_API_KEY", "")
    yield


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def test_field_query_returns_crop_and_harvest():
    client = AMEDClient()
    result = client.get_field_data(plot_id="plot_001")

    assert result["crop_type"] in {"Grapes", "Pomegranate"}
    assert _is_iso_date(result["harvest_date_predicted"])
    assert _is_iso_date(result["sowing_date"])
    assert isinstance(result["history"], list)
    assert len(result["history"]) == 3
    # Confidence within documented band.
    assert 0.85 <= result["crop_confidence"] <= 0.95
    assert result["source"] == "AMED"


def test_belt_query_returns_volume_forecast():
    client = AMEDClient()
    result = client.get_belt_data(bbox=TASGAON_BBOX, crop_type="Grapes")

    assert result["region"] == "Tasgaon_Sangli_belt"
    assert result["total_fields_detected"] == 2847
    assert result["total_area_acres"] == 8234
    assert len(result["harvest_forecast"]) == 4

    peak = result["harvest_forecast"][1]
    assert peak["week_start"] == "2026-04-14"
    assert peak["week_end"] == "2026-04-20"
    assert peak["estimated_volume_mt"] == 680
    assert peak["fields_harvesting"] == 467

    # Belt-wide health distribution sums to 1.0 within float tolerance.
    health = result["health_distribution"]
    assert health["good"] == 0.63
    assert pytest.approx(sum(health.values()), rel=1e-6) == 1.0


def test_mock_vs_live_switch(monkeypatch):
    """With mock=false and an empty key, fall back through live -> mock."""
    monkeypatch.setenv("AMED_USE_MOCK", "true")
    client = AMEDClient()
    assert client._use_mock() is True
    mock_response = client.get_field_data(plot_id="plot_007")
    assert mock_response["source"] == "AMED"

    # Flip to live mode — the stub raises NotImplementedError, which the
    # facade must catch and recover from by serving the mock.
    monkeypatch.setenv("AMED_USE_MOCK", "false")
    assert client._use_mock() is False
    fallback_response = client.get_field_data(plot_id="plot_007")
    assert fallback_response["source"] == "AMED"
    # And the fallback must be deterministic too — equal to a fresh mock call.
    assert fallback_response["field_id"] == mock_response["field_id"]
    assert fallback_response["harvest_date_predicted"] == mock_response["harvest_date_predicted"]

    # Belt and historical also fall through cleanly.
    belt = client.get_belt_data(bbox=TASGAON_BBOX, crop_type="Grapes")
    assert belt["total_fields_detected"] == 2847
    hist = client.get_historical_data(
        bbox=TASGAON_BBOX,
        crop_type="Grapes",
        seasons=["2022-23", "2023-24", "2024-25"],
    )
    assert {row["season"] for row in hist} == {"2022-23", "2023-24", "2024-25"}


def test_deterministic_seeding():
    client = AMEDClient()
    first = client.get_field_data(plot_id="plot_001")
    second = client.get_field_data(plot_id="plot_001")
    assert first == second

    # And a different plot_id should change something material.
    other = client.get_field_data(plot_id="plot_999")
    assert other["field_id"] != first["field_id"]
