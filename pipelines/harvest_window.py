"""Pure-logic helpers for harvest window and combined signal merging.

These functions have no side effects and depend only on stdlib. They are
exercised both by :mod:`pipelines.ndvi_pipeline` and by unit tests in
``tests/test_revised_pipeline.py``.

SDD references:
  * Section 6.1 — Harvest window prediction (3-branch NDVI logic)
  * Section 6.3 — Combined health summary used in advisory prompt
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping, Tuple


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _coerce_date(value: Any) -> date:
    """Accept ``date``, ``datetime`` or ``"YYYY-MM-DD"`` strings.

    Raises ``ValueError`` for anything else so callers see the bad input.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise ValueError(f"Unsupported date value: {value!r}")


def _ndvi_quality_factor(ndvi_30day_trend: Mapping[str, Any] | None) -> float:
    """Map the Sentinel-2 30-day trend payload to a 0..1 quality factor.

    The trend dict is expected to carry at least ``slope`` (NDVI per day,
    negative when declining) and optionally a ``cloud_cover_flag`` boolean.
    A missing or empty payload degrades quality but does not break the
    pipeline — confidence is clamped to a floor of 0.5.
    """
    if not ndvi_30day_trend:
        return 0.5

    factor = 1.0
    if ndvi_30day_trend.get("cloud_cover_flag"):
        # SDD 4.2: cloud-cover reuse penalises confidence by 15%.
        factor *= 0.85

    quality = ndvi_30day_trend.get("quality")
    if isinstance(quality, (int, float)):
        factor *= max(0.0, min(1.0, float(quality)))

    return max(0.5, min(1.0, factor))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_harvest_window(
    amed_harvest_date: Any,
    ndvi_30day_trend: Mapping[str, Any] | None,
    amed_confidence: float,
) -> Tuple[date, date, float]:
    """Refine the AMED harvest prediction using the Sentinel-2 NDVI trend.

    Implements the three-branch logic from SDD Section 6.1:

      * NDVI declining normally  -> [-3 days, +4 days] around AMED date.
      * NDVI declining faster than expected -> [-7 days, -1 day].
      * NDVI not yet declining   -> [AMED date, +10 days].

    Parameters
    ----------
    amed_harvest_date:
        The ``harvest_date_predicted`` from the AMED field response. Accepts
        a ``date`` / ``datetime`` instance or a ``"YYYY-MM-DD"`` string.
    ndvi_30day_trend:
        A mapping describing the 30-day NDVI trend. Recognised keys:
        ``slope`` (float, NDVI units per day — negative means declining),
        ``state`` (optional explicit state: ``"declining_normal"``,
        ``"declining_fast"``, ``"flat"``), ``quality`` (0..1) and
        ``cloud_cover_flag`` (bool).
    amed_confidence:
        Crop confidence reported by AMED (0..1).

    Returns
    -------
    tuple
        ``(window_start, window_end, harvest_confidence)``.
    """
    base = _coerce_date(amed_harvest_date)

    state = None
    if ndvi_30day_trend:
        state = ndvi_30day_trend.get("state")
        if state is None:
            slope = ndvi_30day_trend.get("slope")
            if isinstance(slope, (int, float)):
                # Heuristic thresholds. NDVI slope of -0.002/day or shallower
                # is treated as "not yet declining"; steeper than -0.006/day
                # counts as "declining faster than expected".
                if slope > -0.002:
                    state = "flat"
                elif slope < -0.006:
                    state = "declining_fast"
                else:
                    state = "declining_normal"

    # Fall back to the conservative AMED-only window if no trend is available.
    if state == "declining_fast":
        window_start = base - timedelta(days=7)
        window_end = base - timedelta(days=1)
    elif state in (None, "flat", "not_declining"):
        window_start = base
        window_end = base + timedelta(days=10)
    else:  # declining_normal (default informed branch)
        window_start = base - timedelta(days=3)
        window_end = base + timedelta(days=4)

    confidence = max(0.0, min(1.0, float(amed_confidence))) * _ndvi_quality_factor(
        ndvi_30day_trend
    )
    return window_start, window_end, round(confidence, 4)


def _category_from_score(score: float) -> str:
    if score >= 0.75:
        return "good"
    if score >= 0.55:
        return "moderate"
    if score >= 0.35:
        return "stressed"
    return "critical"


def merge_signals(
    amed_growth_stage: str | None,
    ndvi: float | None,
    reci: float | None,
    ndwi: float | None,
) -> dict:
    """Produce a combined-health summary used by the advisory engine.

    Weighting roughly follows the M6 prompt in SDD 6.3 where NDVI carries
    the most weight, RECI signals chlorophyll vigour and NDWI signals
    canopy water status. AMED growth stage shifts the floor — a known
    ``berry_development`` or ``ripening`` stage is given a small positive
    bias since AMED has already confirmed the plant is on a healthy path.

    All inputs are optional. Missing signals are simply not weighted in.
    """
    contributions: list[tuple[float, float]] = []  # (weight, normalised_value)

    if isinstance(ndvi, (int, float)):
        # NDVI maps roughly 0.2..0.85 to bad..great.
        norm = max(0.0, min(1.0, (float(ndvi) - 0.2) / 0.65))
        contributions.append((0.5, norm))

    if isinstance(reci, (int, float)):
        # RECI typically ranges 0..4 for healthy canopies.
        norm = max(0.0, min(1.0, float(reci) / 4.0))
        contributions.append((0.25, norm))

    if isinstance(ndwi, (int, float)):
        # NDWI roughly -0.3..0.6 -> 0..1.
        norm = max(0.0, min(1.0, (float(ndwi) + 0.3) / 0.9))
        contributions.append((0.25, norm))

    if contributions:
        total_w = sum(w for w, _ in contributions)
        score = sum(w * v for w, v in contributions) / total_w
    else:
        # No Sentinel-2 signals at all — fall back to neutral 0.5.
        score = 0.5

    # Small bias from AMED growth stage, capped at +/- 0.05.
    bias = 0.0
    if amed_growth_stage:
        stage = amed_growth_stage.lower()
        if stage in {"berry_development", "ripening", "flowering"}:
            bias = 0.05
        elif stage in {"sowing", "germination"}:
            bias = -0.05
    score = max(0.0, min(1.0, score + bias))

    return {
        "score": round(score, 4),
        "category": _category_from_score(score),
        "ndvi": ndvi,
        "reci": reci,
        "ndwi": ndwi,
        "amed_growth_stage": amed_growth_stage,
    }


__all__ = ["calculate_harvest_window", "merge_signals"]
