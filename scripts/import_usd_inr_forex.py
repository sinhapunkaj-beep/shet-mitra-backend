"""Import (or synthesise) USD/INR daily forex rates.

ShetMitra Mango Agent 2 - USD/INR Forex Importer.

This script produces a daily USD/INR rate series used as the
``export_demand_proxy`` exogenous variable for the Alphonso export-price
ARIMAX model (Mango SDD Section 4.3).

Modes:

* ``--live``  - hits https://api.frankfurter.app/{date}?from=USD&to=INR
  daily across the requested year range. Any network failure (DNS, HTTP,
  parse) is caught and we fall back to the synthetic generator with a
  warning so the script always emits a CSV.

* default     - deterministic synthetic sinusoidal trend that drifts
  from ~65 INR/USD (early 2015) up to ~84 INR/USD (late 2026) with a
  small daily noise term. numpy seed = 42.

Output CSV columns:
    date            ISO yyyy-mm-dd
    usd_inr_rate    float
    source          'synthetic' | 'frankfurter'

Run::

    python scripts/import_usd_inr_forex.py
    python scripts/import_usd_inr_forex.py --years 2015 2026
    python scripts/import_usd_inr_forex.py --live
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date, timedelta
from typing import Iterable

import numpy as np
import pandas as pd


FRANKFURTER_BASE = "https://api.frankfurter.app"


# ---------------------------------------------------------------------------
# Synthetic generator
# ---------------------------------------------------------------------------

def _daily_dates(year_start: int, year_end: int) -> list[date]:
    """Every calendar day from year_start-01-01 through year_end-12-31."""
    out: list[date] = []
    cursor = date(year_start, 1, 1)
    end = date(year_end, 12, 31)
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def build_synthetic_dataframe(
    year_start: int = 2015,
    year_end: int = 2026,
    seed: int = 42,
) -> pd.DataFrame:
    """Deterministic daily USD/INR synthetic series.

    Linear drift 65 -> 84 across the range, plus a slow seasonal wobble
    (+-1.5) and a small daily noise (sigma=0.25) so the series is
    monotone-ish but realistic enough to use as a feature.
    """
    rng = np.random.default_rng(seed)
    dates = _daily_dates(year_start, year_end)
    if not dates:
        return pd.DataFrame(columns=["date", "usd_inr_rate", "source"])
    total = len(dates) - 1 if len(dates) > 1 else 1
    rows: list[dict[str, object]] = []
    for idx, d in enumerate(dates):
        progress = idx / total
        baseline = 65.0 + (84.0 - 65.0) * progress
        day_of_year = (d - date(d.year, 1, 1)).days
        seasonal = 1.5 * math.sin(2.0 * math.pi * day_of_year / 365.25)
        noise = float(rng.normal(loc=0.0, scale=0.25))
        rate = baseline + seasonal + noise
        rows.append({
            "date": d.isoformat(),
            "usd_inr_rate": round(rate, 4),
            "source": "synthetic",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Frankfurter live fetch
# ---------------------------------------------------------------------------

def _fetch_frankfurter_daily(year_start: int, year_end: int) -> pd.DataFrame:
    """Day-by-day pull from frankfurter.app. Raises on any HTTP / parse failure."""
    import httpx  # local import - only required for --live.

    rows: list[dict[str, object]] = []
    with httpx.Client(timeout=15.0) as client:
        for d in _daily_dates(year_start, year_end):
            resp = client.get(
                f"{FRANKFURTER_BASE}/{d.isoformat()}",
                params={"from": "USD", "to": "INR"},
            )
            resp.raise_for_status()
            payload = resp.json()
            rate = payload.get("rates", {}).get("INR")
            if rate is None:
                continue
            rows.append({
                "date": d.isoformat(),
                "usd_inr_rate": float(rate),
                "source": "frankfurter",
            })
    return pd.DataFrame(rows)


def fetch_live_or_fallback(
    year_start: int,
    year_end: int,
    seed: int,
) -> tuple[pd.DataFrame, str]:
    """Try frankfurter; on any failure return synthetic. Returns (df, source)."""
    try:
        df = _fetch_frankfurter_daily(year_start, year_end)
    except Exception as exc:  # noqa: BLE001 - intentionally broad
        print(
            f"[warn] Frankfurter live fetch failed "
            f"({type(exc).__name__}: {exc!s}). Falling back to synthetic.",
            file=sys.stderr,
        )
        return build_synthetic_dataframe(year_start, year_end, seed), "synthetic"
    if df.empty:
        print(
            "[warn] Frankfurter returned no rows. Falling back to synthetic.",
            file=sys.stderr,
        )
        return build_synthetic_dataframe(year_start, year_end, seed), "synthetic"
    return df, "frankfurter"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_quality_report(df: pd.DataFrame, source: str) -> None:
    print("=" * 60)
    print(f"USD/INR forex summary (source={source})")
    print("=" * 60)
    print(f"Total rows:        {len(df):,}")
    if df.empty:
        return
    print(f"Date range:        {df['date'].min()} -> {df['date'].max()}")
    print(
        f"Rate range:        "
        f"{df['usd_inr_rate'].min():.4f} .. {df['usd_inr_rate'].max():.4f}"
    )
    df_yearly = df.copy()
    df_yearly["year"] = df_yearly["date"].str.slice(0, 4)
    yearly = df_yearly.groupby("year")["usd_inr_rate"].mean()
    print()
    print("Yearly mean USD/INR:")
    for year, mean_rate in yearly.items():
        print(f"  {year}  {mean_rate:7.3f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_output_path() -> str:
    return os.path.join("data", "forex_rates_synthetic.csv")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import (or synthesise) daily USD/INR forex rates used as the "
            "export_demand_proxy feature for the Alphonso ARIMAX price model."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Actually hit frankfurter.app for live rates. Default is OFF "
            "(synthetic). Any network failure falls back to synthetic."
        ),
    )
    parser.add_argument(
        "--output",
        default=_default_output_path(),
        help=(
            "Path to write the CSV to. "
            "Defaults to data/forex_rates_synthetic.csv."
        ),
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
        df, source = fetch_live_or_fallback(year_start, year_end, args.seed)
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
