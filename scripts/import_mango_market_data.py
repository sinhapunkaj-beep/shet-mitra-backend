"""Import (or synthesise) Maharashtra mango wholesale market data.

ShetMitra Mango Agent 2 - CEDA Market Data Importer.

This script produces a weekly Rs/kg-modal + arrivals MT series for the five
Maharashtra mango varieties identified in the Mango SDD (Section 4.1):

    Alphonso       (Konkan)        - export-grade premium
    Kesar          (Marathwada)    - mid-tier domestic premium
    Dasheri        (Vidarbha)      - North-Indian preference, mid-tier
    Totapuri       (Marathwada)    - processing/pulp variety, stable
    Banganapalli   (Western Mh)    - mid-tier domestic

It supports two modes:

* ``--live``  - actually hits the CEDA API
  (``https://api.ceda.ashoka.edu.in/v1/market-prices``) with the bearer
  token from ``shetmitra_test/nano.env``. Any network failure (DNS, TLS,
  HTTP, parse) is caught and we fall back to the synthetic generator with
  a warning so the script always produces a CSV.

* default     - deterministic synthetic dataset (numpy seed=42) covering
  the requested years for every variety x mandi pair from SDD Section 4.1,
  restricted to mango-season months 3..7 (March-July), weekly-Monday-anchored.

Output CSV columns:
    arrival_date, commodity, variety, mandi_name,
    price_modal_kg, arrivals_mt,
    price_lag_1, price_lag_7, price_lag_14,
    arrivals_lag_1, arrivals_7day_avg,
    season_week, month, year,
    price_yoy, arrivals_yoy,
    bearing_year_flag, export_demand_proxy,
    flowering_weather_score

The synthetic generator additionally:

* alternates bearing-year flag per calendar year (ON=odd 2015..2025,
  OFF=even 2016..2026), with OFF-year volumes scaled to 0.45 x ON-year;
* injects a 2024 Alphonso export-driven price spike toward Rs.650/kg;
* gives Totapuri (processing variety) the lowest price std/mean so the
  test suite can assert it is more stable than Alphonso;
* sources ``export_demand_proxy`` from the USD/INR synthetic series
  (delegated to ``scripts.import_usd_inr_forex`` if it has already
  been imported, else recomputed inline).

Run::

    python scripts/import_mango_market_data.py
    python scripts/import_mango_market_data.py --years 2015 2026
    python scripts/import_mango_market_data.py --live
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Make repo root importable so we can lean on the forex synth helper.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CEDA_BASE_URL = "https://api.ceda.ashoka.edu.in/v1"
CEDA_KEY_PATH = Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env")
CEDA_MAX_ROWS = 10_000

# Mango season: months 3..7 (SDD Section 4.2).
MANGO_SEASON_MONTHS: tuple[int, ...] = (3, 4, 5, 6, 7)

# Bearing-year alternation. ON=odd years, OFF=even years.
_ON_YEARS = {2015, 2017, 2019, 2021, 2023, 2025}
_OFF_YEARS = {2016, 2018, 2020, 2022, 2024, 2026}


@dataclass(frozen=True)
class _Variety:
    """Per-variety tuning constants (SDD Sections 4.1, 4.2)."""

    name: str
    mandis: tuple[str, ...]
    base_price_kg: float
    price_amplitude: float
    arrivals_peak_mt: float
    arrivals_base_mt: float
    noise_price_kg: float
    noise_arrivals_mt: float


_VARIETIES: tuple[_Variety, ...] = (
    _Variety(
        name="Alphonso",
        mandis=(
            "Ratnagiri APMC",
            "Devgad APMC",
            "Chiplun APMC",
            "Vashi APMC",
        ),
        base_price_kg=400.0,
        price_amplitude=60.0,
        arrivals_peak_mt=450.0,
        arrivals_base_mt=200.0,
        noise_price_kg=15.0,
        noise_arrivals_mt=20.0,
    ),
    _Variety(
        name="Kesar",
        mandis=(
            "Aurangabad APMC",
            "Nashik APMC",
            "Pune APMC",
            "Ahmednagar APMC",
        ),
        base_price_kg=180.0,
        price_amplitude=30.0,
        arrivals_peak_mt=300.0,
        arrivals_base_mt=120.0,
        noise_price_kg=8.0,
        noise_arrivals_mt=15.0,
    ),
    _Variety(
        name="Dasheri",
        mandis=(
            "Nagpur APMC",
            "Amravati APMC",
        ),
        base_price_kg=80.0,
        price_amplitude=18.0,
        arrivals_peak_mt=150.0,
        arrivals_base_mt=60.0,
        noise_price_kg=5.0,
        noise_arrivals_mt=8.0,
    ),
    _Variety(
        name="Totapuri",
        # Processing/pulp variety - stable prices.
        mandis=(
            "Latur APMC",
            "Solapur APMC",
            "Osmanabad APMC",
        ),
        base_price_kg=40.0,
        price_amplitude=4.0,
        arrivals_peak_mt=600.0,
        arrivals_base_mt=250.0,
        noise_price_kg=1.2,
        noise_arrivals_mt=25.0,
    ),
    _Variety(
        name="Banganapalli",
        mandis=(
            "Sangli APMC",
            "Kolhapur APMC",
            "Satara APMC",
        ),
        base_price_kg=120.0,
        price_amplitude=22.0,
        arrivals_peak_mt=100.0,
        arrivals_base_mt=45.0,
        noise_price_kg=6.0,
        noise_arrivals_mt=6.0,
    ),
)


_VARIETIES_BY_NAME = {v.name: v for v in _VARIETIES}


# ---------------------------------------------------------------------------
# Jharkhand / Bihar / Bengal belt (SDD Section 3.4)
# ---------------------------------------------------------------------------

# Mapping: state -> list of mandi names. These mirror SDD §3.1 + §3.4 and are
# used both by --state filtering and by the --jharkhand preset.
JHARKHAND_BELT_MANDIS: dict[str, tuple[str, ...]] = {
    "Jharkhand": (
        "Ranchi APMC",
        "Deoghar APMC",
        "Dumka APMC",
        "Godda Mandi",
        "Sahebganj Mandi",
    ),
    "Bihar": (
        "Bhagalpur APMC",
        "Munger APMC",
        "Patna APMC",
        "Saharsa APMC",
        "Banka APMC",
        "Purnea APMC",
    ),
    "West Bengal": (
        "Malda APMC",
        "Murshidabad Mandi",
        "Kolkata Koley Market",
    ),
    "Delhi": (
        "Delhi Azadpur APMC",
    ),
}


# Per-mandi "premium tilt" so different mandis price the same variety
# differently (Bhagalpur pays best for Jardalu GI; Delhi Azadpur sets the
# national benchmark; Kolkata Koley sets Bengal reference; etc.).
_JHARKHAND_MANDI_PREMIUM: dict[str, float] = {
    # Jharkhand
    "Ranchi APMC": 1.00,
    "Deoghar APMC": 0.95,
    "Dumka APMC": 0.92,
    "Godda Mandi": 1.05,
    "Sahebganj Mandi": 0.98,
    # Bihar
    "Bhagalpur APMC": 1.15,   # MOST IMPORTANT - Jardalu GI buyer
    "Munger APMC": 1.02,
    "Patna APMC": 1.10,       # state capital premium buyer
    "Saharsa APMC": 0.95,
    "Banka APMC": 1.03,
    "Purnea APMC": 0.97,
    # West Bengal
    "Malda APMC": 1.08,
    "Murshidabad Mandi": 1.00,
    "Kolkata Koley Market": 1.12,  # price setter
    # National
    "Delhi Azadpur APMC": 1.18,    # national price signal
}


@dataclass(frozen=True)
class _JharkhandVariety:
    """Per-variety tuning constants for the Jharkhand belt (SDD §3.2/3.4)."""

    name: str
    base_price_kg: float
    price_amplitude: float
    arrivals_peak_mt: float
    arrivals_base_mt: float
    noise_price_kg: float
    noise_arrivals_mt: float
    season_months: tuple[int, ...]
    # Mandis where this variety is materially traded.
    mandis: tuple[str, ...]
    # GI / lumpy-premium variety gets occasional spike multipliers.
    gi_lumpy: bool = False


# Mango season in the eastern belt skews to May-July (Mallika harvest May-Jun,
# Amrapali June-July, Jardalu May-Jun GI window).
_JHARKHAND_SEASON_MONTHS: tuple[int, ...] = (4, 5, 6, 7)


_JHARKHAND_VARIETIES: tuple[_JharkhandVariety, ...] = (
    _JharkhandVariety(
        name="Mallika",
        base_price_kg=70.0,
        price_amplitude=18.0,
        arrivals_peak_mt=220.0,
        arrivals_base_mt=80.0,
        noise_price_kg=4.5,
        noise_arrivals_mt=12.0,
        season_months=(5, 6),
        mandis=(
            "Bhagalpur APMC",
            "Ranchi APMC",
            "Deoghar APMC",
            "Dumka APMC",
            "Patna APMC",
            "Delhi Azadpur APMC",
            "Malda APMC",
            "Kolkata Koley Market",
        ),
    ),
    _JharkhandVariety(
        name="Amrapali",
        base_price_kg=85.0,
        price_amplitude=20.0,
        arrivals_peak_mt=180.0,
        arrivals_base_mt=70.0,
        noise_price_kg=4.0,
        noise_arrivals_mt=10.0,
        season_months=(6, 7),
        mandis=(
            "Ranchi APMC",
            "Patna APMC",
            "Bhagalpur APMC",
            "Kolkata Koley Market",
            "Delhi Azadpur APMC",
            "Murshidabad Mandi",
        ),
    ),
    _JharkhandVariety(
        name="Jardalu",
        base_price_kg=160.0,        # GI premium baseline
        price_amplitude=22.0,       # moderate seasonal swing
        arrivals_peak_mt=80.0,
        arrivals_base_mt=20.0,
        noise_price_kg=4.5,         # slightly noisier than other varieties
        noise_arrivals_mt=6.0,
        season_months=(5, 6),
        mandis=(
            "Bhagalpur APMC",       # primary
            "Godda Mandi",          # GI specialist
            "Banka APMC",
            "Sahebganj Mandi",
            "Delhi Azadpur APMC",
        ),
        gi_lumpy=True,
    ),
)


_JHARKHAND_VARIETIES_BY_NAME = {v.name: v for v in _JHARKHAND_VARIETIES}


def _jharkhand_mandi_to_state(mandi: str) -> str:
    for state, mandis in JHARKHAND_BELT_MANDIS.items():
        if mandi in mandis:
            return state
    return "Unknown"


# ---------------------------------------------------------------------------
# Week generation helpers
# ---------------------------------------------------------------------------

def _mondays_in_year_range(year_start: int, year_end: int) -> list[date]:
    """All Mondays from year_start-01-01 through year_end-12-31 (inclusive)."""
    cursor = date(year_start, 1, 1)
    # Snap forward to the next Monday.
    if cursor.weekday() != 0:
        cursor = cursor + timedelta(days=(7 - cursor.weekday()) % 7)
    end = date(year_end, 12, 31)
    out: list[date] = []
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=7)
    return out


def _season_week_for_date(d: date) -> int:
    """1-based week index inside the active mango season (else 0).

    Season window = first Monday of March through last Monday of July.
    Week 1 = first Monday of March of the row's year.
    """
    if d.month not in MANGO_SEASON_MONTHS:
        return 0
    march_first = date(d.year, 3, 1)
    if march_first.weekday() != 0:
        march_first = march_first + timedelta(
            days=(7 - march_first.weekday()) % 7
        )
    delta_days = (d - march_first).days
    if delta_days < 0:
        return 0
    return (delta_days // 7) + 1


def _bearing_flag(year: int) -> int:
    if year in _ON_YEARS:
        return 1
    if year in _OFF_YEARS:
        return 0
    return -1


# ---------------------------------------------------------------------------
# Forex (USD/INR) helper
# ---------------------------------------------------------------------------

def _synthetic_usd_inr_series(
    year_start: int,
    year_end: int,
    seed: int = 42,
) -> dict[date, float]:
    """Deterministic synthetic USD/INR rate per Monday across the range.

    Sinusoidal trend that drifts from ~65 (early 2015) to ~84 (late 2026),
    with a small daily noise term. Returns ``{monday_date: rate}`` covering
    every Monday in the range so callers can look up per-week proxies.
    """
    rng = np.random.default_rng(seed + 7)  # offset so it does not collide.
    mondays = _mondays_in_year_range(year_start, year_end)
    if not mondays:
        return {}
    total_weeks = len(mondays)
    out: dict[date, float] = {}
    for idx, d in enumerate(mondays):
        # Linear drift 65 -> 84 across the range.
        progress = idx / max(1, total_weeks - 1)
        baseline = 65.0 + (84.0 - 65.0) * progress
        # Slow seasonal wobble (~12-month period) +/- 1.5.
        day_of_year = (d - date(d.year, 1, 1)).days
        seasonal = 1.5 * math.sin(2.0 * math.pi * day_of_year / 365.25)
        noise = float(rng.normal(loc=0.0, scale=0.3))
        out[d] = round(baseline + seasonal + noise, 4)
    return out


def _flowering_weather_score(d: date, variety_name: str, rng: np.random.Generator) -> float:
    """0..100 'good flowering weather' proxy.

    Higher in March-April (flowering peak) and gradually decays through
    July. Adds a small variety-specific tilt so each variety gets a
    slightly different distribution.
    """
    if d.month not in MANGO_SEASON_MONTHS:
        return 0.0
    # March (3) -> 95 peak, decay to ~50 by July (7).
    peak = 95.0 - 10.0 * (d.month - 3)
    tilt = {
        "Alphonso": 2.0,
        "Kesar": 0.0,
        "Dasheri": -1.0,
        "Totapuri": -2.0,
        "Banganapalli": 1.0,
    }.get(variety_name, 0.0)
    noise = float(rng.normal(loc=0.0, scale=4.0))
    score = peak + tilt + noise
    return float(max(0.0, min(100.0, round(score, 2))))


# ---------------------------------------------------------------------------
# Synthetic generator
# ---------------------------------------------------------------------------

def _alphonso_export_spike_factor(d: date) -> float:
    """Return the multiplicative price factor for the 2024 Alphonso spike.

    Targets the SDD's 'Rs.650/kg' export-driven spike across the 2024
    mango season. The factor is centred on May 1 2024 with a Gaussian
    decay so adjacent weeks also lift, peaking at roughly 650/400=1.625.
    """
    if d.year != 2024 or d.month not in MANGO_SEASON_MONTHS:
        return 1.0
    centre = date(2024, 5, 1)
    sigma_days = 45.0
    offset = (d - centre).days
    weight = math.exp(-(offset ** 2) / (2.0 * sigma_days ** 2))
    # Lift baseline 400 -> 650 at the peak.
    return 1.0 + weight * (650.0 / 400.0 - 1.0)


def _seasonal_price_component(d: date, variety: _Variety) -> float:
    """Sinusoidal seasonal component centred on April (mid-season).

    Mango season is March-July; we anchor the peak at April 15 of each
    year so prices rise into April then fall toward July.
    """
    peak_doy = (date(d.year, 4, 15) - date(d.year, 1, 1)).days
    doy = (d - date(d.year, 1, 1)).days
    phase = 2.0 * math.pi * (doy - peak_doy) / 365.25
    return variety.price_amplitude * math.cos(phase)


def _generate_one_variety_mandi(
    variety: _Variety,
    mandi: str,
    mondays: list[date],
    forex: dict[date, float],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Build the row set for a single (variety, mandi) pair.

    Only Mondays inside the mango season (months 3-7) are emitted.
    Lag / rolling / YoY columns are computed at the (variety, mandi)
    level so the shifts are exact.
    """
    rows: list[dict[str, object]] = []
    for d in mondays:
        if d.month not in MANGO_SEASON_MONTHS:
            continue

        # ---- price ----
        seasonal = _seasonal_price_component(d, variety)
        # YoY drift: small per-variety creep ~1%/yr above 2015 base.
        years_since_2015 = d.year - 2015 + ((d - date(d.year, 1, 1)).days / 365.25)
        drift = 0.01 * variety.base_price_kg * years_since_2015
        noise = float(rng.normal(loc=0.0, scale=variety.noise_price_kg))
        price = variety.base_price_kg + seasonal + drift + noise

        if variety.name == "Alphonso":
            price *= _alphonso_export_spike_factor(d)

        # ---- arrivals ----
        # Bell-curve around April 15 within season; OFF-year volumes
        # scaled to 0.45x; small Gaussian noise.
        peak_doy = (date(d.year, 4, 15) - date(d.year, 1, 1)).days
        doy = (d - date(d.year, 1, 1)).days
        sigma_days = 35.0
        bell = math.exp(-((doy - peak_doy) ** 2) / (2.0 * sigma_days ** 2))
        arrivals = (
            variety.arrivals_base_mt
            + variety.arrivals_peak_mt * bell
            + float(rng.normal(loc=0.0, scale=variety.noise_arrivals_mt))
        )
        bearing_year_flag = _bearing_flag(d.year)
        if bearing_year_flag == 0:
            arrivals *= 0.45
        arrivals = max(0.0, arrivals)

        # ---- exogenous proxies ----
        usd_inr = float(forex.get(d, 75.0))
        flowering_score = _flowering_weather_score(d, variety.name, rng)
        season_week = _season_week_for_date(d)

        rows.append({
            "arrival_date": d.isoformat(),
            "commodity": "Mango",
            "variety": variety.name,
            "mandi_name": mandi,
            "price_modal_kg": round(price, 2),
            "arrivals_mt": round(arrivals, 2),
            "season_week": season_week,
            "month": d.month,
            "year": d.year,
            "bearing_year_flag": bearing_year_flag,
            "export_demand_proxy": round(usd_inr, 4),
            "flowering_weather_score": flowering_score,
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # ---- lags and rolling features (chronological) ----
    df["price_lag_1"] = df["price_modal_kg"].shift(1)
    df["price_lag_7"] = df["price_modal_kg"].shift(7)
    df["price_lag_14"] = df["price_modal_kg"].shift(14)
    df["arrivals_lag_1"] = df["arrivals_mt"].shift(1)
    df["arrivals_7day_avg"] = (
        df["arrivals_mt"].rolling(window=7, min_periods=1).mean()
    )

    # In-season weekly rows; YoY shift is ~20 (5 months x ~4 weeks).
    # Because we restrict to months 3..7 each year contains roughly 20
    # weekly rows per (variety, mandi). Use that as the YoY lag.
    df["price_yoy"] = df["price_modal_kg"] - df["price_modal_kg"].shift(20)
    df["arrivals_yoy"] = df["arrivals_mt"] - df["arrivals_mt"].shift(20)

    return df


def build_synthetic_dataframe(
    year_start: int = 2015,
    year_end: int = 2026,
    seed: int = 42,
) -> pd.DataFrame:
    """Build the full synthetic mango mandi DataFrame."""
    rng = np.random.default_rng(seed)
    mondays = _mondays_in_year_range(year_start, year_end)
    forex = _synthetic_usd_inr_series(year_start, year_end, seed=seed)

    frames: list[pd.DataFrame] = []
    for variety in _VARIETIES:
        for mandi in variety.mandis:
            sub = _generate_one_variety_mandi(
                variety=variety,
                mandi=mandi,
                mondays=mondays,
                forex=forex,
                rng=rng,
            )
            if not sub.empty:
                frames.append(sub)

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    column_order = [
        "arrival_date", "commodity", "variety", "mandi_name",
        "price_modal_kg", "arrivals_mt",
        "price_lag_1", "price_lag_7", "price_lag_14",
        "arrivals_lag_1", "arrivals_7day_avg",
        "season_week", "month", "year",
        "price_yoy", "arrivals_yoy",
        "bearing_year_flag", "export_demand_proxy",
        "flowering_weather_score",
    ]
    return df[column_order]


# ---------------------------------------------------------------------------
# CEDA live fetch (best-effort; falls back to synthetic on any failure)
# ---------------------------------------------------------------------------

def _load_ceda_key() -> str | None:
    """Read CEDA_API_KEY from shetmitra_test/nano.env, else from env."""
    env_key = os.environ.get("CEDA_API_KEY")
    if env_key:
        return env_key
    if not CEDA_KEY_PATH.exists():
        return None
    try:
        for raw in CEDA_KEY_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "CEDA_API_KEY":
                return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _fetch_ceda_pages(
    commodity: str,
    state: str,
    max_rows: int,
    api_key: str,
) -> list[dict]:
    """Page through CEDA market-prices. Raises on any HTTP / parse failure."""
    import httpx  # local import so synth mode does not require httpx

    rows: list[dict] = []
    headers = {"Authorization": f"Bearer {api_key}"}
    page = 1
    page_size = 500
    with httpx.Client(timeout=20.0) as client:
        while len(rows) < max_rows:
            resp = client.get(
                f"{CEDA_BASE_URL}/market-prices",
                headers=headers,
                params={
                    "commodity": commodity,
                    "state": state,
                    "page": page,
                    "page_size": page_size,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            page_rows = (
                payload.get("data") or payload.get("results") or payload
            )
            if not isinstance(page_rows, list) or not page_rows:
                break
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                break
            page += 1
    return rows[:max_rows]


def _ceda_rows_to_dataframe(rows: list[dict]) -> pd.DataFrame:
    """Translate CEDA payload rows into our CSV schema.

    Per SDD Section 4.2:
      * price Rs/Quintal -> Rs/kg  (divide by 100)
      * arrivals Quintals -> MT    (divide by 10)
    """
    parsed: list[dict] = []
    for row in rows:
        try:
            arrival_date = row.get("arrival_date") or row.get("date")
            variety = row.get("variety") or row.get("variety_name") or "Unknown"
            mandi = row.get("market") or row.get("mandi") or row.get("mandi_name")
            price_q = float(row.get("price_modal") or row.get("modal_price") or 0)
            arrivals_q = float(row.get("arrivals") or row.get("arrivals_qtl") or 0)
        except (TypeError, ValueError):
            continue
        if not arrival_date or not mandi:
            continue
        try:
            d = date.fromisoformat(str(arrival_date)[:10])
        except ValueError:
            continue
        if d.month not in MANGO_SEASON_MONTHS:
            continue
        parsed.append({
            "arrival_date": d.isoformat(),
            "commodity": "Mango",
            "variety": variety,
            "mandi_name": mandi,
            "price_modal_kg": round(price_q / 100.0, 2),
            "arrivals_mt": round(arrivals_q / 10.0, 2),
            "season_week": _season_week_for_date(d),
            "month": d.month,
            "year": d.year,
            "bearing_year_flag": _bearing_flag(d.year),
            "export_demand_proxy": None,
            "flowering_weather_score": None,
        })
    if not parsed:
        return pd.DataFrame()
    df = pd.DataFrame(parsed).sort_values(
        ["variety", "mandi_name", "arrival_date"]
    ).reset_index(drop=True)
    # Lag / rolling / yoy per (variety, mandi).
    grp = df.groupby(["variety", "mandi_name"], group_keys=False)
    df["price_lag_1"] = grp["price_modal_kg"].shift(1)
    df["price_lag_7"] = grp["price_modal_kg"].shift(7)
    df["price_lag_14"] = grp["price_modal_kg"].shift(14)
    df["arrivals_lag_1"] = grp["arrivals_mt"].shift(1)
    df["arrivals_7day_avg"] = grp["arrivals_mt"].transform(
        lambda s: s.rolling(window=7, min_periods=1).mean()
    )
    df["price_yoy"] = grp["price_modal_kg"].shift(20).rsub(df["price_modal_kg"])
    df["arrivals_yoy"] = grp["arrivals_mt"].shift(20).rsub(df["arrivals_mt"])
    return df[[
        "arrival_date", "commodity", "variety", "mandi_name",
        "price_modal_kg", "arrivals_mt",
        "price_lag_1", "price_lag_7", "price_lag_14",
        "arrivals_lag_1", "arrivals_7day_avg",
        "season_week", "month", "year",
        "price_yoy", "arrivals_yoy",
        "bearing_year_flag", "export_demand_proxy",
        "flowering_weather_score",
    ]]


def fetch_live_or_fallback(
    commodity: str,
    year_start: int,
    year_end: int,
    seed: int,
) -> tuple[pd.DataFrame, str]:
    """Try CEDA live; on any failure return synthetic. Returns (df, source)."""
    api_key = _load_ceda_key()
    if not api_key:
        print(
            "[warn] No CEDA_API_KEY found - falling back to synthetic data.",
            file=sys.stderr,
        )
        return build_synthetic_dataframe(year_start, year_end, seed), "synthetic"

    try:
        rows = _fetch_ceda_pages(
            commodity=commodity,
            state="Maharashtra",
            max_rows=CEDA_MAX_ROWS,
            api_key=api_key,
        )
    except Exception as exc:  # noqa: BLE001 - intentionally broad
        print(
            f"[warn] CEDA live fetch failed ({type(exc).__name__}: {exc!s}). "
            "Falling back to synthetic data.",
            file=sys.stderr,
        )
        return build_synthetic_dataframe(year_start, year_end, seed), "synthetic"

    df = _ceda_rows_to_dataframe(rows)
    if df.empty:
        print(
            "[warn] CEDA returned no usable rows. Falling back to synthetic.",
            file=sys.stderr,
        )
        return build_synthetic_dataframe(year_start, year_end, seed), "synthetic"
    return df, "ceda_live"


# ---------------------------------------------------------------------------
# Jharkhand belt synthetic generator (SDD §3.4)
# ---------------------------------------------------------------------------

def _jardalu_gi_spike_factor(d: date, mandi: str, rng: np.random.Generator) -> float:
    """Lumpy GI premium spike for Jardalu.

    Mid-May concentration; Bhagalpur APMC sees the biggest lumps (it's the
    primary GI buyer) and Delhi Azadpur sees the secondary signal. Random
    sub-spikes give the series its 'lumpy' character that motivates the
    relaxed 20% MAPE target in SDD §3.4.
    """
    if d.month not in (5, 6):
        return 1.0
    centre = date(d.year, 5, 20)
    sigma_days = 25.0
    offset = (d - centre).days
    base_weight = math.exp(-(offset ** 2) / (2.0 * sigma_days ** 2))
    mandi_lift = {
        "Bhagalpur APMC": 0.14,
        "Godda Mandi": 0.10,
        "Delhi Azadpur APMC": 0.08,
    }.get(mandi, 0.05)
    # Occasional lumpy kick: ~5% of weeks see an extra 3-6% lift.
    lumpy_kick = 0.0
    if rng.random() < 0.05:
        lumpy_kick = float(rng.uniform(0.03, 0.06))
    return 1.0 + base_weight * mandi_lift + lumpy_kick


def _generate_one_jharkhand_pair(
    variety: _JharkhandVariety,
    mandi: str,
    mondays: list[date],
    forex: dict[date, float],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Build rows for a single (variety, mandi) pair in the Jharkhand belt."""
    rows: list[dict[str, object]] = []
    mandi_premium = _JHARKHAND_MANDI_PREMIUM.get(mandi, 1.0)
    state = _jharkhand_mandi_to_state(mandi)
    for d in mondays:
        if d.month not in variety.season_months:
            continue

        # ---- price ----
        peak_doy = (date(d.year, 5, 20) - date(d.year, 1, 1)).days
        doy = (d - date(d.year, 1, 1)).days
        phase = 2.0 * math.pi * (doy - peak_doy) / 365.25
        seasonal = variety.price_amplitude * math.cos(phase)
        years_since_2015 = (
            d.year - 2015 + ((d - date(d.year, 1, 1)).days / 365.25)
        )
        drift = 0.012 * variety.base_price_kg * years_since_2015
        noise = float(rng.normal(loc=0.0, scale=variety.noise_price_kg))
        price = (variety.base_price_kg + seasonal + drift + noise) * mandi_premium

        if variety.gi_lumpy:
            price *= _jardalu_gi_spike_factor(d, mandi, rng)

        # ---- arrivals ----
        sigma_days = 30.0
        bell = math.exp(-((doy - peak_doy) ** 2) / (2.0 * sigma_days ** 2))
        arrivals = (
            variety.arrivals_base_mt
            + variety.arrivals_peak_mt * bell
            + float(rng.normal(loc=0.0, scale=variety.noise_arrivals_mt))
        )
        bearing_year_flag = _bearing_flag(d.year)
        # Eastern-belt varieties are mostly non-alternate-bearing (SDD §3.2)
        # so the OFF factor is gentler than Maharashtra (~0.8 vs 0.45).
        if bearing_year_flag == 0:
            arrivals *= 0.82
        arrivals = max(0.0, arrivals)

        # ---- exogenous proxies ----
        usd_inr = float(forex.get(d, 75.0))
        flowering_score = _flowering_weather_score(d, variety.name, rng)
        season_week = _season_week_for_date(d)

        rows.append({
            "arrival_date": d.isoformat(),
            "commodity": "Mango",
            "variety": variety.name,
            "mandi_name": mandi,
            "state": state,
            "price_modal_kg": round(price, 2),
            "arrivals_mt": round(arrivals, 2),
            "season_week": season_week,
            "month": d.month,
            "year": d.year,
            "bearing_year_flag": bearing_year_flag,
            "export_demand_proxy": round(usd_inr, 4),
            "flowering_weather_score": flowering_score,
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df["price_lag_1"] = df["price_modal_kg"].shift(1)
    df["price_lag_7"] = df["price_modal_kg"].shift(7)
    df["price_lag_14"] = df["price_modal_kg"].shift(14)
    df["arrivals_lag_1"] = df["arrivals_mt"].shift(1)
    df["arrivals_7day_avg"] = (
        df["arrivals_mt"].rolling(window=7, min_periods=1).mean()
    )
    # ~8-12 weekly rows per year in months 4..7 -> use 10 as YoY lag.
    df["price_yoy"] = df["price_modal_kg"] - df["price_modal_kg"].shift(10)
    df["arrivals_yoy"] = df["arrivals_mt"] - df["arrivals_mt"].shift(10)
    return df


def build_jharkhand_synthetic_dataframe(
    year_start: int = 2015,
    year_end: int = 2026,
    seed: int = 42,
    mandi_filter: tuple[str, ...] | None = None,
    state_filter: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Build the synthetic Jharkhand-belt mango mandi DataFrame.

    Args:
        year_start, year_end: inclusive year range.
        seed: deterministic RNG seed.
        mandi_filter: if given, only emit rows for these mandi names.
        state_filter: if given, only emit rows for mandis in these states.
    """
    rng = np.random.default_rng(seed + 101)  # offset from Maharashtra path.
    mondays = _mondays_in_year_range(year_start, year_end)
    forex = _synthetic_usd_inr_series(year_start, year_end, seed=seed)

    if mandi_filter is not None:
        mandi_filter = tuple(m.strip() for m in mandi_filter if m and m.strip())
    if state_filter is not None:
        state_filter = tuple(s.strip() for s in state_filter if s and s.strip())

    frames: list[pd.DataFrame] = []
    for variety in _JHARKHAND_VARIETIES:
        for mandi in variety.mandis:
            if mandi_filter and mandi not in mandi_filter:
                continue
            if state_filter and _jharkhand_mandi_to_state(mandi) not in state_filter:
                continue
            sub = _generate_one_jharkhand_pair(
                variety=variety,
                mandi=mandi,
                mondays=mondays,
                forex=forex,
                rng=rng,
            )
            if not sub.empty:
                frames.append(sub)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    column_order = [
        "arrival_date", "commodity", "variety", "mandi_name", "state",
        "price_modal_kg", "arrivals_mt",
        "price_lag_1", "price_lag_7", "price_lag_14",
        "arrivals_lag_1", "arrivals_7day_avg",
        "season_week", "month", "year",
        "price_yoy", "arrivals_yoy",
        "bearing_year_flag", "export_demand_proxy",
        "flowering_weather_score",
    ]
    return df[column_order]


def fetch_jharkhand_live_or_fallback(
    year_start: int,
    year_end: int,
    seed: int,
    mandis: tuple[str, ...] | None,
    states: tuple[str, ...] | None,
) -> tuple[pd.DataFrame, str]:
    """Try CEDA live across Jharkhand-belt states; fall back to synthetic.

    Live-mode parity with Maharashtra path: any HTTP / parse / empty
    response triggers a fallback to ``build_jharkhand_synthetic_dataframe``.
    """
    api_key = _load_ceda_key()
    if not api_key:
        print(
            "[warn] No CEDA_API_KEY found - falling back to synthetic Jharkhand data.",
            file=sys.stderr,
        )
        return (
            build_jharkhand_synthetic_dataframe(
                year_start, year_end, seed,
                mandi_filter=mandis, state_filter=states,
            ),
            "synthetic",
        )

    fetch_states = (
        states if states else tuple(JHARKHAND_BELT_MANDIS.keys())
    )
    all_rows: list[dict] = []
    for state in fetch_states:
        try:
            rows = _fetch_ceda_pages(
                commodity="Mango",
                state=state,
                max_rows=CEDA_MAX_ROWS,
                api_key=api_key,
            )
            all_rows.extend(rows)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[warn] CEDA live fetch failed for state={state} "
                f"({type(exc).__name__}: {exc!s}). Continuing.",
                file=sys.stderr,
            )

    if not all_rows:
        print(
            "[warn] CEDA returned no Jharkhand-belt rows. "
            "Falling back to synthetic.",
            file=sys.stderr,
        )
        return (
            build_jharkhand_synthetic_dataframe(
                year_start, year_end, seed,
                mandi_filter=mandis, state_filter=states,
            ),
            "synthetic",
        )

    df = _ceda_rows_to_dataframe(all_rows)
    if df.empty:
        return (
            build_jharkhand_synthetic_dataframe(
                year_start, year_end, seed,
                mandi_filter=mandis, state_filter=states,
            ),
            "synthetic",
        )
    if mandis:
        df = df[df["mandi_name"].isin(mandis)].reset_index(drop=True)
    return df, "ceda_live"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_quality_report(df: pd.DataFrame, source: str) -> None:
    print("=" * 60)
    print(f"Mango market data summary (source={source})")
    print("=" * 60)
    print(f"Total rows:        {len(df):,}")
    if df.empty:
        print("No rows produced.")
        return
    print(
        f"Date range:        {df['arrival_date'].min()} -> "
        f"{df['arrival_date'].max()}"
    )
    print(
        f"Price range:       "
        f"Rs.{df['price_modal_kg'].min():.2f}/kg .. "
        f"Rs.{df['price_modal_kg'].max():.2f}/kg"
    )
    print()
    print("Rows per variety:")
    by_var = df.groupby("variety", sort=False)
    for variety, sub in by_var:
        mean_price = float(sub["price_modal_kg"].mean())
        mean_arrivals = float(sub["arrivals_mt"].mean())
        print(
            f"  {variety:13s} rows={len(sub):5d}  "
            f"mean_price={mean_price:7.2f}  mean_arrivals={mean_arrivals:7.2f}"
        )
    print()
    print("Rows per mandi:")
    by_mandi = df.groupby(["variety", "mandi_name"], sort=False)
    for (variety, mandi), sub in by_mandi:
        print(f"  {variety:13s} {mandi:25s} rows={len(sub):5d}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_output_path() -> str:
    return os.path.join("data", "price_history_mango_synthetic.csv")


def _default_jharkhand_output_path() -> str:
    return os.path.join("data", "price_history_jharkhand_mango.csv")


def _parse_years_arg(raw: object) -> tuple[int, int]:
    """Accept either ['2015','2026'] (legacy nargs=2) or '2015-2026'."""
    if isinstance(raw, (list, tuple)):
        if len(raw) == 2:
            return int(raw[0]), int(raw[1])
        if len(raw) == 1:
            raw = raw[0]
    if isinstance(raw, str):
        if "-" in raw:
            a, b = raw.split("-", 1)
            return int(a.strip()), int(b.strip())
        return int(raw), int(raw)
    raise ValueError(f"Cannot interpret --years value: {raw!r}")


def _split_csv_arg(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    return tuple(part.strip() for part in value.split(",") if part.strip())


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import (or synthesise) mango wholesale market data for the "
            "ARIMAX mango price models. Supports Maharashtra (default) and "
            "Jharkhand/Bihar/Bengal belts (--jharkhand)."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Actually hit the CEDA API for live data. Default is OFF "
            "(synthetic). Any network failure falls back to synthetic."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Path to write the CSV to. Defaults to "
            "data/price_history_mango_synthetic.csv (Maharashtra) or "
            "data/price_history_jharkhand_mango.csv (--jharkhand)."
        ),
    )
    parser.add_argument(
        "--commodity",
        default="Mango",
        help="Commodity name passed to CEDA (default: Mango).",
    )
    parser.add_argument(
        "--years",
        nargs="+",
        default=["2015", "2026"],
        help=(
            "Inclusive year range. Accepts either --years 2015 2026 "
            "or --years 2015-2026."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for numpy.random.default_rng (default: 42).",
    )
    parser.add_argument(
        "--jharkhand",
        action="store_true",
        help=(
            "Preset for the Jharkhand belt: pulls Jharkhand+Bihar+Bengal+"
            "Delhi mandis (SDD §3.1/§3.4) and writes to "
            "data/price_history_jharkhand_mango.csv."
        ),
    )
    parser.add_argument(
        "--mandis",
        default=None,
        help=(
            "Comma-separated mandi name filter "
            "(e.g. 'Bhagalpur APMC,Ranchi APMC'). "
            "Applies to Jharkhand-belt mode."
        ),
    )
    parser.add_argument(
        "--state",
        default=None,
        help=(
            "Comma-separated state filter "
            "(e.g. 'Jharkhand,Bihar'). "
            "Applies to Jharkhand-belt mode."
        ),
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        year_start, year_end = _parse_years_arg(args.years)
    except ValueError as exc:
        parser.error(str(exc))
    if year_end < year_start:
        parser.error("--years must satisfy START <= END.")

    mandis = _split_csv_arg(args.mandis)
    states = _split_csv_arg(args.state)

    if args.jharkhand or mandis is not None or states is not None:
        # Jharkhand belt mode.
        output = args.output or _default_jharkhand_output_path()
        if args.jharkhand and mandis is None and states is None:
            # The preset: fill all four states from the belt mapping.
            states = tuple(JHARKHAND_BELT_MANDIS.keys())
        if args.live:
            df, source = fetch_jharkhand_live_or_fallback(
                year_start=year_start,
                year_end=year_end,
                seed=args.seed,
                mandis=mandis,
                states=states,
            )
        else:
            df = build_jharkhand_synthetic_dataframe(
                year_start=year_start,
                year_end=year_end,
                seed=args.seed,
                mandi_filter=mandis,
                state_filter=states,
            )
            source = "synthetic_jharkhand"
    else:
        output = args.output or _default_output_path()
        if args.live:
            df, source = fetch_live_or_fallback(
                commodity=args.commodity,
                year_start=year_start,
                year_end=year_end,
                seed=args.seed,
            )
        else:
            df = build_synthetic_dataframe(year_start, year_end, args.seed)
            source = "synthetic"

    os.makedirs(
        os.path.dirname(os.path.abspath(output)) or ".",
        exist_ok=True,
    )
    df.to_csv(output, index=False)
    print(f"Wrote {len(df):,} rows -> {output}")
    _print_quality_report(df, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
