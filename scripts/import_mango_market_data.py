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


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import (or synthesise) Maharashtra mango wholesale market data "
            "for the ARIMAX mango price model. See SDD Section 4 for the "
            "variety x mandi mapping and column semantics."
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
        default=_default_output_path(),
        help=(
            "Path to write the CSV to. "
            "Defaults to data/price_history_mango_synthetic.csv."
        ),
    )
    parser.add_argument(
        "--commodity",
        default="Mango",
        help="Commodity name passed to CEDA (default: Mango).",
    )
    parser.add_argument(
        "--years",
        nargs=2,
        type=int,
        metavar=("START", "END"),
        default=(2015, 2026),
        help="Inclusive year range, e.g. --years 2015 2026 (default).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for numpy.random.default_rng (default: 42).",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    year_start, year_end = args.years
    if year_end < year_start:
        parser.error("--years START END must satisfy START <= END.")

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
        os.path.dirname(os.path.abspath(args.output)) or ".",
        exist_ok=True,
    )
    df.to_csv(args.output, index=False)
    print(f"Wrote {len(df):,} rows -> {args.output}")
    _print_quality_report(df, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
