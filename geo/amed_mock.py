"""AMED mock data generator.

Produces deterministic synthetic responses that match the schemas in
SDD Section 2.1 (field-level), 2.2 (belt-level) and 2.3 (historical).

The data is shaped for the Tasgaon / Sangli belt and supports both
Grapes (rabi season — sow Dec, harvest Apr-May) and Pomegranate
(bahar season — sow varies, harvest Aug-Feb).

All randomness is seeded by plot_id (or a fixed master seed of 42)
so repeated calls return identical payloads — important for snapshot
tests and for downstream caching layers.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta
from typing import Any

# Master seed used wherever a plot_id is not supplied.
_MASTER_SEED = 42

# Tasgaon / Sangli belt bounding box (default — SDD Section 2.2).
_BELT_BBOX_DEFAULT = {
    "north": 17.2,
    "south": 16.8,
    "east": 74.8,
    "west": 74.3,
}

# Realistic Sangli-belt varieties (used as crop_variety_hint).
_GRAPE_VARIETIES = [
    "Thompson Seedless",
    "Sonaka",
    "Sharad Seedless",
    "Tas-A-Ganesh",
    "Manik Chaman",
]

_POMEGRANATE_VARIETIES = [
    "Bhagwa",
    "Ganesh",
    "Arakta",
    "Mridula",
]

_GROWTH_STAGES_GRAPES = [
    "bud_break",
    "flowering",
    "berry_set",
    "berry_development",
    "veraison",
    "ripening",
]

_GROWTH_STAGES_POMEGRANATE = [
    "vegetative",
    "flowering",
    "fruit_set",
    "fruit_development",
    "ripening",
]

_LAST_EVENTS = [
    "irrigation",
    "spray",
    "pruning",
    "thinning",
    "fertigation",
]

# Data refresh anchor — SDD example uses 2026-04-06.
_DATA_REFRESH = "2026-04-06"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_from_plot(plot_id: str | None) -> int:
    """Map a plot_id string to a stable integer seed."""
    if plot_id is None:
        return _MASTER_SEED
    digest = hashlib.sha256(plot_id.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _rng(plot_id: str | None) -> random.Random:
    return random.Random(_seed_from_plot(plot_id))


def _iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _round(value: float, digits: int = 2) -> float:
    return round(value, digits)


# ---------------------------------------------------------------------------
# Public API — field-level
# ---------------------------------------------------------------------------

def get_field_data(
    polygon: list[tuple[float, float]] | None = None,
    centroid: tuple[float, float] | None = None,
    area_acres: float | None = None,
    plot_id: str | None = None,
    crop_season: str = "rabi_2025_26",
) -> dict[str, Any]:
    """Return a mock AMED field-level response.

    The shape matches SDD Section 2.1. All output is deterministic when
    plot_id is supplied.
    """
    rng = _rng(plot_id)

    # Decide crop type. The SDD spread is ~80 grapes / ~22 pomegranate
    # across the seeded 102 farmers — about 78% grapes.
    if crop_season.startswith("rabi"):
        crop_type = "Grapes" if rng.random() < 0.78 else "Pomegranate"
    else:
        crop_type = "Pomegranate"

    if crop_type == "Grapes":
        variety = rng.choice(_GRAPE_VARIETIES)
        stages = _GROWTH_STAGES_GRAPES
        # Harvest window: 15 Apr -- 10 May 2026 (Sangli belt rabi grapes).
        harvest_day_offset = rng.randint(0, 25)
        harvest_date = date(2026, 4, 15) + timedelta(days=harvest_day_offset)
    else:
        variety = rng.choice(_POMEGRANATE_VARIETIES)
        stages = _GROWTH_STAGES_POMEGRANATE
        # Pomegranate: harvest Aug -- Feb. Pick a month uniformly across that span.
        # Map 0..6 -> Aug 2025, Sep, Oct, Nov, Dec, Jan 2026, Feb 2026.
        bahar_months = [
            (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
            (2026, 1), (2026, 2),
        ]
        y, m = rng.choice(bahar_months)
        day = rng.randint(1, 25)
        harvest_date = date(y, m, day)

    # Sow date 120 -- 150 days before harvest (per spec).
    sow_gap_days = rng.randint(120, 150)
    sowing_date = harvest_date - timedelta(days=sow_gap_days)

    # Area — keep it in [1.5, 6.5] acres unless caller supplied a number.
    if area_acres is None:
        field_size = _round(rng.uniform(1.5, 6.5), 1)
    else:
        field_size = _round(float(area_acres), 1)

    confidence = _round(rng.uniform(0.85, 0.95), 2)
    growth_stage = rng.choice(stages)
    growth_conf = _round(rng.uniform(0.85, 0.95), 2)
    irrigation_detected = rng.random() < 0.85
    last_event = rng.choice(_LAST_EVENTS)

    # Pick a last_event_date within the 14 days before data_refresh.
    refresh = datetime.strptime(_DATA_REFRESH, "%Y-%m-%d").date()
    last_event_date = refresh - timedelta(days=rng.randint(1, 14))

    # 3-year history — keep crop_type consistent (per spec).
    history = []
    base_harvest_years = [2025, 2024, 2023] if crop_type == "Grapes" else [2025, 2024, 2023]
    for yr in base_harvest_years:
        if crop_type == "Grapes":
            h_offset = rng.randint(0, 25)
            h_date = date(yr, 4, 15) + timedelta(days=h_offset)
        else:
            # Pomegranate history — mirror harvest months across years.
            hist_months = [
                (yr - 1, 8), (yr - 1, 9), (yr - 1, 10), (yr - 1, 11),
                (yr - 1, 12), (yr, 1), (yr, 2),
            ]
            y2, m2 = rng.choice(hist_months)
            d2 = rng.randint(1, 25)
            h_date = date(y2, m2, d2)
        s_date = h_date - timedelta(days=rng.randint(120, 150))
        # Acreage drifts a little year to year, capped at ±0.2 acres.
        hist_area = max(0.5, _round(field_size + rng.uniform(-0.2, 0.0), 1))
        season_label = f"{yr - 1}-{str(yr)[2:]}"
        history.append({
            "season": season_label,
            "crop_type": crop_type,
            "sowing_date": _iso(s_date),
            "harvest_date": _iso(h_date),
            "area_acres": hist_area,
        })

    field_id_seed = _seed_from_plot(plot_id) & 0xFFFFF
    field_id = f"amed_field_{field_id_seed:05x}"

    response = {
        "field_id": field_id,
        "crop_type": crop_type,
        "crop_variety_hint": variety,
        "crop_confidence": confidence,
        "field_size_acres": field_size,
        "sowing_date": _iso(sowing_date),
        "harvest_date_predicted": _iso(harvest_date),
        "growth_stage": growth_stage,
        "growth_stage_confidence": growth_conf,
        "irrigation_detected": irrigation_detected,
        "last_event": last_event,
        "last_event_date": _iso(last_event_date),
        "history": history,
        "data_refresh_date": _DATA_REFRESH,
        "source": "AMED",
    }
    # Echo back the request inputs for traceability (non-breaking extras).
    if polygon is not None:
        response["_request_polygon_points"] = len(polygon)
    if centroid is not None:
        response["_request_centroid"] = list(centroid)
    response["_request_crop_season"] = crop_season
    return response


# ---------------------------------------------------------------------------
# Public API — belt-level
# ---------------------------------------------------------------------------

# Fixed harvest forecast bell curve — values pulled straight from the SDD
# / Agent 1 brief so downstream tests can rely on exact numbers.
_GRAPE_HARVEST_FORECAST = [
    {
        "week_start": "2026-04-07",
        "week_end": "2026-04-13",
        "fields_harvesting": 234,
        "estimated_volume_mt": 340,
    },
    {
        "week_start": "2026-04-14",
        "week_end": "2026-04-20",
        "fields_harvesting": 467,
        "estimated_volume_mt": 680,
    },
    {
        "week_start": "2026-04-21",
        "week_end": "2026-04-27",
        "fields_harvesting": 312,
        "estimated_volume_mt": 450,
    },
    {
        "week_start": "2026-04-28",
        "week_end": "2026-05-04",
        "fields_harvesting": 145,
        "estimated_volume_mt": 210,
    },
]


def get_belt_data(
    bbox: dict[str, float],
    crop_type: str,
    season: str = "rabi_2025_26",
) -> dict[str, Any]:
    """Return a mock AMED belt-level response (SDD Section 2.2).

    The Grapes-Tasgaon answer uses the exact numbers from the SDD so that
    downstream assertions are stable. Pomegranate / other-crop calls get a
    proportional synthetic variant.
    """
    bbox = bbox or _BELT_BBOX_DEFAULT

    if crop_type == "Grapes":
        total_fields = 2847
        total_area = 8234
        forecast = [dict(week) for week in _GRAPE_HARVEST_FORECAST]
    elif crop_type == "Pomegranate":
        total_fields = 1342
        total_area = 4218
        # Pomegranate peak in Tasgaon belt — model a similar 4-week curve
        # centred on early February 2026.
        forecast = [
            {"week_start": "2026-01-26", "week_end": "2026-02-01",
             "fields_harvesting": 110, "estimated_volume_mt": 165},
            {"week_start": "2026-02-02", "week_end": "2026-02-08",
             "fields_harvesting": 230, "estimated_volume_mt": 340},
            {"week_start": "2026-02-09", "week_end": "2026-02-15",
             "fields_harvesting": 160, "estimated_volume_mt": 235},
            {"week_start": "2026-02-16", "week_end": "2026-02-22",
             "fields_harvesting": 75, "estimated_volume_mt": 110},
        ]
    else:
        total_fields = 0
        total_area = 0
        forecast = []

    return {
        "region": "Tasgaon_Sangli_belt",
        "crop_type": crop_type,
        "season": season,
        "bbox": bbox,
        "total_fields_detected": total_fields,
        "total_area_acres": total_area,
        "harvest_forecast": forecast,
        "health_distribution": {
            "good": 0.63,
            "moderate": 0.24,
            "stressed": 0.10,
            "critical": 0.03,
        },
        "data_refresh_date": _DATA_REFRESH,
        "source": "AMED",
    }


# ---------------------------------------------------------------------------
# Public API — historical
# ---------------------------------------------------------------------------

# Three seasons of belt history. Values come straight from the Agent 2 /
# Agent 4 brief in the SDD so that the price-history backfill is reproducible.
_HISTORY_DEFAULT = {
    "Grapes": [
        {
            "season": "2022-23",
            "total_area_acres": 7890,
            "peak_harvest_week": "2023-04-17/2023-04-23",
            "estimated_total_volume_mt": 11420,
            "avg_harvest_date": "2023-04-21",
        },
        {
            "season": "2023-24",
            "total_area_acres": 8012,
            "peak_harvest_week": "2024-04-15/2024-04-21",
            "estimated_total_volume_mt": 11680,
            "avg_harvest_date": "2024-04-19",
        },
        {
            "season": "2024-25",
            "total_area_acres": 8180,
            "peak_harvest_week": "2025-04-14/2025-04-20",
            "estimated_total_volume_mt": 11890,
            "avg_harvest_date": "2025-04-14",
        },
    ],
    "Pomegranate": [
        {
            "season": "2022-23",
            "total_area_acres": 4050,
            "peak_harvest_week": "2023-02-06/2023-02-12",
            "estimated_total_volume_mt": 5980,
            "avg_harvest_date": "2023-02-08",
        },
        {
            "season": "2023-24",
            "total_area_acres": 4140,
            "peak_harvest_week": "2024-02-05/2024-02-11",
            "estimated_total_volume_mt": 6120,
            "avg_harvest_date": "2024-02-07",
        },
        {
            "season": "2024-25",
            "total_area_acres": 4218,
            "peak_harvest_week": "2025-02-03/2025-02-09",
            "estimated_total_volume_mt": 6240,
            "avg_harvest_date": "2025-02-05",
        },
    ],
}


# ---------------------------------------------------------------------------
# Public API — mango belt-level (SDD §10 Agent 4)
# ---------------------------------------------------------------------------

# Mango bbox configs — Konkan (Alphonso), Nashik (Kesar), Vidarbha (Dasheri).
# Values must match the AMED_BBOX_* env vars appended by Agent 4 to .env.
_MANGO_BBOXES: dict[str, dict[str, float]] = {
    "Konkan": {"north": 18.0, "south": 15.5, "east": 74.0, "west": 72.8},
    "Nashik": {"north": 21.0, "south": 19.0, "east": 75.5, "west": 73.5},
    "Vidarbha": {"north": 22.0, "south": 19.5, "east": 80.5, "west": 77.0},
}

# Region + variety pairings. Each entry is the SDD §10 deterministic
# fingerprint used by belt-level downstream consumers.
_MANGO_BELT_PROFILES: dict[tuple[str, str], dict[str, Any]] = {
    ("Konkan", "Alphonso"): {
        "total_fields_detected": 1240,
        "total_area_acres": 4820,
        "peak_harvest_date": date(2026, 4, 22),
        "estimated_total_volume_mt": 540,
        "health_distribution": {
            "good": 0.66, "moderate": 0.22, "stressed": 0.09, "critical": 0.03,
        },
        "flowering_pct": 0.18,
        "fruit_set_pct": 0.72,
    },
    ("Nashik", "Kesar"): {
        "total_fields_detected": 2860,
        "total_area_acres": 8120,
        "peak_harvest_date": date(2026, 5, 18),
        "estimated_total_volume_mt": 980,
        "health_distribution": {
            "good": 0.61, "moderate": 0.25, "stressed": 0.11, "critical": 0.03,
        },
        "flowering_pct": 0.12,
        "fruit_set_pct": 0.78,
    },
    ("Vidarbha", "Dasheri"): {
        "total_fields_detected": 1410,
        "total_area_acres": 3940,
        "peak_harvest_date": date(2026, 6, 24),
        "estimated_total_volume_mt": 420,
        "health_distribution": {
            "good": 0.58, "moderate": 0.27, "stressed": 0.12, "critical": 0.03,
        },
        "flowering_pct": 0.08,
        "fruit_set_pct": 0.68,
    },
}


def _mango_harvest_forecast(peak: date, total_volume_mt: int, total_fields: int) -> list[dict[str, Any]]:
    """Build a 4-week bell-curve harvest forecast centred on ``peak``.

    Distribution mirrors the grape mock: 25% / 45% / 22% / 8% across the
    four weeks. Deterministic — no randomness.
    """
    # Anchor week_start on the Tuesday on/before the peak so the peak
    # itself falls inside week 2 of the bell (the 45% slice).
    week2_start = peak - timedelta(days=peak.weekday())  # Monday-anchored
    week1_start = week2_start - timedelta(days=7)
    weights = [0.25, 0.45, 0.22, 0.08]
    forecast: list[dict[str, Any]] = []
    cursor = week1_start
    for w in weights:
        forecast.append({
            "week_start": _iso(cursor),
            "week_end": _iso(cursor + timedelta(days=6)),
            "fields_harvesting": int(round(total_fields * w)),
            "estimated_volume_mt": int(round(total_volume_mt * w)),
        })
        cursor += timedelta(days=7)
    return forecast


def get_mango_belt_data(
    region: str,
    variety: str,
    season: str = "mango_2025_26",
) -> dict[str, Any]:
    """Return a mock AMED belt-level response for a mango region (SDD §10 Agent 4).

    Supported pairings:
      * Konkan + Alphonso  → 1240 fields, 4820 acres, peak Apr 22, 540 MT.
      * Nashik + Kesar     → 2860 fields, 8120 acres, peak May 18, 980 MT.
      * Vidarbha + Dasheri → 1410 fields, 3940 acres, peak Jun 24, 420 MT.

    Unknown pairings return an empty-volume scaffold so downstream
    consumers can still call the function safely.
    """
    region_key = (region or "").strip()
    variety_key = (variety or "").strip()
    profile = _MANGO_BELT_PROFILES.get((region_key, variety_key))
    bbox = _MANGO_BBOXES.get(region_key, _BELT_BBOX_DEFAULT)

    if profile is None:
        return {
            "region": region_key,
            "crop_type": "Mango",
            "variety": variety_key,
            "season": season,
            "bbox": bbox,
            "total_fields_detected": 0,
            "total_area_acres": 0,
            "harvest_forecast": [],
            "health_distribution": {},
            "flowering_pct": 0.0,
            "fruit_set_pct": 0.0,
            "peak_harvest_date": None,
            "data_refresh_date": _DATA_REFRESH,
            "source": "AMED",
        }

    forecast = _mango_harvest_forecast(
        profile["peak_harvest_date"],
        profile["estimated_total_volume_mt"],
        profile["total_fields_detected"],
    )

    return {
        "region": region_key,
        "crop_type": "Mango",
        "variety": variety_key,
        "season": season,
        "bbox": bbox,
        "total_fields_detected": profile["total_fields_detected"],
        "total_area_acres": profile["total_area_acres"],
        "peak_harvest_date": _iso(profile["peak_harvest_date"]),
        "estimated_total_volume_mt": profile["estimated_total_volume_mt"],
        "harvest_forecast": forecast,
        "health_distribution": dict(profile["health_distribution"]),
        "flowering_pct": profile["flowering_pct"],
        "fruit_set_pct": profile["fruit_set_pct"],
        "data_refresh_date": _DATA_REFRESH,
        "source": "AMED",
    }


def get_historical_data(
    bbox: dict[str, float],
    crop_type: str,
    seasons: list[str],
) -> list[dict[str, Any]]:
    """Return a mock AMED historical response (SDD Section 2.3).

    Filters the canned dataset by the seasons the caller requested.
    Unknown crops return an empty list.
    """
    library = _HISTORY_DEFAULT.get(crop_type, [])
    if not seasons:
        return [dict(row) for row in library]
    wanted = set(seasons)
    return [dict(row) for row in library if row["season"] in wanted]


# ---------------------------------------------------------------------------
# Synthetic 102-farmer seed
# ---------------------------------------------------------------------------

def generate_seeded_farmer_data(n: int = 102) -> list[dict[str, Any]]:
    """Return n synthetic AMED field responses for the seeded farmer set.

    Roughly 78% Grapes / 22% Pomegranate — the same mix used internally by
    get_field_data() — so the overall belt mix matches the SDD.

    Output is fully deterministic for a given n. Each entry is a
    get_field_data() response keyed by a stable plot_id of the form
    ``plot_001`` ... ``plot_NNN``.
    """
    rng = random.Random(_MASTER_SEED)
    # Decide crop assignment up front so the 78/22 split is exact rather
    # than approximate.
    n_grapes = int(round(n * 80 / 102))  # ~80 of 102
    n_pomegranate = n - n_grapes         # ~22 of 102
    crop_pool = (["Grapes"] * n_grapes) + (["Pomegranate"] * n_pomegranate)
    rng.shuffle(crop_pool)

    results: list[dict[str, Any]] = []
    for i, crop in enumerate(crop_pool, start=1):
        plot_id = f"plot_{i:03d}"
        season = "rabi_2025_26" if crop == "Grapes" else "bahar_2025_26"
        # Force the crop by routing through a deterministic per-plot
        # generator and overriding the crop choice if needed.
        record = get_field_data(plot_id=plot_id, crop_season=season)
        if record["crop_type"] != crop:
            # Rebuild with a crop override by re-seeding deterministically.
            # We keep the same plot_id seed and just patch the crop fields.
            record = _force_crop(record, crop, plot_id)
        record["plot_id"] = plot_id
        results.append(record)
    return results


def _force_crop(record: dict[str, Any], target_crop: str, plot_id: str) -> dict[str, Any]:
    """Patch a generated record to match a target crop while staying deterministic."""
    rng = _rng(plot_id + ":force")
    if target_crop == "Grapes":
        variety = rng.choice(_GRAPE_VARIETIES)
        harvest_date = date(2026, 4, 15) + timedelta(days=rng.randint(0, 25))
        growth_stage = rng.choice(_GROWTH_STAGES_GRAPES)
    else:
        variety = rng.choice(_POMEGRANATE_VARIETIES)
        bahar_months = [
            (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
            (2026, 1), (2026, 2),
        ]
        y, m = rng.choice(bahar_months)
        harvest_date = date(y, m, rng.randint(1, 25))
        growth_stage = rng.choice(_GROWTH_STAGES_POMEGRANATE)
    sowing_date = harvest_date - timedelta(days=rng.randint(120, 150))

    record = dict(record)
    record["crop_type"] = target_crop
    record["crop_variety_hint"] = variety
    record["sowing_date"] = _iso(sowing_date)
    record["harvest_date_predicted"] = _iso(harvest_date)
    record["growth_stage"] = growth_stage

    # Rebuild history so it stays consistent with the new crop type.
    history = []
    for yr in [2025, 2024, 2023]:
        if target_crop == "Grapes":
            h_date = date(yr, 4, 15) + timedelta(days=rng.randint(0, 25))
        else:
            hist_months = [
                (yr - 1, 8), (yr - 1, 9), (yr - 1, 10), (yr - 1, 11),
                (yr - 1, 12), (yr, 1), (yr, 2),
            ]
            y2, m2 = rng.choice(hist_months)
            h_date = date(y2, m2, rng.randint(1, 25))
        s_date = h_date - timedelta(days=rng.randint(120, 150))
        hist_area = max(0.5, _round(record["field_size_acres"] + rng.uniform(-0.2, 0.0), 1))
        season_label = f"{yr - 1}-{str(yr)[2:]}"
        history.append({
            "season": season_label,
            "crop_type": target_crop,
            "sowing_date": _iso(s_date),
            "harvest_date": _iso(h_date),
            "area_acres": hist_area,
        })
    record["history"] = history
    return record
