"""Mango belt-level mock data tests (SDD §10 Agent 4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geo import amed_mock  # noqa: E402


def test_konkan_alphonso_belt():
    data = amed_mock.get_mango_belt_data("Konkan", "Alphonso")
    assert data["region"] == "Konkan"
    assert data["variety"] == "Alphonso"
    assert data["crop_type"] == "Mango"
    assert data["total_fields_detected"] == 1240
    assert data["total_area_acres"] == 4820
    assert data["estimated_total_volume_mt"] == 540
    assert data["peak_harvest_date"] == "2026-04-22"
    # Bbox must match SDD spec.
    assert data["bbox"]["north"] == 18.0
    assert data["bbox"]["south"] == 15.5
    # Forecast bell curve should have 4 weeks.
    assert len(data["harvest_forecast"]) == 4


def test_nashik_kesar_belt():
    data = amed_mock.get_mango_belt_data("Nashik", "Kesar")
    assert data["region"] == "Nashik"
    assert data["variety"] == "Kesar"
    assert data["total_fields_detected"] == 2860
    assert data["total_area_acres"] == 8120
    assert data["estimated_total_volume_mt"] == 980
    assert data["peak_harvest_date"] == "2026-05-18"
    assert data["bbox"]["north"] == 21.0
    assert data["bbox"]["south"] == 19.0


def test_vidarbha_dasheri_belt():
    data = amed_mock.get_mango_belt_data("Vidarbha", "Dasheri")
    assert data["region"] == "Vidarbha"
    assert data["variety"] == "Dasheri"
    assert data["total_fields_detected"] == 1410
    assert data["total_area_acres"] == 3940
    assert data["estimated_total_volume_mt"] == 420
    assert data["peak_harvest_date"] == "2026-06-24"
    assert data["bbox"]["north"] == 22.0
    assert data["bbox"]["south"] == 19.5
