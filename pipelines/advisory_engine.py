"""ShetMitra - Variety-aware advisory engine.

This module is the M6 advisory engine that consumes the AMED + Sentinel
pipeline output, looks up variety-specific agronomic targets, computes a
revenue potential figure that respects mandi grade premia, and assembles
the Claude LLM prompt.

The module is deliberately decoupled from any LLM SDK: callers either
provide an ``llm_client`` with a ``.generate(prompt)`` method, or accept
the deterministic stub narrative that this module produces from the
inputs. That makes the module testable without network access.

Public API
----------
    load_variety_config(path)            -> dict
    get_variety_config(crop_type, variety) -> dict
    estimate_brix_from_reci(reci)        -> float
    calculate_revenue_potential(...)     -> dict
    build_claude_prompt(...)             -> str
    generate_advisory(...)               -> dict
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Optional


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "data" / "variety_config.json"

#: Hard-coded fallback used when the crop is not present in the config at all.
GLOBAL_FALLBACK_VARIETY_CONFIG: dict[str, Any] = {
    "brix_target_min": 14,
    "brix_target_max": 20,
    "maturity_days": 120,
    "spray_schedule": "standard_generic",
    "mandi_grade_premium": False,
    "price_premium_pct": 0,
}


# --------------------------------------------------------------------------- #
# mtime-aware config cache
# --------------------------------------------------------------------------- #

# Maps absolute path -> (mtime_ns, parsed_dict).
_CONFIG_CACHE: dict[str, tuple[int, dict]] = {}
_CACHE_LOCK = RLock()


def _resolve_config_path(path: str | os.PathLike[str] | None) -> Path:
    """Return an absolute Path for the requested config location."""
    if path is None:
        return _DEFAULT_CONFIG_PATH
    p = Path(path)
    if not p.is_absolute():
        # Resolve relative paths against the project root so that the
        # canonical default ``"data/variety_config.json"`` works no matter
        # what the caller's current working directory is.
        p = (_PROJECT_ROOT / p).resolve()
    else:
        p = p.resolve()
    return p


def load_variety_config(path: str | os.PathLike[str] = "data/variety_config.json") -> dict:
    """Load and cache the variety config JSON.

    The cache is keyed by absolute path and invalidated when the file's
    mtime changes. ``functools.lru_cache`` would happily serve stale data
    after an on-disk edit, so we roll a tiny mtime-aware cache here.
    """
    resolved = _resolve_config_path(path)
    try:
        mtime_ns = resolved.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"variety_config.json not found at {resolved}"
        ) from exc

    key = str(resolved)
    with _CACHE_LOCK:
        cached = _CONFIG_CACHE.get(key)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]

        with resolved.open("r", encoding="utf-8") as fh:
            parsed = json.load(fh)

        if not isinstance(parsed, dict):
            raise ValueError(
                f"variety_config.json must be a JSON object, got {type(parsed).__name__}"
            )
        _CONFIG_CACHE[key] = (mtime_ns, parsed)
        return parsed


def _clear_config_cache() -> None:
    """Drop every cached entry. Exposed for tests that need a hard reset."""
    with _CACHE_LOCK:
        _CONFIG_CACHE.clear()


# --------------------------------------------------------------------------- #
# Variety lookup
# --------------------------------------------------------------------------- #

def get_variety_config(
    crop_type: str | None,
    variety: str | None,
    config_path: str | os.PathLike[str] = "data/variety_config.json",
) -> dict:
    """Resolve a (crop, variety) pair to a config dict.

    Resolution order:
      1. Exact variety entry under the crop bucket.
      2. The crop bucket's ``default`` entry.
      3. The module-level ``GLOBAL_FALLBACK_VARIETY_CONFIG``.

    A returned dict is always a fresh shallow copy so callers can mutate
    it without contaminating the cache.
    """
    config = load_variety_config(config_path)
    crop_bucket = config.get(crop_type or "", None)

    if not isinstance(crop_bucket, dict):
        return dict(GLOBAL_FALLBACK_VARIETY_CONFIG)

    if variety:
        variety_entry = crop_bucket.get(variety)
        if isinstance(variety_entry, dict):
            return dict(variety_entry)

    default_entry = crop_bucket.get("default")
    if isinstance(default_entry, dict):
        return dict(default_entry)

    return dict(GLOBAL_FALLBACK_VARIETY_CONFIG)


# --------------------------------------------------------------------------- #
# Brix estimation
# --------------------------------------------------------------------------- #

def estimate_brix_from_reci(reci: float | None) -> float:
    """Approximate Brix (degrees) from a Sentinel-2 RECI reading.

    Calibrated as a quick proxy for the Claude prompt rather than as a
    lab-grade model:

        brix = clip(8 + (reci - 0.5) * 15, 4, 30)

    ``None`` and non-finite inputs fall back to the linear value for
    RECI=1.0 (so the prompt stays well-formed).
    """
    try:
        r = float(reci) if reci is not None else 1.0
    except (TypeError, ValueError):
        r = 1.0
    raw = 8.0 + (r - 0.5) * 15.0
    return float(max(4.0, min(30.0, raw)))


# --------------------------------------------------------------------------- #
# Revenue potential
# --------------------------------------------------------------------------- #

def calculate_revenue_potential(
    predicted_yield_kg_per_acre: float,
    area_acres: float,
    base_modal_price: float,
    variety_config: Mapping[str, Any],
) -> dict:
    """Compute the variety-adjusted revenue potential.

    If the variety carries a mandi grade premium, the expected price is
    bumped by ``price_premium_pct``. Revenue is always
    ``yield * expected_price * area``.
    """
    yield_kg = float(predicted_yield_kg_per_acre or 0.0)
    area = float(area_acres or 0.0)
    base = float(base_modal_price or 0.0)

    is_premium = bool(variety_config.get("mandi_grade_premium", False))
    premium_pct = float(variety_config.get("price_premium_pct", 0) or 0)

    if is_premium and premium_pct:
        expected = base * (1.0 + premium_pct / 100.0)
    else:
        expected = base
        # When the variety isn't flagged premium we surface 0% so that the
        # UI/report doesn't accidentally show a premium that we did not apply.
        if not is_premium:
            premium_pct = 0.0

    revenue = yield_kg * expected * area

    return {
        "base_price_kg": round(base, 2),
        "expected_price_kg": round(expected, 2),
        "premium_pct": round(premium_pct, 2),
        "revenue_potential_inr": round(revenue, 2),
        "is_premium_variety": is_premium,
    }


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #

def _fmt(value: Any, default: str = "n/a") -> str:
    """Render a value for the prompt, treating None as 'n/a'."""
    if value is None:
        return default
    if isinstance(value, float):
        if value != value:  # NaN guard
            return default
        return f"{value:.2f}"
    return str(value)


def _format_weather(weather: Mapping[str, Any] | None) -> str:
    """Render the weather payload as a short multi-day summary."""
    if not weather:
        return "n/a"
    forecast = weather.get("forecast_7day") or weather.get("forecast") or []
    if isinstance(forecast, str):
        return forecast
    if isinstance(forecast, list) and forecast:
        lines = []
        for day in forecast:
            if isinstance(day, Mapping):
                date_s = day.get("date", "?")
                tmax = day.get("temp_max", day.get("max"))
                tmin = day.get("temp_min", day.get("min"))
                rain = day.get("rain_mm", day.get("rain"))
                lines.append(
                    f"{date_s}: max {_fmt(tmax)}C / min {_fmt(tmin)}C / rain {_fmt(rain)} mm"
                )
            else:
                lines.append(str(day))
        return "\n      ".join(lines)
    summary = weather.get("summary")
    if summary:
        return str(summary)
    return "n/a"


def build_claude_prompt(
    plot: Mapping[str, Any],
    amed: Mapping[str, Any] | None,
    sentinel: Mapping[str, Any] | None,
    harvest_window: Mapping[str, Any] | None,
    market: Mapping[str, Any] | None,
    weather: Mapping[str, Any] | None,
    variety_config: Mapping[str, Any],
) -> str:
    """Compose the full variety-aware advisory prompt.

    Layout follows AMED SDD section 6.3 (AMED / Sentinel / Regional /
    Market / Weather blocks) and prepends the VARIETY DETAILS block from
    the variety collection spec. The prompt ends with the required
    "Provide variety-specific advice for {variety}." line.
    """
    amed = amed or {}
    sentinel = sentinel or {}
    market = market or {}
    plot = plot or {}

    variety = (
        plot.get("current_crop_variety")
        or variety_config.get("variety_name")
        or "default"
    )
    variety_source = plot.get("variety_source", "farmer_reported")
    crop_type = plot.get("current_crop") or amed.get("crop_type") or "Unknown"

    brix_min = variety_config.get("brix_target_min", 0)
    brix_max = variety_config.get("brix_target_max", 0)
    maturity_days = variety_config.get("maturity_days", 0)
    spray_schedule = variety_config.get("spray_schedule", "standard_generic")
    grade_premium = variety_config.get("mandi_grade_premium", False)
    price_premium = variety_config.get("price_premium_pct", 0)

    estimated_brix = estimate_brix_from_reci(sentinel.get("reci"))

    # Harvest window line.
    hw_start = (harvest_window or {}).get("start", "n/a")
    hw_end = (harvest_window or {}).get("end", "n/a")
    hw_conf = (harvest_window or {}).get("confidence")

    # Sentinel readings.
    ndvi = sentinel.get("ndvi")
    reci = sentinel.get("reci")
    ndwi = sentinel.get("ndwi")
    lai = sentinel.get("lai")
    npp = sentinel.get("npp")
    npp_anomaly = sentinel.get("npp_anomaly")
    prithvi_vigour = sentinel.get("prithvi_vigour")
    prithvi_stress = sentinel.get("prithvi_stress")
    prithvi_phenology = sentinel.get("prithvi_phenology")

    # AMED block.
    amed_crop = amed.get("crop_type", crop_type)
    amed_conf = amed.get("crop_confidence")
    amed_field_size = amed.get("field_size_acres")
    amed_sowing = amed.get("sowing_date")
    amed_harvest = amed.get("harvest_date_predicted")
    amed_stage = amed.get("growth_stage")
    amed_last_event = amed.get("last_event_date") or amed.get("last_irrigation_date")
    amed_avg_harvest = amed.get("avg_harvest_date_3yr") or amed.get("avg_harvest_date")

    # Regional / belt block.
    belt = (market.get("belt") if isinstance(market, Mapping) else None) or {}
    belt_volume_mt = belt.get("belt_volume_mt") or market.get("belt_volume_mt")
    belt_volume_next = (
        belt.get("belt_volume_next_mt") or market.get("belt_volume_next_mt")
    )
    health_good = belt.get("health_pct_good") or market.get("health_pct_good")
    fields_harvesting = (
        belt.get("fields_harvesting") or market.get("fields_harvesting")
    )

    # Market block.
    current_price = market.get("current_price")
    predictions = market.get("predictions") or []
    pred_day1 = predictions[0] if len(predictions) >= 1 else None
    pred_day2 = predictions[1] if len(predictions) >= 2 else None
    pred_day3 = predictions[2] if len(predictions) >= 3 else None
    model_confidence = market.get("confidence") or market.get("model_confidence")
    supply_pressure = market.get("supply_pressure", "medium")

    weather_block = _format_weather(weather)

    prompt = (
        "You are an expert agronomist for the Maharashtra Sangli belt.\n"
        "Generate a daily advisory tailored to the specific variety below.\n"
        "\n"
        "VARIETY DETAILS:\n"
        f"  Variety: {variety} ({variety_source})\n"
        f"  Crop: {crop_type}\n"
        f"  Brix target: {brix_min}-{brix_max}\n"
        f"  Maturity: {maturity_days} days from sowing\n"
        f"  Current RECI estimate: {estimated_brix:.2f}\n"
        f"  Spray schedule template: {spray_schedule}\n"
        f"  Grade premium variety: {grade_premium}\n"
        f"  Price premium vs standard: +{price_premium}%\n"
        "\n"
        "FARM DATA (AMED confirmed):\n"
        f"  Crop: {_fmt(amed_crop)} (confidence {_fmt(amed_conf)})\n"
        f"  Field size: {_fmt(amed_field_size)} acres\n"
        f"  Sowing date: {_fmt(amed_sowing)}\n"
        f"  Predicted harvest: {_fmt(amed_harvest)}\n"
        f"  Growth stage: {_fmt(amed_stage)}\n"
        f"  Last irrigation: {_fmt(amed_last_event)}\n"
        f"  3-year avg harvest: {_fmt(amed_avg_harvest)}\n"
        "\n"
        "SATELLITE HEALTH (Sentinel-2):\n"
        f"  NDVI: {_fmt(ndvi)} | RECI: {_fmt(reci)} | NDWI: {_fmt(ndwi)}\n"
        f"  LAI: {_fmt(lai)} | NPP: {_fmt(npp)} | NPP anomaly: {_fmt(npp_anomaly)}%\n"
        f"  Prithvi vigour: {_fmt(prithvi_vigour)}\n"
        f"  Prithvi stress: {_fmt(prithvi_stress)}\n"
        f"  Phenology: {_fmt(prithvi_phenology)}\n"
        "\n"
        "REGIONAL CONTEXT (AMED belt data):\n"
        f"  Belt harvest this week: {_fmt(belt_volume_mt)} MT\n"
        f"  Belt harvest next week: {_fmt(belt_volume_next)} MT\n"
        f"  Regional health good: {_fmt(health_good)}%\n"
        f"  Farms actively harvesting: {_fmt(fields_harvesting)}\n"
        "\n"
        "MARKET (ARIMA + AMED enhanced):\n"
        f"  Current price: Rs {_fmt(current_price)}/kg\n"
        f"  3-day prediction: Rs {_fmt(pred_day1)}, Rs {_fmt(pred_day2)}, Rs {_fmt(pred_day3)}\n"
        f"  Model confidence: {_fmt(model_confidence)}\n"
        f"  Supply pressure: {supply_pressure}\n"
        "\n"
        "HARVEST WINDOW (combined):\n"
        f"  Start: {_fmt(hw_start)} | End: {_fmt(hw_end)} | Confidence: {_fmt(hw_conf)}\n"
        "\n"
        "WEATHER (Open-Meteo):\n"
        f"      {weather_block}\n"
        "\n"
        "Provide:\n"
        "1. Spray advisory (SPRAY NOW / DELAY / AVOID + reason)\n"
        "2. Harvest window (start date to end date)\n"
        "3. Yield prediction (kg/acre range + confidence)\n"
        "4. Grade prediction (A/B/C + reasoning)\n"
        "5. Market timing (sell now / hold X days + reason)\n"
        "6. One critical alert if any (disease risk, weather risk, market risk)\n"
        "\n"
        f"Provide variety-specific advice for {variety}."
    )
    return prompt


# --------------------------------------------------------------------------- #
# Deterministic stub narrative
# --------------------------------------------------------------------------- #

def _stub_narrative(
    *,
    variety: str,
    variety_source: str,
    estimated_brix: float,
    variety_config: Mapping[str, Any],
    harvest_window: Mapping[str, Any] | None,
    revenue: Mapping[str, Any],
    market: Mapping[str, Any] | None,
) -> str:
    """Build a deterministic placeholder narrative without an LLM."""
    brix_min = variety_config.get("brix_target_min", 0)
    brix_max = variety_config.get("brix_target_max", 0)
    spray_schedule = variety_config.get("spray_schedule", "standard_generic")
    is_premium = revenue.get("is_premium_variety", False)
    expected_price = revenue.get("expected_price_kg", 0.0)
    revenue_inr = revenue.get("revenue_potential_inr", 0.0)
    supply_pressure = (market or {}).get("supply_pressure", "medium")

    hw_start = (harvest_window or {}).get("start", "n/a")
    hw_end = (harvest_window or {}).get("end", "n/a")

    brix_status = (
        "on target"
        if brix_min <= estimated_brix <= brix_max
        else ("below target — extend ripening" if estimated_brix < brix_min else "above target — schedule harvest")
    )

    return (
        f"Variety advisory for {variety} ({variety_source}).\n"
        f"Estimated Brix from satellite RECI: {estimated_brix:.2f} "
        f"(target {brix_min}-{brix_max}, {brix_status}).\n"
        f"Recommended spray schedule: {spray_schedule}.\n"
        f"Harvest window: {hw_start} to {hw_end}.\n"
        f"Expected mandi price: Rs {expected_price}/kg "
        f"({'premium variety' if is_premium else 'standard pricing'}).\n"
        f"Projected revenue potential: Rs {revenue_inr}.\n"
        f"Supply pressure: {supply_pressure}.\n"
        "This is a deterministic stub narrative — provide an llm_client to "
        "generate the full advisory."
    )


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def generate_advisory(
    plot: Mapping[str, Any],
    amed: Mapping[str, Any] | None,
    sentinel: Mapping[str, Any] | None,
    harvest_window: Mapping[str, Any] | None,
    market: Mapping[str, Any] | None,
    weather: Mapping[str, Any] | None,
    *,
    llm_client: Optional[Any] = None,
    config_path: str | os.PathLike[str] = "data/variety_config.json",
) -> dict:
    """End-to-end advisory orchestration.

    Looks up the variety config, computes a Brix estimate, derives the
    revenue potential, builds the Claude prompt, and either calls
    ``llm_client.generate(prompt)`` for the narrative or emits a
    deterministic stub.
    """
    plot = plot or {}
    sentinel = sentinel or {}
    market = market or {}

    crop_type = plot.get("current_crop") or (amed or {}).get("crop_type") or ""
    variety = plot.get("current_crop_variety")
    variety_source = plot.get("variety_source", "farmer_reported")

    variety_config = get_variety_config(crop_type, variety, config_path=config_path)
    estimated_brix = estimate_brix_from_reci(sentinel.get("reci"))

    predicted_yield = float(
        market.get("predicted_yield_kg_per_acre")
        or plot.get("predicted_yield_kg_per_acre")
        or 0.0
    )
    area_acres = float(
        plot.get("self_reported_acres")
        or plot.get("area_acres")
        or (amed or {}).get("field_size_acres")
        or 0.0
    )
    base_modal_price = float(
        market.get("current_price")
        or market.get("base_modal_price")
        or 0.0
    )

    revenue = calculate_revenue_potential(
        predicted_yield_kg_per_acre=predicted_yield,
        area_acres=area_acres,
        base_modal_price=base_modal_price,
        variety_config=variety_config,
    )

    prompt = build_claude_prompt(
        plot=plot,
        amed=amed,
        sentinel=sentinel,
        harvest_window=harvest_window,
        market=market,
        weather=weather,
        variety_config=variety_config,
    )

    if llm_client is not None and hasattr(llm_client, "generate"):
        try:
            narrative = llm_client.generate(prompt)
            if not isinstance(narrative, str):
                narrative = str(narrative)
        except Exception as exc:  # noqa: BLE001
            narrative = (
                "LLM generation failed: "
                f"{exc.__class__.__name__}: {exc}\n"
                + _stub_narrative(
                    variety=variety or "default",
                    variety_source=variety_source,
                    estimated_brix=estimated_brix,
                    variety_config=variety_config,
                    harvest_window=harvest_window,
                    revenue=revenue,
                    market=market,
                )
            )
    else:
        narrative = _stub_narrative(
            variety=variety or "default",
            variety_source=variety_source,
            estimated_brix=estimated_brix,
            variety_config=variety_config,
            harvest_window=harvest_window,
            revenue=revenue,
            market=market,
        )

    return {
        "variety": variety or "default",
        "variety_source": variety_source,
        "variety_config": variety_config,
        "brix_estimate": round(estimated_brix, 2),
        "brix_target_min": int(variety_config.get("brix_target_min", 0)),
        "brix_target_max": int(variety_config.get("brix_target_max", 0)),
        "maturity_days": int(variety_config.get("maturity_days", 0)),
        "spray_schedule": str(variety_config.get("spray_schedule", "")),
        "revenue_potential": revenue,
        "harvest_window": dict(harvest_window) if harvest_window else None,
        "narrative": narrative,
        "prompt": prompt,
    }


# --------------------------------------------------------------------------- #
# Mango advisory (SDD section 8)
# --------------------------------------------------------------------------- #

#: Bearing-year multipliers applied to predicted yield per SDD section 2.2.
_BEARING_YEAR_MULTIPLIERS = {
    "ON": 1.00,
    "OFF": 0.45,
    "UNKNOWN": 0.75,
}


def _to_date(value):
    """Return a ``datetime.date`` or None for a flexible date input."""
    import datetime as _dt

    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return _dt.datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def _detect_mango_advisory_type(
    plot, sentinel, phenology, harvest_window, reference_date=None
):
    """Detect the seasonal advisory type per SDD section 8.2.

    Order:
      1. FLOWERING   -- month in (12, 1, 2) AND NDVI drop > 0.10
      2. FRUIT_SET   -- phenology flag set
      3. HARVEST_READINESS -- days_to_harvest <= 21
      4. FRUIT_DEVELOPMENT -- default
    """
    import datetime as _dt

    plot = plot or {}
    sentinel = sentinel or {}
    phenology = phenology or {}
    harvest_window = harvest_window or {}

    today = reference_date or _to_date(plot.get("reference_date")) or _dt.date.today()
    month = today.month

    ndvi_current = sentinel.get("ndvi")
    ndvi_prev = sentinel.get("ndvi_previous_month")
    if ndvi_prev is None:
        ndvi_prev = sentinel.get("ndvi_prev_month")
    ndvi_drop = None
    if ndvi_current is not None and ndvi_prev is not None:
        try:
            ndvi_drop = float(ndvi_prev) - float(ndvi_current)
        except (TypeError, ValueError):
            ndvi_drop = None

    if month in (12, 1, 2) and ndvi_drop is not None and ndvi_drop > 0.10:
        return "FLOWERING"

    if phenology.get("fruit_set_detected"):
        return "FRUIT_SET"

    days_to_harvest = phenology.get("days_to_harvest")
    if days_to_harvest is None:
        days_to_harvest = harvest_window.get("days_to_harvest")
    if days_to_harvest is None:
        hw_start = _to_date(harvest_window.get("start"))
        if hw_start is not None:
            days_to_harvest = (hw_start - today).days
    if days_to_harvest is not None:
        try:
            if int(days_to_harvest) <= 21:
                return "HARVEST_READINESS"
        except (TypeError, ValueError):
            pass

    return "FRUIT_DEVELOPMENT"


def _apply_bearing_year_adjustment(predicted_yield_kg_per_tree, bearing_year):
    """Apply the ON/OFF/UNKNOWN multiplier from SDD section 2.2."""
    try:
        base = float(predicted_yield_kg_per_tree or 0.0)
    except (TypeError, ValueError):
        base = 0.0
    multiplier = _BEARING_YEAR_MULTIPLIERS.get(
        (bearing_year or "UNKNOWN").upper(),
        _BEARING_YEAR_MULTIPLIERS["UNKNOWN"],
    )
    return base * multiplier, multiplier


def _is_alphonso_export_season(harvest_date):
    """True if harvest_date falls in Apr 15 - May 15 (Alphonso export window)."""
    import datetime as _dt

    d = _to_date(harvest_date)
    if d is None:
        return False
    year = d.year
    return _dt.date(year, 4, 15) <= d <= _dt.date(year, 5, 15)


def _mango_alerts(weather, phenology, reference_date=None):
    """Build the mango-specific alerts list (frost + heat-stress)."""
    import datetime as _dt

    alerts = []
    today = reference_date or _dt.date.today()
    month = today.month

    weather = weather or {}
    phenology = phenology or {}

    min_temp = weather.get("min_temp")
    if min_temp is None:
        forecast = weather.get("forecast_7day") or weather.get("forecast") or []
        if isinstance(forecast, list):
            mins = []
            for day in forecast:
                if isinstance(day, Mapping):
                    t = day.get("temp_min", day.get("min"))
                    if t is not None:
                        try:
                            mins.append(float(t))
                        except (TypeError, ValueError):
                            pass
            if mins:
                min_temp = min(mins)

    if min_temp is not None and month in (12, 1, 2):
        try:
            if float(min_temp) < 8.0:
                alerts.append({
                    "code": "FROST_RISK",
                    "severity": "high",
                    "message": (
                        f"Frost risk: min temp {float(min_temp):.1f}C in month {month}. "
                        "Cover panicles, light smudge fires before dawn."
                    ),
                })
        except (TypeError, ValueError):
            pass

    heat_events = phenology.get("heat_stress_events_count")
    try:
        if heat_events is not None and int(heat_events) > 0:
            alerts.append({
                "code": "HEAT_STRESS_RISK",
                "severity": "medium",
                "message": (
                    f"Heat stress detected: {int(heat_events)} events this season. "
                    "Increase irrigation frequency and consider kaolin spray."
                ),
            })
    except (TypeError, ValueError):
        pass

    return alerts


def _resolve_phenology(plot, phenology):
    """Return phenology dict, lazy-importing the mango_phenology helper.

    The Mango Agent 4 module may not be available yet. If it is and the
    caller did not pass phenology, attempt to derive it.
    """
    if phenology:
        return dict(phenology)
    try:
        from pipelines import mango_phenology  # type: ignore  # noqa: F401
    except Exception:
        return {}
    try:
        derive = getattr(mango_phenology, "derive_phenology", None)
        if callable(derive):
            result = derive(plot or {})
            if isinstance(result, Mapping):
                return dict(result)
    except Exception:
        return {}
    return {}


def build_mango_claude_prompt(
    plot,
    amed,
    sentinel,
    weather,
    phenology,
    variety_config,
    market,
    *,
    advisory_type,
    bearing_year,
    bearing_confidence,
    predicted_yield_kg_per_tree,
    predicted_yield_total,
    harvest_window,
    revenue,
    alerts,
):
    """Compose the mango Claude prompt per SDD section 8.3."""
    plot = plot or {}
    amed = amed or {}
    sentinel = sentinel or {}
    weather = weather or {}
    phenology = phenology or {}
    market = market or {}

    variety = (
        plot.get("current_crop_variety")
        or variety_config.get("variety_name")
        or "default"
    )
    region = plot.get("crop_region") or plot.get("region") or amed.get("region") or "n/a"
    language_hint = variety_config.get("language_hint", "Marathi")

    # Variety block
    harvest_months = variety_config.get("harvest_months", [])
    brix_min = variety_config.get("brix_target_min", 0)
    brix_max = variety_config.get("brix_target_max", 0)
    weight_target = variety_config.get("weight_target_grams", 0)
    mandi_primary = variety_config.get("mandi_primary", "n/a")
    mandi_secondary = variety_config.get("mandi_secondary", "n/a")
    price_premium = variety_config.get("price_premium_pct", 0)
    spray_schedule = variety_config.get("spray_schedule", "standard_mango_generic")
    gi_tagged = variety_config.get("gi_tagged", False)
    export_quality = variety_config.get("export_quality", False)

    # AMED block
    amed_field_size = amed.get("field_size_acres") or plot.get("area_acres")
    tree_count = plot.get("tree_count") or amed.get("tree_count") or 0
    tree_age = plot.get("tree_age_years") or amed.get("tree_age_years")
    amed_harvest = amed.get("harvest_date_predicted")

    # Phenology block
    flowering_detected = phenology.get("flowering_detected", False)
    flowering_start = phenology.get("flowering_start_date") or phenology.get("flowering_start")
    flowering_peak = phenology.get("flowering_peak_date")
    flowering_intensity = phenology.get("flowering_intensity_pct")
    fruit_set_detected = phenology.get("fruit_set_detected", False)
    fruit_set_date = phenology.get("fruit_set_date")
    fruit_set_pct = phenology.get("fruit_set_pct")
    frost_events = phenology.get("frost_events_count", 0)
    heat_events = phenology.get("heat_stress_events_count", 0)
    rain_during_flowering = phenology.get("rain_during_flowering_mm", 0)

    # Sentinel
    ndvi = sentinel.get("ndvi")
    reci = sentinel.get("reci")
    ndwi = sentinel.get("ndwi")
    lai = sentinel.get("lai")
    prithvi_phenology = sentinel.get("prithvi_phenology")
    prithvi_stress = sentinel.get("prithvi_stress")

    # Market
    current_price = market.get("current_price")
    predictions = market.get("predictions") or []
    pred_day1 = predictions[0] if len(predictions) >= 1 else None
    pred_day2 = predictions[1] if len(predictions) >= 2 else None
    pred_day3 = predictions[2] if len(predictions) >= 3 else None
    model_confidence = market.get("confidence") or market.get("model_confidence")
    export_premium_applied = revenue.get("export_premium_applied", False)

    weather_block = _format_weather(weather)

    # Harvest window
    hw_start = (harvest_window or {}).get("start", "n/a")
    hw_end = (harvest_window or {}).get("end", "n/a")
    hw_conf = (harvest_window or {}).get("confidence")

    alerts_block = "\n      ".join(
        [f"- {a.get('code')}: {a.get('message')}" for a in alerts]
    ) if alerts else "none"

    harvest_months_str = ", ".join(str(m) for m in harvest_months) if harvest_months else "n/a"

    prompt = (
        "You are an expert mango agronomist for the Indian mango belt.\n"
        f"Advisory type: {advisory_type}\n"
        f"Respond in language: {language_hint}\n"
        "\n"
        "VARIETY DETAILS:\n"
        f"  Variety: {variety}\n"
        f"  Region: {region}\n"
        f"  Harvest months: {harvest_months_str}\n"
        f"  Brix target: {brix_min}-{brix_max}\n"
        f"  Weight target: {weight_target} g\n"
        f"  GI tagged: {gi_tagged} | Export quality: {export_quality}\n"
        f"  Primary mandi: {mandi_primary} | Secondary: {mandi_secondary}\n"
        f"  Price premium vs standard: +{price_premium}%\n"
        f"  Spray schedule template: {spray_schedule}\n"
        "\n"
        "FARM DATA (AMED confirmed):\n"
        f"  Field size: {_fmt(amed_field_size)} acres\n"
        f"  Tree count: {_fmt(tree_count)}\n"
        f"  Tree age: {_fmt(tree_age)} years\n"
        f"  Bearing year: {bearing_year} (confidence {_fmt(bearing_confidence)})\n"
        f"  Predicted harvest date: {_fmt(amed_harvest)}\n"
        "\n"
        "PHENOLOGY THIS SEASON:\n"
        f"  Flowering detected: {flowering_detected}\n"
        f"  Flowering start: {_fmt(flowering_start)} | Peak: {_fmt(flowering_peak)}\n"
        f"  Flowering intensity: {_fmt(flowering_intensity)}%\n"
        f"  Fruit set detected: {fruit_set_detected} on {_fmt(fruit_set_date)}\n"
        f"  Fruit set: {_fmt(fruit_set_pct)}%\n"
        f"  Frost events: {_fmt(frost_events)} | Heat stress events: {_fmt(heat_events)}\n"
        f"  Rain during flowering: {_fmt(rain_during_flowering)} mm\n"
        "\n"
        "SATELLITE HEALTH (Sentinel-2):\n"
        f"  NDVI: {_fmt(ndvi)} | RECI: {_fmt(reci)} | NDWI: {_fmt(ndwi)} | LAI: {_fmt(lai)}\n"
        f"  Prithvi phenology: {_fmt(prithvi_phenology)}\n"
        f"  Prithvi stress: {_fmt(prithvi_stress)}\n"
        "\n"
        "WEATHER FORECAST (Open-Meteo):\n"
        f"      {weather_block}\n"
        "\n"
        "MARKET (ARIMA + AMED + forex):\n"
        f"  Current price: Rs {_fmt(current_price)}/kg\n"
        f"  3-day prediction: Rs {_fmt(pred_day1)}, Rs {_fmt(pred_day2)}, Rs {_fmt(pred_day3)}\n"
        f"  Model confidence: {_fmt(model_confidence)}\n"
        f"  Export premium applied: {export_premium_applied}\n"
        "\n"
        "HARVEST WINDOW:\n"
        f"  Start: {_fmt(hw_start)} | End: {_fmt(hw_end)} | Confidence: {_fmt(hw_conf)}\n"
        "\n"
        "YIELD ESTIMATE:\n"
        f"  Per tree: {_fmt(predicted_yield_kg_per_tree)} kg\n"
        f"  Total: {_fmt(predicted_yield_total)} kg\n"
        "\n"
        "ALERTS:\n"
        f"      {alerts_block}\n"
        "\n"
        "Provide variety-specific mango advice. Answer the following 6 questions:\n"
        "1. Spray advisory (SPRAY NOW / DELAY / AVOID + reason for this variety)\n"
        "2. Harvest readiness (Brix + weight + days_to_harvest assessment)\n"
        "3. Bearing year impact (yield expectation vs ON-year potential)\n"
        "4. Mandi routing (primary vs secondary mandi for this lot)\n"
        "5. Market timing (sell now / hold X days, factoring export demand)\n"
        "6. One critical alert (frost / heat / disease / market risk)\n"
        "\n"
        f"Respond in {language_hint} for the farmer."
    )
    return prompt


def _mango_stub_narrative(
    *,
    variety,
    region,
    advisory_type,
    bearing_year,
    predicted_yield_kg_per_tree,
    predicted_yield_total,
    revenue,
    harvest_window,
    alerts,
    variety_config,
):
    """Deterministic WhatsApp-shaped narrative when no llm_client is given."""
    hw_start = (harvest_window or {}).get("start", "n/a")
    hw_end = (harvest_window or {}).get("end", "n/a")
    spray_schedule = variety_config.get("spray_schedule", "standard_mango_generic")
    mandi_primary = variety_config.get("mandi_primary", "n/a")
    language_hint = variety_config.get("language_hint", "Marathi")
    price_premium = revenue.get("premium_pct", 0)
    revenue_inr = revenue.get("revenue_potential_inr", 0)

    alerts_lines = "\n".join(
        [f"  ! {a.get('code')}: {a.get('message')}" for a in alerts]
    ) if alerts else "  none"

    return (
        f"Mango advisory ({advisory_type}) for {variety} in {region}.\n"
        f"Bearing year: {bearing_year}.\n"
        f"Predicted yield: {predicted_yield_kg_per_tree:.1f} kg/tree "
        f"(total {predicted_yield_total:.1f} kg).\n"
        f"Harvest window: {hw_start} to {hw_end}.\n"
        f"Spray schedule: {spray_schedule}.\n"
        f"Target mandi: {mandi_primary}.\n"
        f"Premium: +{price_premium}% | Revenue potential: Rs {revenue_inr}.\n"
        f"Alerts:\n{alerts_lines}\n"
        f"Language hint: {language_hint}.\n"
        "This is a deterministic stub narrative - provide an llm_client "
        "for full Claude advisory."
    )


def generate_mango_advisory(
    plot,
    amed,
    sentinel,
    weather,
    phenology,
    variety_config,
    price_pred,
    market,
    *,
    llm_client=None,
    reference_date=None,
):
    """Generate a mango-specific advisory per SDD section 8.

    Returns a dict with variety, advisory_type, bearing_year, phenology
    flags, yield/revenue figures, harvest window, alerts, narrative, and
    the Claude prompt.
    """
    import datetime as _dt

    plot = plot or {}
    amed = amed or {}
    sentinel = sentinel or {}
    weather = weather or {}
    market = market or {}
    price_pred = price_pred or {}

    variety_config = dict(variety_config or {})
    phenology = _resolve_phenology(plot, phenology)

    variety = (
        plot.get("current_crop_variety")
        or variety_config.get("variety_name")
        or "default"
    )
    region = plot.get("crop_region") or plot.get("region") or amed.get("region") or "n/a"

    today = reference_date or _to_date(plot.get("reference_date")) or _dt.date.today()

    # Harvest window — combine inputs from market, plot, amed.
    harvest_window_in = (
        market.get("harvest_window")
        or plot.get("harvest_window")
        or {}
    )
    harvest_window = {
        "start": harvest_window_in.get("start") or amed.get("harvest_date_predicted"),
        "end": harvest_window_in.get("end"),
        "confidence": harvest_window_in.get("confidence"),
    }

    advisory_type = _detect_mango_advisory_type(
        plot, sentinel, phenology, harvest_window, reference_date=today
    )

    # Bearing year + confidence
    bearing_year = (
        plot.get("bearing_year")
        or phenology.get("bearing_year")
        or "UNKNOWN"
    )
    bearing_year = str(bearing_year).upper()
    if bearing_year not in _BEARING_YEAR_MULTIPLIERS:
        bearing_year = "UNKNOWN"
    bearing_confidence = (
        plot.get("bearing_confidence")
        or phenology.get("bearing_confidence")
    )

    # Yield prediction
    base_yield_per_tree = float(
        market.get("predicted_yield_kg_per_tree")
        or phenology.get("predicted_yield_kg_per_tree")
        or plot.get("predicted_yield_kg_per_tree")
        or 0.0
    )
    predicted_yield_kg_per_tree, _bmult = _apply_bearing_year_adjustment(
        base_yield_per_tree, bearing_year
    )
    tree_count = int(
        plot.get("tree_count")
        or amed.get("tree_count")
        or 0
    )
    predicted_yield_total = predicted_yield_kg_per_tree * tree_count

    # Revenue calculation
    base_price = float(
        market.get("current_price")
        or price_pred.get("current_price")
        or market.get("base_modal_price")
        or 0.0
    )
    is_premium = bool(variety_config.get("mandi_grade_premium", False))
    premium_pct = float(variety_config.get("price_premium_pct", 0) or 0)

    # Alphonso export window flag (Apr 15 - May 15)
    harvest_date_for_export = (
        harvest_window.get("start")
        or amed.get("harvest_date_predicted")
    )
    in_export_window = _is_alphonso_export_season(harvest_date_for_export)
    export_premium_applied = False
    export_premium_pct = 0.0
    if variety == "Alphonso" and in_export_window:
        export_premium_pct = 30.0
        export_premium_applied = True

    total_premium_pct = (premium_pct if is_premium else 0.0) + export_premium_pct
    expected_price = base_price * (1.0 + total_premium_pct / 100.0)
    revenue_inr = predicted_yield_total * expected_price

    revenue = {
        "base_price_kg": round(base_price, 2),
        "expected_price_kg": round(expected_price, 2),
        "premium_pct": round(total_premium_pct, 2),
        "is_premium_variety": is_premium,
        "export_premium_applied": export_premium_applied,
        "export_premium_pct": export_premium_pct,
        "revenue_potential_inr": round(revenue_inr, 2),
    }

    # Alerts
    alerts = _mango_alerts(weather, phenology, reference_date=today)

    # Phenology flags surfaced to the result
    flowering_detected = bool(phenology.get("flowering_detected", False))
    fruit_set_detected = bool(phenology.get("fruit_set_detected", False))
    phenology_stage = (
        phenology.get("stage")
        or phenology.get("phenology_stage")
        or advisory_type
    )

    prompt = build_mango_claude_prompt(
        plot=plot,
        amed=amed,
        sentinel=sentinel,
        weather=weather,
        phenology=phenology,
        variety_config=variety_config,
        market=market,
        advisory_type=advisory_type,
        bearing_year=bearing_year,
        bearing_confidence=bearing_confidence,
        predicted_yield_kg_per_tree=predicted_yield_kg_per_tree,
        predicted_yield_total=predicted_yield_total,
        harvest_window=harvest_window,
        revenue=revenue,
        alerts=alerts,
    )

    if llm_client is not None and hasattr(llm_client, "generate"):
        try:
            narrative = llm_client.generate(prompt)
            if not isinstance(narrative, str):
                narrative = str(narrative)
        except Exception as exc:  # noqa: BLE001
            narrative = (
                f"LLM generation failed: {exc.__class__.__name__}: {exc}\n"
                + _mango_stub_narrative(
                    variety=variety,
                    region=region,
                    advisory_type=advisory_type,
                    bearing_year=bearing_year,
                    predicted_yield_kg_per_tree=predicted_yield_kg_per_tree,
                    predicted_yield_total=predicted_yield_total,
                    revenue=revenue,
                    harvest_window=harvest_window,
                    alerts=alerts,
                    variety_config=variety_config,
                )
            )
    else:
        narrative = _mango_stub_narrative(
            variety=variety,
            region=region,
            advisory_type=advisory_type,
            bearing_year=bearing_year,
            predicted_yield_kg_per_tree=predicted_yield_kg_per_tree,
            predicted_yield_total=predicted_yield_total,
            revenue=revenue,
            harvest_window=harvest_window,
            alerts=alerts,
            variety_config=variety_config,
        )

    return {
        "variety": variety,
        "region": region,
        "advisory_type": advisory_type,
        "bearing_year": bearing_year,
        "bearing_confidence": bearing_confidence,
        "phenology_stage": phenology_stage,
        "flowering_detected": flowering_detected,
        "fruit_set_detected": fruit_set_detected,
        "predicted_yield_kg_per_tree": round(predicted_yield_kg_per_tree, 2),
        "predicted_yield_total": round(predicted_yield_total, 2),
        "revenue_potential_inr": revenue["revenue_potential_inr"],
        "is_premium_variety": revenue["is_premium_variety"],
        "premium_pct": revenue["premium_pct"],
        "export_premium_applied": revenue["export_premium_applied"],
        "harvest_window": dict(harvest_window),
        "spray_schedule": str(variety_config.get("spray_schedule", "")),
        "alerts": alerts,
        "narrative": narrative,
        "prompt": prompt,
    }


# --------------------------------------------------------------------------- #
# Crop-aware dispatcher
# --------------------------------------------------------------------------- #

def generate_advisory_for_plot(
    plot,
    amed,
    sentinel,
    weather,
    phenology,
    market_data,
    *,
    llm_client=None,
    config_path: str | os.PathLike[str] = "data/variety_config.json",
    reference_date=None,
):
    """Route to the correct crop-specific advisory builder.

    - Mango -> ``generate_mango_advisory``
    - Grapes / Pomegranate / other -> ``generate_advisory``
    """
    plot = plot or {}
    crop = plot.get("current_crop") or (amed or {}).get("crop_type")

    if crop == "Mango":
        variety = plot.get("current_crop_variety")
        variety_config = get_variety_config(crop, variety, config_path=config_path)
        price_pred = (market_data or {}).get("price_pred") or {}
        return generate_mango_advisory(
            plot=plot,
            amed=amed,
            sentinel=sentinel,
            weather=weather,
            phenology=phenology,
            variety_config=variety_config,
            price_pred=price_pred,
            market=market_data,
            llm_client=llm_client,
            reference_date=reference_date,
        )

    # Grapes / Pomegranate / unknown all use the generic engine.
    harvest_window = (market_data or {}).get("harvest_window")
    return generate_advisory(
        plot=plot,
        amed=amed,
        sentinel=sentinel,
        harvest_window=harvest_window,
        market=market_data,
        weather=weather,
        llm_client=llm_client,
        config_path=config_path,
    )


__all__ = [
    "GLOBAL_FALLBACK_VARIETY_CONFIG",
    "build_claude_prompt",
    "build_mango_claude_prompt",
    "calculate_revenue_potential",
    "estimate_brix_from_reci",
    "generate_advisory",
    "generate_advisory_for_plot",
    "generate_mango_advisory",
    "get_variety_config",
    "load_variety_config",
]
