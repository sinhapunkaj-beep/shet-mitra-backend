"""Mango phenology detection — pure-logic helpers per SDD §2.2 and §2.3.

This module is deliberately self-contained: stdlib + ``datetime`` only.
It is consumed by :mod:`pipelines.ndvi_pipeline` (Step 4b mango branch)
and by Agent 1's persistence layer for ``mango_phenology_log``.

All thresholds come directly from the mango SDD:

  * §2.2 — Alternate bearing:
        Aug-Sep NDVI > 0.75   → 'OFF' (high canopy density = recovered tree)
        Aug-Sep NDVI 0.60-0.75 → 'UNKNOWN' (neutral)
        Aug-Sep NDVI < 0.60   → 'ON'  (exhausted after heavy fruiting)

  * §2.3 — Flowering / fruit-set / heat / water thresholds:
        Flowering window: months 12 / 1 / 2 with 0.10-0.20 NDVI drop
        Fruit set window: months 2 / 3 with partial NDVI recovery + RECI rise
        Heat stress: temp > 38.0 C, default in Feb-Mar
        Drought:    NDWI < -0.20
        Waterlog:   NDWI > 0.40
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

__all__ = [
    "detect_bearing_year",
    "detect_flowering",
    "detect_fruit_set",
    "detect_heat_stress",
    "assess_water_status",
    "adjust_health_during_flowering",
    "bearing_year_yield_multiplier",
]


# ---------------------------------------------------------------------------
# §2.2 Alternate bearing
# ---------------------------------------------------------------------------

# Bearing-year NDVI thresholds (Aug-Sep mean canopy reading).
_BEARING_OFF_NDVI = 0.75
_BEARING_ON_NDVI = 0.60

# Bearing-year yield multipliers — ON year is the heavy crop reference (1.0),
# OFF year is the alternate-bearing drop, UNKNOWN is the conservative middle.
_YIELD_MULTIPLIERS = {
    "ON": 1.0,
    "OFF": 0.45,
    "UNKNOWN": 0.75,
}


def detect_bearing_year(
    aug_sep_ndvi_mean: float,
    *,
    history: list[float] | None = None,
) -> tuple[str, float]:
    """Classify alternate-bearing state from the Aug-Sep NDVI mean.

    Returns ``(bearing_year, confidence)`` where ``bearing_year`` is one
    of ``'ON'``, ``'OFF'`` or ``'UNKNOWN'``.

    Confidence rules (SDD §2.2):
      * Clearly above OFF threshold (>= 0.78) or clearly below ON
        threshold (<= 0.55) → 0.90 confidence.
      * Within 0.03 of either threshold → 0.65 (borderline).
      * Neutral band (0.60-0.75) → 0.50 baseline 'UNKNOWN'.

    When ``history`` (previous-year NDVI means) is supplied and the
    current value is *opposite* the most recent value, confidence is
    nudged up by 0.05 because alternate-bearing is the expected pattern.
    """
    if aug_sep_ndvi_mean is None:
        return "UNKNOWN", 0.0

    try:
        ndvi = float(aug_sep_ndvi_mean)
    except (TypeError, ValueError):
        return "UNKNOWN", 0.0

    if ndvi > _BEARING_OFF_NDVI:
        bearing = "OFF"
        confidence = 0.90 if ndvi >= 0.78 else 0.65
    elif ndvi < _BEARING_ON_NDVI:
        bearing = "ON"
        confidence = 0.90 if ndvi <= 0.55 else 0.65
    else:
        bearing = "UNKNOWN"
        confidence = 0.50

    # Alternate-bearing reinforcement: if the previous season was the
    # opposite phase, bump confidence a touch.
    if history:
        try:
            prev_ndvi = float(history[-1])
        except (TypeError, ValueError, IndexError):
            prev_ndvi = None
        if prev_ndvi is not None:
            prev_bearing, _ = detect_bearing_year(prev_ndvi)
            if {prev_bearing, bearing} == {"ON", "OFF"}:
                confidence = min(0.99, round(confidence + 0.05, 2))

    return bearing, round(confidence, 2)


def bearing_year_yield_multiplier(bearing_year: str) -> float:
    """Yield multiplier for the given bearing-year state (SDD §2.2).

    Unknown labels fall back to the 'UNKNOWN' multiplier (0.75) so the
    yield model is conservative rather than over-optimistic.
    """
    if not bearing_year:
        return _YIELD_MULTIPLIERS["UNKNOWN"]
    return _YIELD_MULTIPLIERS.get(str(bearing_year).upper(), _YIELD_MULTIPLIERS["UNKNOWN"])


# ---------------------------------------------------------------------------
# §2.3 Flowering detection
# ---------------------------------------------------------------------------

_FLOWERING_MONTHS = (12, 1, 2)
_FLOWERING_DROP_MIN = 0.10
_FLOWERING_DROP_MAX = 0.20


def _sorted_monthly(
    monthly_ndvi: Iterable[tuple[date, float]],
) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    for item in monthly_ndvi or []:
        try:
            d, v = item
        except (TypeError, ValueError):
            continue
        if d is None or v is None:
            continue
        try:
            rows.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def detect_flowering(
    monthly_ndvi: list[tuple[date, float]],
    *,
    current_month: int,
) -> tuple[bool, date | None]:
    """Detect a flowering-induced NDVI drop.

    Per SDD §2.3, mango flowering produces a 0.10-0.20 NDVI dip in
    Dec-Feb as the canopy redirects energy into panicle production.

    Returns ``(flowering_detected, peak_date)`` — ``peak_date`` is the
    date of the lowest NDVI sample in the qualifying window.
    """
    if current_month not in _FLOWERING_MONTHS:
        return False, None

    rows = _sorted_monthly(monthly_ndvi)
    if len(rows) < 2:
        return False, None

    # Compare each in-window sample to the most recent prior reading.
    best_drop = 0.0
    best_date: date | None = None
    for i in range(1, len(rows)):
        d, v = rows[i]
        if d.month not in _FLOWERING_MONTHS:
            continue
        prev_v = rows[i - 1][1]
        drop = prev_v - v
        if _FLOWERING_DROP_MIN <= drop <= _FLOWERING_DROP_MAX and drop > best_drop:
            best_drop = drop
            best_date = d

    if best_date is None:
        return False, None
    return True, best_date


# ---------------------------------------------------------------------------
# §2.3 Fruit set detection
# ---------------------------------------------------------------------------

_FRUIT_SET_MONTHS = (2, 3)
_FRUIT_SET_RECOVERY_MIN = 0.03
_FRUIT_SET_RECI_TREND_MIN = 0.05


def detect_fruit_set(
    monthly_ndvi: list[tuple[date, float]],
    reci_trend: float,
) -> tuple[bool, date | None]:
    """Detect fruit set after flowering (SDD §2.3).

    Fruit set is signalled by:
      * A partial NDVI recovery (canopy rebuilds chlorophyll after the
        flowering dip), AND
      * A rising RECI (chlorophyll proxy) trend.

    Returns ``(fruit_set_detected, fruit_set_date)``.
    """
    rows = _sorted_monthly(monthly_ndvi)
    if len(rows) < 2:
        return False, None

    try:
        reci = float(reci_trend) if reci_trend is not None else 0.0
    except (TypeError, ValueError):
        reci = 0.0

    if reci < _FRUIT_SET_RECI_TREND_MIN:
        return False, None

    best_recovery = 0.0
    best_date: date | None = None
    for i in range(1, len(rows)):
        d, v = rows[i]
        if d.month not in _FRUIT_SET_MONTHS:
            continue
        prev_v = rows[i - 1][1]
        recovery = v - prev_v
        if recovery >= _FRUIT_SET_RECOVERY_MIN and recovery > best_recovery:
            best_recovery = recovery
            best_date = d

    if best_date is None:
        return False, None
    return True, best_date


# ---------------------------------------------------------------------------
# §2.3 Heat stress
# ---------------------------------------------------------------------------

def detect_heat_stress(
    temps: list[tuple[date, float]],
    *,
    threshold_c: float = 38.0,
    in_months: tuple[int, ...] = (2, 3),
) -> int:
    """Count heat-stress events (temp above ``threshold_c``).

    Restricted to ``in_months`` because the mango stress window per
    SDD §2.3 is February-March (flowering through fruit set). Each
    qualifying date is counted once; duplicates on the same date are
    de-duplicated to avoid double counting.
    """
    if not temps:
        return 0
    seen: set[date] = set()
    months = set(in_months) if in_months else None
    for item in temps:
        try:
            d, t = item
        except (TypeError, ValueError):
            continue
        if d is None or t is None:
            continue
        try:
            temp_c = float(t)
        except (TypeError, ValueError):
            continue
        if months is not None and d.month not in months:
            continue
        if temp_c > float(threshold_c):
            seen.add(d)
    return len(seen)


# ---------------------------------------------------------------------------
# §2.3 Water status (NDWI)
# ---------------------------------------------------------------------------

_NDWI_DROUGHT = -0.20
_NDWI_WATERLOG = 0.40


def assess_water_status(ndwi: float) -> str:
    """Classify field water status from NDWI.

    Returns ``'drought'`` (< -0.20), ``'waterlogged'`` (> 0.40) or
    ``'normal'`` per SDD §2.3.
    """
    if ndwi is None:
        return "normal"
    try:
        val = float(ndwi)
    except (TypeError, ValueError):
        return "normal"
    if val < _NDWI_DROUGHT:
        return "drought"
    if val > _NDWI_WATERLOG:
        return "waterlogged"
    return "normal"


# ---------------------------------------------------------------------------
# Flowering-aware stress suppression
# ---------------------------------------------------------------------------

def adjust_health_during_flowering(ndvi_drop: float, month: int) -> bool:
    """Should this NDVI drop be EXCLUDED from stress alerting?

    Returns ``True`` when the observed drop is within the expected
    flowering range (0.10-0.20) AND the calendar month is part of the
    flowering window (Dec-Feb). In that case the alerting layer should
    treat the drop as normal phenology and *not* fire a stress alert.

    Drops outside this range, or in non-flowering months, are NOT
    suppressed — caller continues with the normal stress workflow.
    """
    try:
        drop = float(ndvi_drop)
    except (TypeError, ValueError):
        return False
    if month not in _FLOWERING_MONTHS:
        return False
    return _FLOWERING_DROP_MIN <= drop <= _FLOWERING_DROP_MAX
