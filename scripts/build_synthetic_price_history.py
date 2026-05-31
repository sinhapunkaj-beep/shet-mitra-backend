"""Build a synthetic ``price_history_training`` CSV.

ShetMitra Agent 4 — Historical Data Agent.

This script generates a deterministic synthetic weekly price + arrival series
for two commodity/mandi pairs covered by the ARIMAX price model:

    Dry_Grapes  @ Tasgaon
    Pomegranate @ Solapur

The output is written to ``data/price_history_training_synthetic.csv`` and
covers the Monday-anchored weeks from 2015-01-05 through 2026-04-27.

Key design choices (per the SDD and Agent 4 brief):

* Weekly Monday anchor (``arrival_date``).
* Seasonal sinusoid price drift around a base of Rs. 110/kg.
* A pronounced 2025 grape spike (Jan-May 2025) that lifts Dry_Grapes prices
  to roughly Rs. 280-320/kg — this is the out-of-distribution event the
  baseline ARIMAX model fails to predict.
* Arrival volumes follow a Gaussian bell curve around each season's peak
  week (Apr 14 for grapes, Feb 5-7 for pomegranate) with a per-commodity
  base and small i.i.d. noise.
* Lag / rolling / YoY engineered columns are computed in-script so the
  output is immediately usable for model training without further joins.
* AMED columns (``amed_belt_volume_mt``, ``amed_fields_harvesting``,
  ``amed_health_pct_good``, ``amed_season_week``) are left blank — they
  are backfilled by ``scripts.load_amed_history``.

All randomness uses ``numpy.random.default_rng(42)`` for full determinism.

Run::

    python scripts/build_synthetic_price_history.py
    python scripts/build_synthetic_price_history.py --output custom_path.csv
"""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Weekly anchor: first Monday on or after 2015-01-05 (which is itself a Monday).
START_DATE = date(2015, 1, 5)
END_DATE = date(2026, 4, 27)

# AMED columns expected in the backfill stage. We emit them as NaN so the
# loader script can populate them in place.
AMED_COLUMNS = (
    "amed_belt_volume_mt",
    "amed_fields_harvesting",
    "amed_health_pct_good",
    "amed_season_week",
)

# Per-commodity tuning. ``mandi`` matches the SDD's wholesale-market name.
# ``peak_week`` is the calendar (month, day) of the centre of the bell-curve
# harvest-arrival distribution. ``arrival_sigma_weeks`` controls how broad
# the arrival window is. ``arrival_peak_mt`` is the bell-curve amplitude.
@dataclass(frozen=True)
class _Commodity:
    name: str
    mandi: str
    peak_month: int
    peak_day: int
    arrival_peak_mt: float
    arrival_sigma_weeks: float
    base_price: float
    price_amplitude: float
    yoy_drift: float  # year-over-year base-price drift (Rs. per year)


_COMMODITIES: tuple[_Commodity, ...] = (
    # Grapes peak harvest mid-April (consistent with amed_history Tasgaon belt).
    _Commodity(
        name="Dry_Grapes",
        mandi="Tasgaon",
        peak_month=4,
        peak_day=14,
        arrival_peak_mt=85.0,
        arrival_sigma_weeks=4.0,
        base_price=110.0,
        price_amplitude=25.0,
        yoy_drift=2.0,
    ),
    # Pomegranate peak harvest early February.
    _Commodity(
        name="Pomegranate",
        mandi="Solapur",
        peak_month=2,
        peak_day=5,
        arrival_peak_mt=55.0,
        arrival_sigma_weeks=5.0,
        base_price=110.0,
        price_amplitude=18.0,
        yoy_drift=1.5,
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weekly_mondays(start: date, end: date) -> list[date]:
    """Return every Monday in ``[start, end]``."""
    if start.weekday() != 0:
        # Snap forward to the next Monday — for our anchors this is a no-op.
        start = start + timedelta(days=(7 - start.weekday()) % 7)
    weeks: list[date] = []
    cursor = start
    while cursor <= end:
        weeks.append(cursor)
        cursor = cursor + timedelta(days=7)
    return weeks


def _gaussian_week_weight(d: date, peak_month: int, peak_day: int, sigma_weeks: float) -> float:
    """Return a Gaussian weight centred on the year's peak harvest week.

    The week-offset is computed against the closest peak occurrence (either
    this calendar year or the adjacent one) so weeks on the boundary still get
    sensible tail weights.
    """
    candidates = [date(d.year - 1, peak_month, peak_day),
                  date(d.year, peak_month, peak_day),
                  date(d.year + 1, peak_month, peak_day)]
    nearest = min(candidates, key=lambda p: abs((d - p).days))
    week_offset = (d - nearest).days / 7.0
    return math.exp(-(week_offset ** 2) / (2.0 * sigma_weeks ** 2))


def _season_week_number(d: date, peak_month: int, peak_day: int, sigma_weeks: float) -> int:
    """Return a 1-based week index inside the active season (else 0).

    Active season = the band [peak - 3*sigma, peak + 3*sigma] weeks around the
    nearest peak. Week 1 = the first week of that band.
    """
    candidates = [date(d.year - 1, peak_month, peak_day),
                  date(d.year, peak_month, peak_day),
                  date(d.year + 1, peak_month, peak_day)]
    nearest = min(candidates, key=lambda p: abs((d - p).days))
    week_offset = (d - nearest).days / 7.0
    half_band = 3.0 * sigma_weeks
    if abs(week_offset) > half_band:
        return 0
    return int(round(week_offset + half_band)) + 1


def _is_grape_spike(d: date) -> bool:
    """2025 Dry Grapes price spike — Jan through May 2025."""
    return date(2025, 1, 1) <= d <= date(2025, 5, 31)


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def _generate_for_commodity(
    commodity: _Commodity,
    mondays: list[date],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate one commodity's weekly row set with raw price + arrivals.

    Lag / rolling / YoY columns are computed in a follow-up pass that runs on
    the per-commodity frame so the lookups stay simple and exact.
    """
    rows: list[dict[str, object]] = []
    for d in mondays:
        # ---- Seasonal sinusoid price drift ----
        # Phase the sinusoid so its max coincides with the peak harvest month.
        # (Counter-intuitive economically, but it gives us a smooth ~12 month
        # cycle with the magnitudes the SDD expects.)
        day_of_year = (d - date(d.year, 1, 1)).days
        peak_doy = (date(d.year, commodity.peak_month, commodity.peak_day)
                    - date(d.year, 1, 1)).days
        phase = 2.0 * math.pi * (day_of_year - peak_doy) / 365.25
        seasonal = commodity.price_amplitude * math.cos(phase)

        # ---- Year-over-year drift ----
        years_since_2015 = d.year - 2015 + (day_of_year / 365.25)
        drift = commodity.yoy_drift * years_since_2015

        # ---- Noise ----
        noise = float(rng.normal(loc=0.0, scale=4.0))

        price = commodity.base_price + seasonal + drift + noise

        # ---- 2025 grape spike ----
        if commodity.name == "Dry_Grapes" and _is_grape_spike(d):
            # Target Rs. 280-320 in the spike window.
            spike_centre = date(2025, 3, 15)
            spike_sigma_days = 45.0
            spike_offset_days = (d - spike_centre).days
            spike_weight = math.exp(-(spike_offset_days ** 2)
                                    / (2.0 * spike_sigma_days ** 2))
            # Lift baseline towards Rs. 300 +/- noise.
            target = 300.0 + float(rng.normal(loc=0.0, scale=8.0))
            price = price * (1.0 - spike_weight) + target * spike_weight

        # ---- Arrivals (Gaussian bell curve around peak) ----
        bell = _gaussian_week_weight(
            d, commodity.peak_month, commodity.peak_day,
            commodity.arrival_sigma_weeks,
        )
        arrivals_base = 15.0
        arrivals_peak = commodity.arrival_peak_mt
        arrivals_noise = float(rng.normal(loc=0.0, scale=2.0))
        arrivals = arrivals_base + arrivals_peak * bell + arrivals_noise
        arrivals = max(0.0, arrivals)

        season_week = _season_week_number(
            d, commodity.peak_month, commodity.peak_day,
            commodity.arrival_sigma_weeks,
        )

        rows.append({
            "arrival_date": d.isoformat(),
            "commodity": commodity.name,
            "mandi": commodity.mandi,
            "price_modal_kg": round(price, 2),
            "arrivals_mt": round(arrivals, 2),
            "season_week": season_week,
            "month": d.month,
            "year": d.year,
        })

    df = pd.DataFrame(rows)

    # ---- Lag and rolling-window features (per commodity) ----
    df["price_lag_1"] = df["price_modal_kg"].shift(1)
    df["price_lag_7"] = df["price_modal_kg"].shift(7)
    df["price_lag_14"] = df["price_modal_kg"].shift(14)
    df["arrivals_lag_1"] = df["arrivals_mt"].shift(1)
    df["arrivals_7day_avg"] = df["arrivals_mt"].rolling(window=7,
                                                       min_periods=1).mean()

    # ---- Year-over-year features ----
    # 52-week lag is a reasonable weekly YoY proxy.
    df["price_yoy"] = df["price_modal_kg"] - df["price_modal_kg"].shift(52)
    df["arrivals_yoy"] = df["arrivals_mt"] - df["arrivals_mt"].shift(52)

    # ---- Empty AMED placeholders ----
    for col in AMED_COLUMNS:
        df[col] = np.nan

    return df


def build_dataframe(seed: int = 42) -> pd.DataFrame:
    """Build the full synthetic price-history DataFrame."""
    rng = np.random.default_rng(seed)
    mondays = _weekly_mondays(START_DATE, END_DATE)
    frames = [_generate_for_commodity(c, mondays, rng) for c in _COMMODITIES]
    df = pd.concat(frames, ignore_index=True)

    column_order = [
        "arrival_date", "commodity", "mandi",
        "price_modal_kg", "arrivals_mt",
        "price_lag_1", "price_lag_7", "price_lag_14",
        "arrivals_lag_1", "arrivals_7day_avg",
        "season_week", "month", "year",
        "price_yoy", "arrivals_yoy",
        *AMED_COLUMNS,
    ]
    return df[column_order]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_output_path() -> str:
    return os.path.join("data", "price_history_training_synthetic.csv")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Generate the deterministic synthetic "
                     "price_history_training CSV used by Agent 4 / Agent 5."),
    )
    parser.add_argument(
        "--output",
        default=_default_output_path(),
        help=("Path to write the CSV to. "
              "Defaults to data/price_history_training_synthetic.csv."),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for numpy.random.default_rng (default: 42).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    df = build_dataframe(seed=args.seed)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".",
                exist_ok=True)
    df.to_csv(args.output, index=False)

    print("=" * 60)
    print("Synthetic price_history_training CSV written")
    print("=" * 60)
    print(f"Path:       {args.output}")
    print(f"Total rows: {len(df):,}")
    print(f"Date range: {df['arrival_date'].min()} -> {df['arrival_date'].max()}")
    for commodity in df["commodity"].unique():
        sub = df[df["commodity"] == commodity]
        mean_price = sub["price_modal_kg"].mean()
        mean_arrivals = sub["arrivals_mt"].mean()
        print(f"  {commodity:11s} rows={len(sub):4d}  "
              f"mean_price={mean_price:7.2f}  mean_arrivals={mean_arrivals:6.2f}")

    spike_mask = (
        (df["commodity"] == "Dry_Grapes")
        & (df["arrival_date"] >= "2025-01-01")
        & (df["arrival_date"] <= "2025-05-31")
    )
    spike_rows = int(spike_mask.sum())
    spike_mean = float(df.loc[spike_mask, "price_modal_kg"].mean())
    print(f"  2025 Dry_Grapes spike rows: {spike_rows}  "
          f"mean_price={spike_mean:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
