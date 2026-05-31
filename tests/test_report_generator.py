"""Tests for pipelines/report_generator.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.report_generator import (  # noqa: E402
    generate_daily_update_content,
    generate_flash_alert_content,
    generate_pre_season_content,
    generate_weekly_report_content,
)


# --------------------------------------------------------------------------- #
# Stub LLM clients
# --------------------------------------------------------------------------- #
class _MockLLM:
    def __init__(self, response: str = "MOCK REPORT 42"):
        self.response = response
        self.last_prompt: str | None = None

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


class _FailingLLM:
    def generate(self, prompt: str) -> str:  # noqa: ARG002
        raise RuntimeError("simulated LLM failure")


# --------------------------------------------------------------------------- #
# Fixture data
# --------------------------------------------------------------------------- #
@pytest.fixture
def weekly_kwargs():
    return dict(
        commodity="Dry Grapes",
        region="Tasgaon/Sangli Belt",
        signal_data={
            "signal": "BUY",
            "rationale": "Below fair value; supply tightening.",
            "entry_range": [118.0, 122.0],
            "target": 132.0,
            "stop": 110.0,
            "horizon_days": 5,
            "confidence": 0.71,
            "fair_value": 138.0,
            "discount_pct": 13.0,
        },
        belt_data={
            "week1_mt": 850, "week2_mt": 920, "week3_mt": 1010,
            "vs_avg_pct": -8, "bearing_year": "ON",
            "grade_a_pct": 32, "grade_b_pct": 51, "grade_c_pct": 17,
            "avg_brix": "19-21",
        },
        price_data={
            "current": 120, "day1": 121, "day3": 127, "day7": 132,
            "confidence": 71,
        },
        weather_data={"summary_7day": "Mostly clear; light showers Thu-Fri."},
    )


# --------------------------------------------------------------------------- #
# Weekly
# --------------------------------------------------------------------------- #
def test_weekly_stub_has_required_sections(weekly_kwargs):
    out = generate_weekly_report_content(**weekly_kwargs)
    assert isinstance(out, str) and out.strip()
    for needle in ("Weekly Market Intelligence", "SUPPLY", "QUALITY",
                   "PRICE", "SIGNAL", "WEATHER", "Disclaimer"):
        assert needle in out, f"missing section: {needle!r}\n--- output ---\n{out}"


def test_llm_client_used_when_provided(weekly_kwargs):
    llm = _MockLLM("MOCK REPORT 42")
    out = generate_weekly_report_content(**weekly_kwargs, llm_client=llm)
    assert out == "MOCK REPORT 42"
    assert llm.last_prompt is not None and "COMMODITY: Dry Grapes" in llm.last_prompt


def test_llm_failure_falls_back_to_stub(weekly_kwargs):
    out = generate_weekly_report_content(**weekly_kwargs, llm_client=_FailingLLM())
    # Stub fallback should be used; it contains the weekly section headers.
    assert "SIGNAL" in out and "Disclaimer" in out


# --------------------------------------------------------------------------- #
# Flash
# --------------------------------------------------------------------------- #
def test_flash_stub_has_emoji_and_signal():
    out = generate_flash_alert_content(
        commodity="Dry Grapes",
        trigger_event="Price drop > 8% vs yesterday",
        current_price=92.0,
        fair_value=110.0,
        signal="IMMEDIATE_BUY",
        confidence_pct=78,
        next_update_time="Today 18:00 IST",
    )
    assert "🚨" in out
    assert "FLASH ALERT" in out
    assert "IMMEDIATE_BUY" in out


# --------------------------------------------------------------------------- #
# Daily
# --------------------------------------------------------------------------- #
def test_daily_stub_is_short():
    out = generate_daily_update_content(
        commodity="Pomegranate",
        modal_price=92.5,
        change_pct=-1.2,
        arrivals_mt=420,
        vs_forecast_pct=5,
        tomorrow_low=90.0,
        tomorrow_high=95.0,
        confidence_pct=72,
        signal="HOLD",
    )
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) <= 8, f"daily stub too long ({len(lines)} lines):\n{out}"
    assert "Pomegranate" in out and "Signal: HOLD" in out


def test_pre_season_stub_includes_strategy():
    out = generate_pre_season_content(
        commodity="Mango Alphonso",
        region="Ratnagiri/Devgad",
        bearing_year="ON",
        belt_ndvi=0.62,
        vs_3yr_avg=8.5,
        expected_volume_mt=12000,
        peak_week="April Week 3",
    )
    assert "PRE-SEASON" in out
    assert "STRATEGY" in out
    assert "Ratnagiri/Devgad" in out
