"""pipelines/report_generator.py

ShetMitra - Trader Intelligence - Report Generators
SDD sections 2.1, 2.2, 2.3, 2.4 and 5.3.

Each generator builds a Claude prompt matching the SDD 5.3 template, calls
``llm_client.generate(prompt)`` when one is provided, and falls back to a
deterministic WhatsApp-shaped stub on any error or when ``llm_client`` is
None. This keeps the production path (real Claude) and the test path
(zero network) on the same surface.

Public API
----------
    generate_weekly_report_content(commodity, region, signal_data, belt_data,
        price_data, weather_data, *, llm_client=None) -> str
    generate_flash_alert_content(commodity, trigger_event, current_price,
        fair_value, signal, *, confidence_pct, next_update_time,
        llm_client=None) -> str
    generate_daily_update_content(commodity, modal_price, change_pct,
        arrivals_mt, vs_forecast_pct, tomorrow_low, tomorrow_high,
        confidence_pct, signal, *, llm_client=None) -> str
    generate_pre_season_content(commodity, region, bearing_year, belt_ndvi,
        vs_3yr_avg, expected_volume_mt, peak_week, *, llm_client=None) -> str
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


_DISCLAIMER = (
    "Disclaimer: For informational purposes only. Not financial advice."
)
_FOOTER = (
    "Sahyadri Krushi Intelligence\n"
    "Data: AMED + Sentinel-2 + CEDA + Open-Meteo\n"
    f"{_DISCLAIMER}"
)


def _safe_get(d: Any, key: str, default: Any = "-") -> Any:
    if isinstance(d, dict):
        v = d.get(key, default)
        return default if v is None else v
    return default


def _call_llm(llm_client: Any, prompt: str, stub_fn, *stub_args, **stub_kwargs) -> str:
    """Call ``llm_client.generate(prompt)`` if provided; otherwise stub.

    Any exception from the LLM is caught and the stub is used as a fallback.
    """

    if llm_client is None:
        return stub_fn(*stub_args, **stub_kwargs)
    try:
        out = llm_client.generate(prompt)
        if not isinstance(out, str) or not out.strip():
            logger.warning("llm_client returned empty - falling back to stub")
            return stub_fn(*stub_args, **stub_kwargs)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_client.generate failed (%s) - falling back to stub", exc)
        return stub_fn(*stub_args, **stub_kwargs)


# --------------------------------------------------------------------------- #
# WEEKLY  (SDD 2.1 + 5.3)
# --------------------------------------------------------------------------- #
def _weekly_prompt(
    commodity: str,
    region: str,
    signal_data: dict,
    belt_data: dict,
    price_data: dict,
    weather_data: dict,
) -> str:
    return f"""
  Generate a concise trader intelligence report
  for WhatsApp. Professional tone. English only.
  No bullet points - use line breaks and emoji.
  Max 35 lines total.

  COMMODITY: {commodity}
  REGION: {region}
  SIGNAL: {_safe_get(signal_data, 'signal')}
  RATIONALE: {_safe_get(signal_data, 'rationale')}

  SUPPLY DATA:
    This week: {_safe_get(belt_data, 'week1_mt')} MT
    Next week: {_safe_get(belt_data, 'week2_mt')} MT
    Week+2: {_safe_get(belt_data, 'week3_mt')} MT
    vs 3yr average: {_safe_get(belt_data, 'vs_avg_pct')}%
    Bearing year: {_safe_get(belt_data, 'bearing_year')}

  QUALITY:
    Grade A: {_safe_get(belt_data, 'grade_a_pct')}%
    Grade B: {_safe_get(belt_data, 'grade_b_pct')}%
    Grade C: {_safe_get(belt_data, 'grade_c_pct')}%
    Avg Brix: {_safe_get(belt_data, 'avg_brix')}

  PRICE DATA:
    Current modal: Rs{_safe_get(price_data, 'current')}/kg
    Day 1 forecast: Rs{_safe_get(price_data, 'day1')}/kg
    Day 3 forecast: Rs{_safe_get(price_data, 'day3')}/kg
    Day 7 forecast: Rs{_safe_get(price_data, 'day7')}/kg
    Model confidence: {_safe_get(price_data, 'confidence')}%

  WEATHER:
    {_safe_get(weather_data, 'summary_7day')}

  Generate report following this exact format:
  [header with emoji]
  [supply section]
  [quality section]
  [price forecast section]
  [signal section with entry/target/stop]
  [weather impact]
  [footer with disclaimer]
""".strip("\n")


def _compose_stub_weekly_report(
    commodity: str,
    region: str,
    signal_data: dict,
    belt_data: dict,
    price_data: dict,
    weather_data: dict,
) -> str:
    today = date.today().isoformat()
    entry = _safe_get(signal_data, "entry_range", ["-", "-"])
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        entry_lo, entry_hi = entry[0], entry[1]
    else:
        entry_lo = entry_hi = "-"

    lines: list[str] = []
    lines.append(f"🌿 Weekly Market Intelligence")
    lines.append(f"Commodity: {commodity}")
    lines.append(f"Region: {region}")
    lines.append(f"Date: {today}")
    lines.append("")
    lines.append("SUPPLY FORECAST")
    lines.append(f"This week arrivals: {_safe_get(belt_data, 'week1_mt')} MT")
    lines.append(f"Next week: {_safe_get(belt_data, 'week2_mt')} MT")
    lines.append(f"Week+2: {_safe_get(belt_data, 'week3_mt')} MT")
    lines.append(f"vs 3yr avg: {_safe_get(belt_data, 'vs_avg_pct')}%")
    lines.append(f"Bearing year: {_safe_get(belt_data, 'bearing_year')}")
    lines.append("")
    lines.append("QUALITY DISTRIBUTION")
    lines.append(f"Grade A: {_safe_get(belt_data, 'grade_a_pct')}%")
    lines.append(f"Grade B: {_safe_get(belt_data, 'grade_b_pct')}%")
    lines.append(f"Grade C: {_safe_get(belt_data, 'grade_c_pct')}%")
    lines.append(f"Avg Brix: {_safe_get(belt_data, 'avg_brix')}")
    lines.append("")
    lines.append("PRICE FORECAST")
    lines.append(f"Today modal: Rs{_safe_get(price_data, 'current')}/kg")
    lines.append(f"Day 1: Rs{_safe_get(price_data, 'day1')}/kg")
    lines.append(f"Day 3: Rs{_safe_get(price_data, 'day3')}/kg")
    lines.append(f"Day 7: Rs{_safe_get(price_data, 'day7')}/kg")
    lines.append(f"Confidence: {_safe_get(price_data, 'confidence')}%")
    lines.append("")
    lines.append("SIGNAL")
    lines.append(f"{_safe_get(signal_data, 'signal')}")
    lines.append(f"Rationale: {_safe_get(signal_data, 'rationale')}")
    # Surface a disruption note when the price predictor / signal engine
    # flagged unusual market conditions (>15% predicted day-1 move).
    if signal_data.get("unusual_market_conditions"):
        lines.append("")
        lines.append("⚠ UNUSUAL MARKET CONDITIONS")
        lines.append(
            signal_data.get("disruption_message")
            or "Unusual market conditions detected. Price forecast suspended. "
               "Monitor daily."
        )
        delta = signal_data.get("disruption_delta_pct")
        if delta is not None:
            lines.append(f"Predicted day-1 move: {delta:+.1f}% vs current price.")
    lines.append(f"Entry: Rs{entry_lo}-{entry_hi}/kg")
    lines.append(f"Target: Rs{_safe_get(signal_data, 'target')}/kg")
    lines.append(f"Stop: Rs{_safe_get(signal_data, 'stop')}/kg")
    lines.append(f"Horizon: {_safe_get(signal_data, 'horizon_days')} days")
    lines.append("")
    lines.append("WEATHER")
    lines.append(f"{_safe_get(weather_data, 'summary_7day')}")
    lines.append("")
    lines.append(_FOOTER)
    return "\n".join(lines)


def generate_weekly_report_content(
    commodity: str,
    region: str,
    signal_data: dict,
    belt_data: dict,
    price_data: dict,
    weather_data: dict,
    *,
    llm_client: Any = None,
) -> str:
    """Build a weekly trader report for ``commodity`` in ``region``.

    When ``llm_client`` is provided, calls its ``generate(prompt) -> str``.
    Falls back to a deterministic WhatsApp-shaped stub otherwise (or on
    any LLM error).
    """

    prompt = _weekly_prompt(
        commodity, region, signal_data, belt_data, price_data, weather_data
    )
    return _call_llm(
        llm_client,
        prompt,
        _compose_stub_weekly_report,
        commodity, region, signal_data, belt_data, price_data, weather_data,
    )


# --------------------------------------------------------------------------- #
# FLASH  (SDD 2.2)
# --------------------------------------------------------------------------- #
def _flash_prompt(
    commodity: str,
    trigger_event: str,
    current_price: float,
    fair_value: float,
    signal: str,
    confidence_pct: float,
    next_update_time: str,
) -> str:
    return f"""
  Generate a WhatsApp flash alert for traders.
  Professional, urgent tone. English only. Max 12 lines.

  COMMODITY: {commodity}
  TRIGGER: {trigger_event}
  CURRENT_PRICE: Rs{current_price}/kg
  FAIR_VALUE: Rs{fair_value}/kg
  SIGNAL: {signal}
  CONFIDENCE: {confidence_pct}%
  NEXT_UPDATE: {next_update_time}

  Use this exact structure:
  [emoji header with FLASH ALERT and commodity]
  [date - time]
  [signal line + window]
  [trigger description]
  [current vs fair value]
  [2-3 sentence reasoning]
  [confidence and next update]
""".strip("\n")


def _compose_stub_flash_report(
    commodity: str,
    trigger_event: str,
    current_price: float,
    fair_value: float,
    signal: str,
    confidence_pct: float,
    next_update_time: str,
) -> str:
    now = datetime.now()
    try:
        fv_lo = float(fair_value) * 0.97
        fv_hi = float(fair_value) * 1.03
        discount = (float(fair_value) - float(current_price)) / float(fair_value) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        fv_lo = fv_hi = float(fair_value or 0.0)
        discount = 0.0

    lines: list[str] = []
    lines.append(f"🚨 FLASH ALERT - {commodity}")
    lines.append(f"{now.date().isoformat()} - {now.strftime('%H:%M')}")
    lines.append("")
    lines.append(f"Signal: {signal}")
    lines.append("Window: Next 12 hours only")
    lines.append("")
    lines.append(f"Trigger: {trigger_event}")
    lines.append(f"Current price: Rs{current_price}/kg")
    lines.append(f"Fair value estimate: Rs{fv_lo:.0f}-{fv_hi:.0f}/kg")
    lines.append(f"Discount/premium: {discount:+.1f}%")
    lines.append("")
    lines.append(
        f"Reasoning: {trigger_event} detected. Signal {signal} reflects "
        "the gap between current modal and our fair-value estimate. "
        "Re-confirm with your local mandi before committing volume."
    )
    lines.append("")
    lines.append(f"Confidence: {confidence_pct}%")
    lines.append(f"Next update: {next_update_time}")
    lines.append("")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def generate_flash_alert_content(
    commodity: str,
    trigger_event: str,
    current_price: float,
    fair_value: float,
    signal: str,
    *,
    confidence_pct: float,
    next_update_time: str,
    llm_client: Any = None,
) -> str:
    prompt = _flash_prompt(
        commodity, trigger_event, current_price, fair_value, signal,
        confidence_pct, next_update_time,
    )
    return _call_llm(
        llm_client,
        prompt,
        _compose_stub_flash_report,
        commodity, trigger_event, current_price, fair_value, signal,
        confidence_pct, next_update_time,
    )


# --------------------------------------------------------------------------- #
# DAILY  (SDD 2.4)
# --------------------------------------------------------------------------- #
def _daily_prompt(
    commodity: str, modal_price: float, change_pct: float, arrivals_mt: float,
    vs_forecast_pct: float, tomorrow_low: float, tomorrow_high: float,
    confidence_pct: float, signal: str,
) -> str:
    return f"""
  Generate a 5-line WhatsApp daily price update for traders.

  COMMODITY: {commodity}
  MODAL: Rs{modal_price}/kg
  CHANGE_VS_YESTERDAY_PCT: {change_pct}
  ARRIVALS_MT: {arrivals_mt}
  VS_FORECAST_PCT: {vs_forecast_pct}
  TOMORROW_LOW: Rs{tomorrow_low}/kg
  TOMORROW_HIGH: Rs{tomorrow_high}/kg
  CONFIDENCE_PCT: {confidence_pct}
  SIGNAL: {signal}

  Exactly five lines:
  Line 1: emoji + commodity + date
  Line 2: Modal + change vs yesterday
  Line 3: Arrivals + vs forecast
  Line 4: Tomorrow Rs low-high + confidence
  Line 5: Signal
""".strip("\n")


def _compose_stub_daily_report(
    commodity: str, modal_price: float, change_pct: float, arrivals_mt: float,
    vs_forecast_pct: float, tomorrow_low: float, tomorrow_high: float,
    confidence_pct: float, signal: str,
) -> str:
    today = date.today().isoformat()
    return "\n".join([
        f"📊 {commodity} - {today}",
        f"Modal: Rs{modal_price}/kg ({change_pct:+.1f}% vs yesterday)",
        f"Arrivals: {arrivals_mt} MT ({vs_forecast_pct:+.0f}% vs forecast)",
        f"Tomorrow: Rs{tomorrow_low}-{tomorrow_high}/kg ({confidence_pct:.0f}%)",
        f"Signal: {signal}",
    ])


def generate_daily_update_content(
    commodity: str, modal_price: float, change_pct: float, arrivals_mt: float,
    vs_forecast_pct: float, tomorrow_low: float, tomorrow_high: float,
    confidence_pct: float, signal: str,
    *,
    llm_client: Any = None,
) -> str:
    prompt = _daily_prompt(
        commodity, modal_price, change_pct, arrivals_mt, vs_forecast_pct,
        tomorrow_low, tomorrow_high, confidence_pct, signal,
    )
    return _call_llm(
        llm_client,
        prompt,
        _compose_stub_daily_report,
        commodity, modal_price, change_pct, arrivals_mt, vs_forecast_pct,
        tomorrow_low, tomorrow_high, confidence_pct, signal,
    )


# --------------------------------------------------------------------------- #
# PRE-SEASON  (SDD 2.3)
# --------------------------------------------------------------------------- #
def _pre_season_prompt(
    commodity: str, region: str, bearing_year: str, belt_ndvi: float,
    vs_3yr_avg: float, expected_volume_mt: float, peak_week: str,
) -> str:
    return f"""
  Generate a pre-season forecast for traders, WhatsApp format.
  Professional tone. English only. Max 30 lines.

  COMMODITY: {commodity}
  REGION: {region}
  BEARING_YEAR: {bearing_year}
  BELT_NDVI: {belt_ndvi}
  VS_3YR_AVG: {vs_3yr_avg}%
  EXPECTED_VOLUME_MT: {expected_volume_mt}
  PEAK_WEEK: {peak_week}

  Use this exact structure:
  [emoji header with PRE-SEASON FORECAST]
  [bearing year and belt NDVI]
  [expected volume and peak week]
  [price range outlook for the season]
  [strategic recommendation]
  [key risks]
  [disclaimer footer]
""".strip("\n")


def _compose_stub_pre_season_report(
    commodity: str, region: str, bearing_year: str, belt_ndvi: float,
    vs_3yr_avg: float, expected_volume_mt: float, peak_week: str,
) -> str:
    lines: list[str] = []
    lines.append(f"🌿 PRE-SEASON FORECAST - {commodity}")
    lines.append(f"Region: {region}")
    lines.append("")
    lines.append(f"Bearing year: {bearing_year}")
    lines.append(f"Belt NDVI: {belt_ndvi}")
    lines.append(f"vs 3-year average: {vs_3yr_avg:+.1f}%")
    lines.append(f"Expected season volume: {expected_volume_mt} MT")
    lines.append(f"Peak week: {peak_week}")
    lines.append("")
    lines.append("STRATEGY")
    lines.append("Build inventory early-season at favourable prices,")
    lines.append("lighten exposure ahead of peak-week arrivals,")
    lines.append("re-enter late-season if quality holds.")
    lines.append("")
    lines.append("KEY RISKS")
    lines.append("Weather shocks during flowering / harvest window,")
    lines.append("export demand swings, USD/INR volatility.")
    lines.append("")
    lines.append(_FOOTER)
    return "\n".join(lines)


def generate_pre_season_content(
    commodity: str, region: str, bearing_year: str, belt_ndvi: float,
    vs_3yr_avg: float, expected_volume_mt: float, peak_week: str,
    *,
    llm_client: Any = None,
) -> str:
    prompt = _pre_season_prompt(
        commodity, region, bearing_year, belt_ndvi, vs_3yr_avg,
        expected_volume_mt, peak_week,
    )
    return _call_llm(
        llm_client,
        prompt,
        _compose_stub_pre_season_report,
        commodity, region, bearing_year, belt_ndvi, vs_3yr_avg,
        expected_volume_mt, peak_week,
    )


__all__ = [
    "generate_weekly_report_content",
    "generate_flash_alert_content",
    "generate_daily_update_content",
    "generate_pre_season_content",
]
