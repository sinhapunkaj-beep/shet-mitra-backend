"""Unit tests for the revised AMED-primary daily pipeline.

Covers the deliverables from Agent 3 of the SDD swarm build:
  * Happy-path execution with a mocked AMEDClient.
  * Fallback when AMED fetch raises (harvest_source becomes
    ``ndvi_estimate`` and the pipeline does not raise).
  * Crop and area mismatch detection per SDD 6.2.
  * The three NDVI trend branches of
    :func:`pipelines.harvest_window.calculate_harvest_window`.
  * The 14-day cache reuse contract.

The tests use only ``pytest`` and ``unittest.mock`` — no third-party
fixtures or network calls.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Make the project root importable when pytest is launched from a parent dir.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.cache import AmedCache, CACHE_TTL_DAYS  # noqa: E402
from pipelines.harvest_window import calculate_harvest_window  # noqa: E402
from pipelines.ndvi_pipeline import AmedSentinelPipeline  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_amed_response(
    crop_type: str = "Grapes",
    field_size_acres: float = 3.1,
    harvest_date: str = "2026-04-18",
) -> dict:
    return {
        "field_id": "amed_field_test001",
        "crop_type": crop_type,
        "crop_variety_hint": "Thompson Seedless",
        "crop_confidence": 0.91,
        "field_size_acres": field_size_acres,
        "sowing_date": "2025-12-10",
        "harvest_date_predicted": harvest_date,
        "growth_stage": "berry_development",
        "growth_stage_confidence": 0.87,
        "irrigation_detected": True,
        "last_event": "irrigation",
        "last_event_date": "2026-04-08",
        "data_refresh_date": "2026-04-06",
        "source": "AMED",
        "use_mock": True,
    }


def _mock_belt_response() -> dict:
    return {
        "region": "Tasgaon_Sangli_belt",
        "crop_type": "Grapes",
        "total_fields_detected": 2847,
        "total_area_acres": 8234,
        "harvest_forecast": [
            {
                "week_start": "2026-04-14",
                "week_end": "2026-04-20",
                "fields_harvesting": 467,
                "estimated_volume_mt": 680,
            }
        ],
        "health_distribution": {
            "good": 0.63,
            "moderate": 0.24,
            "stressed": 0.10,
            "critical": 0.03,
        },
        "data_refresh_date": "2026-04-06",
        "source": "AMED",
    }


def _build_client(field_response: dict | None = None) -> MagicMock:
    """Build a MagicMock that imitates AMEDClient's two methods."""
    client = MagicMock()
    client.get_field_data.return_value = (
        field_response if field_response is not None else _mock_amed_response()
    )
    client.get_belt_data.return_value = _mock_belt_response()
    return client


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Happy-path
# ---------------------------------------------------------------------------

def test_pipeline_runs_with_amed_mock(tmp_path):
    """Pipeline returns expected keys and harvest_source == 'amed_confirmed'."""
    client = _build_client()
    cache = AmedCache(db_path=str(tmp_path / "missing.db"))  # no-op cache
    pipeline = AmedSentinelPipeline(
        amed_client=client, cache=cache, db_path=str(tmp_path / "missing.db")
    )

    plot = {
        "id": "plot-001",
        "current_crop": "Grapes",
        "area_acres": 3.1,
        "boundary_polygon": [[17.0374, 74.5958], [17.0380, 74.5965]],
    }

    result = _run(pipeline.run_full_pipeline("farmer-001", plot))

    expected_keys = {
        "amed",
        "belt",
        "sentinel",
        "harvest_window",
        "combined_health",
        "mismatches",
        "harvest_source",
        "harvest_confidence",
        "errors",
    }
    assert expected_keys.issubset(set(result.keys()))

    assert result["harvest_source"] == "amed_confirmed"
    assert result["amed"]["crop_type"] == "Grapes"
    assert result["belt"]["region"] == "Tasgaon_Sangli_belt"
    assert result["sentinel"]["ndvi"] is not None
    assert result["harvest_window"] is not None
    assert "start" in result["harvest_window"]
    assert "end" in result["harvest_window"]
    assert result["harvest_confidence"] > 0.0
    assert result["errors"] == []
    assert result["mismatches"] == []
    assert client.get_field_data.call_count == 1
    assert client.get_belt_data.call_count == 1


# ---------------------------------------------------------------------------
# 2. Fallback when AMED fails
# ---------------------------------------------------------------------------

def test_fallback_when_amed_unavailable(tmp_path):
    """If AMEDClient raises, pipeline returns harvest_source='ndvi_estimate'."""
    client = MagicMock()
    client.get_field_data.side_effect = RuntimeError("AMED API down")
    client.get_belt_data.side_effect = RuntimeError("AMED API down")

    cache = AmedCache(db_path=str(tmp_path / "missing.db"))
    pipeline = AmedSentinelPipeline(
        amed_client=client, cache=cache, db_path=str(tmp_path / "missing.db")
    )

    plot = {
        "id": "plot-002",
        "current_crop": "Grapes",
        "area_acres": 2.8,
    }

    result = _run(pipeline.run_full_pipeline("farmer-002", plot))

    assert result["amed"] is None
    assert result["belt"] is None
    assert result["harvest_source"] == "ndvi_estimate"
    # Pipeline did not raise — instead errors were captured.
    assert any("amed_field_fetch_failed" in e for e in result["errors"])
    assert any("amed_belt_fetch_failed" in e for e in result["errors"])
    # Sentinel-2 stub still produced a reading.
    assert result["sentinel"]["ndvi"] is not None
    assert result["harvest_window"] is not None  # NDVI-only window
    assert result["mismatches"] == []


# ---------------------------------------------------------------------------
# 3. Crop mismatch detection
# ---------------------------------------------------------------------------

def test_crop_mismatch_detected(tmp_path):
    """Farmer registered Grapes but AMED says Pomegranate -> mismatch."""
    client = _build_client(_mock_amed_response(crop_type="Pomegranate"))
    cache = AmedCache(db_path=str(tmp_path / "missing.db"))
    pipeline = AmedSentinelPipeline(
        amed_client=client, cache=cache, db_path=str(tmp_path / "missing.db")
    )

    plot = {
        "id": "plot-003",
        "current_crop": "Grapes",
        "area_acres": 3.1,
    }
    result = _run(pipeline.run_full_pipeline("farmer-003", plot))

    crop_mismatches = [m for m in result["mismatches"] if m["type"] == "crop_type"]
    assert len(crop_mismatches) == 1
    entry = crop_mismatches[0]
    assert entry["registered"] == "Grapes"
    assert entry["detected"] == "Pomegranate"
    assert "Grapes" in entry["message"]
    assert "Pomegranate" in entry["message"]


# ---------------------------------------------------------------------------
# 4. Area mismatch detection
# ---------------------------------------------------------------------------

def test_area_mismatch_detected(tmp_path):
    """Registered 3.2 acres but AMED says 5.1 acres -> > 20% mismatch."""
    client = _build_client(_mock_amed_response(field_size_acres=5.1))
    cache = AmedCache(db_path=str(tmp_path / "missing.db"))
    pipeline = AmedSentinelPipeline(
        amed_client=client, cache=cache, db_path=str(tmp_path / "missing.db")
    )

    plot = {
        "id": "plot-004",
        "current_crop": "Grapes",
        "area_acres": 3.2,
    }
    result = _run(pipeline.run_full_pipeline("farmer-004", plot))

    area_mismatches = [m for m in result["mismatches"] if m["type"] == "area"]
    assert len(area_mismatches) == 1
    entry = area_mismatches[0]
    assert entry["area_mismatch_pct"] > 20.0
    # 5.1 vs 3.2 -> 59.375% difference.
    assert entry["area_mismatch_pct"] == pytest.approx(59.38, rel=0.01)
    assert entry["registered_acres"] == 3.2
    assert entry["detected_acres"] == 5.1


# ---------------------------------------------------------------------------
# 5. calculate_harvest_window three branches (SDD 6.1)
# ---------------------------------------------------------------------------

def test_harvest_window_three_branches():
    base = date(2026, 4, 18)

    # Branch 1: NDVI declining normally -> -3 .. +4
    start, end, conf = calculate_harvest_window(
        base, {"state": "declining_normal", "quality": 1.0}, 0.9
    )
    assert start == base - timedelta(days=3)
    assert end == base + timedelta(days=4)
    assert conf == pytest.approx(0.9, rel=1e-3)

    # Branch 2: NDVI declining faster than expected -> -7 .. -1
    start, end, _ = calculate_harvest_window(
        base, {"state": "declining_fast", "quality": 1.0}, 0.9
    )
    assert start == base - timedelta(days=7)
    assert end == base - timedelta(days=1)

    # Branch 3: NDVI not yet declining -> 0 .. +10
    start, end, _ = calculate_harvest_window(
        base, {"state": "flat", "quality": 1.0}, 0.9
    )
    assert start == base
    assert end == base + timedelta(days=10)

    # Branch 3 also via slope heuristic (slope > -0.002 -> flat)
    start, end, _ = calculate_harvest_window(
        base, {"slope": -0.0005, "quality": 1.0}, 0.9
    )
    assert start == base
    assert end == base + timedelta(days=10)

    # Branch 2 via slope heuristic (slope < -0.006 -> declining_fast)
    start, end, _ = calculate_harvest_window(
        base, {"slope": -0.0080, "quality": 1.0}, 0.9
    )
    assert start == base - timedelta(days=7)
    assert end == base - timedelta(days=1)

    # Branch 1 via slope heuristic (-0.006 .. -0.002 -> declining_normal)
    start, end, _ = calculate_harvest_window(
        base, {"slope": -0.0040, "quality": 1.0}, 0.9
    )
    assert start == base - timedelta(days=3)
    assert end == base + timedelta(days=4)

    # String date input is accepted.
    start, end, _ = calculate_harvest_window(
        "2026-04-18", {"state": "declining_normal", "quality": 1.0}, 0.9
    )
    assert start == base - timedelta(days=3)


# ---------------------------------------------------------------------------
# 6. 14-day cache logic
# ---------------------------------------------------------------------------

def _build_temp_db(path: str) -> None:
    """Create a minimal SQLite schema sufficient for the cache."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE amed_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plot_id TEXT,
                fetch_date TEXT NOT NULL,
                crop_type_detected TEXT,
                crop_type_confidence REAL,
                field_size_acres_amed REAL,
                sowing_date TEXT,
                harvest_date_predicted TEXT,
                growth_stage TEXT,
                growth_stage_confidence REAL,
                irrigation_detected INTEGER,
                last_event TEXT,
                last_event_date TEXT,
                data_refresh_date TEXT,
                use_mock INTEGER DEFAULT 1,
                raw_response TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE amed_belt_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL,
                fetch_date TEXT NOT NULL,
                crop_type TEXT,
                total_fields_detected INTEGER,
                total_area_acres REAL,
                harvest_week_start TEXT,
                harvest_week_end TEXT,
                fields_harvesting INTEGER,
                estimated_volume_mt REAL,
                health_pct_good REAL,
                health_pct_moderate REAL,
                health_pct_stressed REAL,
                health_pct_critical REAL,
                data_refresh_date TEXT,
                raw_response TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE farm_plots (
                id TEXT PRIMARY KEY,
                current_crop TEXT,
                area_acres REAL,
                amed_crop_verified INTEGER DEFAULT 0,
                amed_field_id TEXT,
                amed_last_fetch TEXT,
                crop_type_mismatch INTEGER DEFAULT 0,
                area_mismatch_pct REAL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_cached_field(
    db_path: str, plot_id: str, days_ago: int, crop: str = "Grapes"
) -> None:
    fetch_date = (datetime.now().date() - timedelta(days=days_ago)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO amed_readings (
                plot_id, fetch_date, crop_type_detected, crop_type_confidence,
                field_size_acres_amed, harvest_date_predicted, growth_stage,
                use_mock, raw_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plot_id,
                fetch_date,
                crop,
                0.91,
                3.1,
                "2026-04-18",
                "berry_development",
                1,
                # raw_response stores the original API payload.
                __import__("json").dumps(_mock_amed_response(crop_type=crop)),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_14_day_cache_logic(tmp_path):
    """Fresh cache (<14 days) skips AMED; stale cache (>14 days) calls it."""
    db_path = str(tmp_path / "test.db")
    _build_temp_db(db_path)

    plot = {
        "id": "plot-cache-001",
        "current_crop": "Grapes",
        "area_acres": 3.1,
    }

    # ---- Case A: 5 days old -> cache HIT -> AMED not called.
    _insert_cached_field(db_path, plot["id"], days_ago=5)

    client = _build_client()
    cache = AmedCache(db_path=db_path)
    pipeline = AmedSentinelPipeline(
        amed_client=client, cache=cache, db_path=db_path
    )

    result = _run(pipeline.run_full_pipeline("farmer-cache-001", plot))
    assert client.get_field_data.call_count == 0, (
        "AMED field fetch should be skipped when cache is < 14 days old"
    )
    assert result["amed_cache_hit"] is True
    assert result["amed"] is not None
    assert result["amed"]["crop_type"] == "Grapes"

    # ---- Case B: only a 20-day-old row -> cache MISS -> AMED IS called.
    # Clear and reseed with a stale row.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM amed_readings WHERE plot_id = ?", (plot["id"],))
        conn.commit()
    finally:
        conn.close()
    _insert_cached_field(db_path, plot["id"], days_ago=20)

    client2 = _build_client()
    cache2 = AmedCache(db_path=db_path)
    pipeline2 = AmedSentinelPipeline(
        amed_client=client2, cache=cache2, db_path=db_path
    )

    result2 = _run(pipeline2.run_full_pipeline("farmer-cache-001", plot))
    assert client2.get_field_data.call_count == 1, (
        "AMED field fetch SHOULD run when only stale (>14d) cache rows exist"
    )
    assert result2["amed_cache_hit"] is False
    assert result2["amed"] is not None


# ---------------------------------------------------------------------------
# 7. Sanity check on CACHE_TTL_DAYS constant
# ---------------------------------------------------------------------------

def test_cache_ttl_constant_is_14_days():
    assert CACHE_TTL_DAYS == 14
