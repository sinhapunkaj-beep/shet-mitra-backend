"""Load AMED 3-year historical belt data and backfill price_history_training.

ShetMitra Agent 4 — Historical Data Agent.

Three things this script does (matching SDD Section 4.4-Agent-4 and Section
5.2):

* **Task 1.** Pull belt history (3 seasons) from the AMED client and upsert
  into ``amed_history`` in the local SQLite DB at ``data/test.db``. Falls
  back to inline mock numbers from the SDD if ``geo.amed_client`` is not yet
  importable (Agent 1 may still be building it). Also prints the equivalent
  PostgreSQL DDL hints so the same payload can later be applied to Supabase.

* **Task 2.** Read the synthetic ``price_history_training`` CSV and backfill
  the new AMED columns (``amed_belt_volume_mt``, ``amed_fields_harvesting``,
  ``amed_health_pct_good``, ``amed_season_week``) using a bell-curve weekly
  distribution of each season's total volume around its peak harvest week
  (see ``_distribute_season_volume`` for the exact math).

* **Task 3.** Print a backfill report — row count, date range, and the
  share of rows fully populated with all four AMED features.

Run::

    python scripts/load_amed_history.py
    python scripts/load_amed_history.py \
        --input  data/price_history_training_synthetic.csv \
        --output data/price_history_training_backfilled.csv \
        --db     data/test.db

All randomness is deterministic. No live Postgres or AMED keys required.
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

# Ensure the project root is importable so ``geo.amed_client`` resolves when
# this script is run directly (``python scripts/load_amed_history.py``).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Constants and configuration
# ---------------------------------------------------------------------------

# Tasgaon / Sangli belt bounding box — SDD Section 2.2.
TASGAON_BBOX: dict[str, float] = {
    "north": 17.2,
    "south": 16.8,
    "east": 74.8,
    "west": 74.3,
}

# Region key used in amed_history rows.
REGION = "Tasgaon_Sangli_belt"

# Default health distribution (SDD Section 2.4) — used both as the default
# value when a season row does not carry its own health field, and as the
# value backfilled into ``amed_health_pct_good``.
DEFAULT_HEALTH_PCT_GOOD = 0.63

# Average farm yield (MT/field) per crop. Used to convert weekly bell-curve
# volume into a count of fields actively harvesting in that week:
#
#     fields = round(weekly_volume_mt / (total_volume_mt / fields_estimate))
#
# These match the SDD's belt-level mock (2,847 grape fields / ~8,200 acres).
AVG_FIELD_ESTIMATE: dict[str, int] = {
    "Grapes": 1500,
    "Pomegranate": 800,
}

# Map from the synthetic CSV's ``commodity`` column to the AMED ``crop_type``
# column. ``Dry_Grapes`` and ``Grapes`` are the same belt for this purpose.
COMMODITY_TO_CROP: dict[str, str] = {
    "Dry_Grapes": "Grapes",
    "Pomegranate": "Pomegranate",
}

# Crops we know how to backfill (i.e. crops with seeded amed_history).
SUPPORTED_AMED_CROPS: tuple[str, ...] = ("Grapes", "Pomegranate")

# The three seasons we always request.
DEFAULT_SEASONS: tuple[str, ...] = ("2022-23", "2023-24", "2024-25")


# ---------------------------------------------------------------------------
# Inline fallback data — used when geo.amed_client is not importable.
# Values come straight from the SDD (Section 7, Agent 2 brief).
# ---------------------------------------------------------------------------

INLINE_HISTORY_FALLBACK: dict[str, list[dict[str, Any]]] = {
    "Grapes": [
        {
            "season": "2022-23",
            "total_area_acres": 7890,
            "harvest_start_date": "2023-04-01",
            "harvest_peak_date": "2023-04-21",
            "harvest_end_date": "2023-05-15",
            "estimated_total_volume_mt": 11420,
            "avg_price_modal_kg": 118.50,
        },
        {
            "season": "2023-24",
            "total_area_acres": 8012,
            "harvest_start_date": "2024-04-05",
            "harvest_peak_date": "2024-04-19",
            "harvest_end_date": "2024-05-10",
            "estimated_total_volume_mt": 11680,
            "avg_price_modal_kg": 134.20,
        },
        {
            "season": "2024-25",
            "total_area_acres": 8180,
            "harvest_start_date": "2025-03-28",
            "harvest_peak_date": "2025-04-14",
            "harvest_end_date": "2025-05-08",
            "estimated_total_volume_mt": 11890,
            "avg_price_modal_kg": 298.40,
        },
    ],
    "Pomegranate": [
        {
            "season": "2022-23",
            "total_area_acres": 4050,
            "harvest_start_date": "2023-01-15",
            "harvest_peak_date": "2023-02-08",
            "harvest_end_date": "2023-03-05",
            "estimated_total_volume_mt": 5980,
            "avg_price_modal_kg": 86.40,
        },
        {
            "season": "2023-24",
            "total_area_acres": 4140,
            "harvest_start_date": "2024-01-14",
            "harvest_peak_date": "2024-02-07",
            "harvest_end_date": "2024-03-04",
            "estimated_total_volume_mt": 6120,
            "avg_price_modal_kg": 92.10,
        },
        {
            "season": "2024-25",
            "total_area_acres": 4218,
            "harvest_start_date": "2025-01-13",
            "harvest_peak_date": "2025-02-05",
            "harvest_end_date": "2025-03-03",
            "estimated_total_volume_mt": 6240,
            "avg_price_modal_kg": 104.80,
        },
    ],
}


# ---------------------------------------------------------------------------
# AMED client adapter
# ---------------------------------------------------------------------------

@dataclass
class HistoryRow:
    """A normalised amed_history row ready for SQLite / Postgres upsert."""
    region: str
    season_label: str
    season_year_start: int
    crop_type: str
    total_area_acres: float
    harvest_start_date: date
    harvest_peak_date: date
    harvest_end_date: date
    estimated_total_volume_mt: float
    avg_price_modal_kg: float | None


def _import_amed_client() -> Any | None:
    """Return ``AMEDClient`` if Agent 1's module is importable; else None.

    The script must run standalone even before Agent 1 finishes building
    ``geo/amed_client.py``. In that case we fall back to ``geo.amed_mock``
    directly, and finally to the inline SDD numbers.
    """
    try:
        from geo.amed_client import AMEDClient  # type: ignore
        return AMEDClient
    except Exception:
        return None


def _import_amed_mock() -> Any | None:
    try:
        from geo import amed_mock  # type: ignore
        return amed_mock
    except Exception:
        return None


def _normalise_history_row(
    raw: dict[str, Any],
    crop_type: str,
    region: str,
) -> HistoryRow:
    """Coerce one AMED history payload into a HistoryRow.

    Handles both the inline-fallback dict shape *and* the amed_mock dict
    shape (where the season carries ``peak_harvest_week`` and
    ``avg_harvest_date`` strings instead of explicit start/peak/end dates).
    """
    season_label = raw["season"]
    season_year_start = int(season_label.split("-")[0])

    # peak / start / end resolution.
    if "harvest_start_date" in raw and "harvest_end_date" in raw:
        start = date.fromisoformat(raw["harvest_start_date"])
        end = date.fromisoformat(raw["harvest_end_date"])
        peak = date.fromisoformat(
            raw.get("harvest_peak_date") or raw.get("avg_harvest_date"))
    else:
        # amed_mock shape — derive from peak_harvest_week + avg_harvest_date.
        peak = date.fromisoformat(raw["avg_harvest_date"])
        start_str, end_str = raw["peak_harvest_week"].split("/")
        # peak_harvest_week is only the peak week itself; broaden to a full
        # ~6-week harvest window centred on the peak (3 weeks either side).
        start = date.fromisoformat(start_str) - timedelta(days=21)
        end = date.fromisoformat(end_str) + timedelta(days=21)

    total_area = float(raw.get("total_area_acres", 0))
    total_volume = float(raw.get("estimated_total_volume_mt", 0))
    avg_price = raw.get("avg_price_modal_kg")
    if avg_price is not None:
        avg_price = float(avg_price)

    return HistoryRow(
        region=region,
        season_label=season_label,
        season_year_start=season_year_start,
        crop_type=crop_type,
        total_area_acres=total_area,
        harvest_start_date=start,
        harvest_peak_date=peak,
        harvest_end_date=end,
        estimated_total_volume_mt=total_volume,
        avg_price_modal_kg=avg_price,
    )


def fetch_history(
    crop_type: str,
    seasons: Iterable[str] = DEFAULT_SEASONS,
    region: str = REGION,
    bbox: dict[str, float] | None = None,
) -> list[HistoryRow]:
    """Return amed_history rows for one crop, choosing the best source.

    Resolution order:
      1. ``geo.amed_client.AMEDClient().get_historical_data(...)``
      2. ``geo.amed_mock.get_historical_data(...)``
      3. Inline fallback dict.
    """
    bbox = bbox or TASGAON_BBOX
    seasons_list = list(seasons)

    # 1. Agent 1's AMEDClient, if available.
    client_cls = _import_amed_client()
    if client_cls is not None:
        try:
            client = client_cls()
            raw_rows = client.get_historical_data(
                bbox=bbox, crop_type=crop_type, seasons=seasons_list,
            )
            if raw_rows:
                return [
                    _normalise_history_row(row, crop_type=crop_type,
                                           region=region)
                    for row in raw_rows
                ]
        except Exception as exc:  # noqa: BLE001 — broad on purpose.
            print(f"[warn] AMEDClient.get_historical_data failed: {exc!r}. "
                  "Falling back to amed_mock.", file=sys.stderr)

    # 2. amed_mock module (still in geo/).
    mock = _import_amed_mock()
    if mock is not None:
        try:
            raw_rows = mock.get_historical_data(
                bbox=bbox, crop_type=crop_type, seasons=seasons_list,
            )
            if raw_rows:
                return [
                    _normalise_history_row(row, crop_type=crop_type,
                                           region=region)
                    for row in raw_rows
                ]
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] amed_mock.get_historical_data failed: {exc!r}. "
                  "Falling back to inline SDD numbers.", file=sys.stderr)

    # 3. Inline fallback.
    print(f"[warn] Using inline INLINE_HISTORY_FALLBACK for crop={crop_type}. "
          "geo.amed_client + geo.amed_mock both unavailable.",
          file=sys.stderr)
    fallback = INLINE_HISTORY_FALLBACK.get(crop_type, [])
    wanted = set(seasons_list)
    return [
        _normalise_history_row(row, crop_type=crop_type, region=region)
        for row in fallback if row["season"] in wanted
    ]


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

AMED_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS amed_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region TEXT NOT NULL,
    season_label TEXT NOT NULL,
    season_year_start INTEGER,
    crop_type TEXT,
    total_area_acres REAL,
    harvest_start_date TEXT,
    harvest_peak_date TEXT,
    harvest_end_date TEXT,
    estimated_total_volume_mt REAL,
    avg_price_modal_kg REAL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (region, season_label, crop_type)
);
""".strip()

POSTGRES_DDL_HINT = """
-- Postgres equivalent (run against Supabase):
CREATE TABLE IF NOT EXISTS amed_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region text NOT NULL,
    season_label text NOT NULL,
    season_year_start integer,
    crop_type text,
    total_area_acres numeric,
    harvest_start_date date,
    harvest_peak_date date,
    harvest_end_date date,
    estimated_total_volume_mt numeric,
    avg_price_modal_kg numeric,
    created_at timestamptz DEFAULT now(),
    UNIQUE (region, season_label, crop_type)
);
""".strip()


def _ensure_amed_history_table(conn: sqlite3.Connection) -> None:
    conn.execute(AMED_HISTORY_DDL)
    conn.commit()


def upsert_history_rows(
    db_path: str,
    rows: list[HistoryRow],
) -> int:
    """Upsert ``rows`` into ``amed_history`` and return the count inserted/updated."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        _ensure_amed_history_table(conn)
        sql = """
            INSERT INTO amed_history (
                region, season_label, season_year_start, crop_type,
                total_area_acres, harvest_start_date, harvest_peak_date,
                harvest_end_date, estimated_total_volume_mt,
                avg_price_modal_kg
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (region, season_label, crop_type) DO UPDATE SET
                season_year_start = excluded.season_year_start,
                total_area_acres = excluded.total_area_acres,
                harvest_start_date = excluded.harvest_start_date,
                harvest_peak_date = excluded.harvest_peak_date,
                harvest_end_date = excluded.harvest_end_date,
                estimated_total_volume_mt = excluded.estimated_total_volume_mt,
                avg_price_modal_kg = excluded.avg_price_modal_kg
        """
        for row in rows:
            conn.execute(sql, (
                row.region, row.season_label, row.season_year_start,
                row.crop_type, row.total_area_acres,
                row.harvest_start_date.isoformat(),
                row.harvest_peak_date.isoformat(),
                row.harvest_end_date.isoformat(),
                row.estimated_total_volume_mt,
                row.avg_price_modal_kg,
            ))
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def load_history_from_db(db_path: str) -> list[HistoryRow]:
    """Read every amed_history row out of ``db_path`` back into HistoryRow objects."""
    conn = sqlite3.connect(db_path)
    try:
        _ensure_amed_history_table(conn)
        cur = conn.execute("""
            SELECT region, season_label, season_year_start, crop_type,
                   total_area_acres, harvest_start_date, harvest_peak_date,
                   harvest_end_date, estimated_total_volume_mt,
                   avg_price_modal_kg
            FROM amed_history
        """)
        rows: list[HistoryRow] = []
        for r in cur.fetchall():
            rows.append(HistoryRow(
                region=r[0],
                season_label=r[1],
                season_year_start=int(r[2]) if r[2] is not None else 0,
                crop_type=r[3],
                total_area_acres=float(r[4]) if r[4] is not None else 0.0,
                harvest_start_date=date.fromisoformat(r[5]),
                harvest_peak_date=date.fromisoformat(r[6]),
                harvest_end_date=date.fromisoformat(r[7]),
                estimated_total_volume_mt=float(r[8]) if r[8] is not None else 0.0,
                avg_price_modal_kg=float(r[9]) if r[9] is not None else None,
            ))
        return rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bell-curve weekly distribution
# ---------------------------------------------------------------------------

def _distribute_season_volume(row: HistoryRow) -> list[tuple[date, float]]:
    """Return a list of (week_start_monday, weekly_volume_mt) pairs.

    Uses a Gaussian bell curve weight per the SDD logic::

        harvest_weeks = (harvest_end_date - harvest_start_date).days // 7 + 1
        peak_offset   = (harvest_peak_date - harvest_start_date).days // 7
        sigma         = harvest_weeks / 4
        weight(i)     = exp(-((i - peak_offset) ** 2) / (2 * sigma ** 2))
        weights      /= sum(weights)
        volume[i]     = season_total_mt * weight(i)

    The week anchors are computed as Mondays starting from the Monday on or
    before ``harvest_start_date`` so they align with the synthetic CSV's
    weekly Monday anchor scheme.
    """
    span_days = (row.harvest_end_date - row.harvest_start_date).days
    harvest_weeks = max(1, span_days // 7 + 1)
    peak_offset = max(0, min(harvest_weeks - 1,
                             (row.harvest_peak_date
                              - row.harvest_start_date).days // 7))
    sigma = max(0.5, harvest_weeks / 4.0)

    weights = np.array([
        math.exp(-((i - peak_offset) ** 2) / (2.0 * sigma ** 2))
        for i in range(harvest_weeks)
    ])
    weights = weights / weights.sum()

    # Snap harvest_start to the Monday on or before it.
    first_monday = row.harvest_start_date - timedelta(
        days=row.harvest_start_date.weekday()
    )

    pairs: list[tuple[date, float]] = []
    for i in range(harvest_weeks):
        week_start = first_monday + timedelta(days=7 * i)
        weekly_volume = float(row.estimated_total_volume_mt * weights[i])
        pairs.append((week_start, weekly_volume))
    return pairs


def build_weekly_volume_index(
    history_rows: list[HistoryRow],
) -> dict[tuple[str, str], dict[date, dict[str, float]]]:
    """Build ``{(commodity, mandi): {week_start: {fields,volume,week_idx}}}``.

    Each value is the per-week dictionary that ``backfill_dataframe`` will
    paste into the AMED columns. ``week_idx`` is 1-based within the season.
    """
    index: dict[tuple[str, str], dict[date, dict[str, float]]] = {}

    for row in history_rows:
        if row.crop_type not in SUPPORTED_AMED_CROPS:
            continue

        # Reverse-map crop_type -> commodity used in CSV (Grapes ->
        # Dry_Grapes for Tasgaon; Pomegranate stays Pomegranate for Solapur).
        if row.crop_type == "Grapes":
            csv_commodity, mandi = "Dry_Grapes", "Tasgaon"
        else:
            csv_commodity, mandi = "Pomegranate", "Solapur"

        key = (csv_commodity, mandi)
        bucket = index.setdefault(key, {})

        fields_estimate = AVG_FIELD_ESTIMATE.get(row.crop_type, 1000)
        avg_per_field_mt = (row.estimated_total_volume_mt
                            / max(1, fields_estimate))

        for week_idx, (week_start, weekly_volume) in enumerate(
                _distribute_season_volume(row), start=1):
            fields_harvesting = (round(weekly_volume / avg_per_field_mt)
                                 if avg_per_field_mt > 0 else 0)
            bucket[week_start] = {
                "amed_belt_volume_mt": round(weekly_volume, 3),
                "amed_fields_harvesting": int(fields_harvesting),
                "amed_health_pct_good": DEFAULT_HEALTH_PCT_GOOD,
                "amed_season_week": int(week_idx),
            }

    return index


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def backfill_dataframe(
    df: pd.DataFrame,
    volume_index: dict[tuple[str, str], dict[date, dict[str, float]]],
) -> pd.DataFrame:
    """Return a copy of ``df`` with AMED columns populated where matched.

    For rows that fall outside any known season we set off-season defaults::

        amed_belt_volume_mt    = 0.0
        amed_fields_harvesting = 0
        amed_health_pct_good   = NaN  (we genuinely don't know off-season)
        amed_season_week       = 0
    """
    df = df.copy()

    # Ensure column dtypes are float-friendly so .at writes don't downcast.
    for col in ("amed_belt_volume_mt", "amed_fields_harvesting",
                "amed_health_pct_good", "amed_season_week"):
        if col not in df.columns:
            df[col] = np.nan
        df[col] = df[col].astype("float64")

    # Default off-season values applied to every row up front; in-season
    # rows then overwrite them.
    df["amed_belt_volume_mt"] = 0.0
    df["amed_fields_harvesting"] = 0
    df["amed_health_pct_good"] = np.nan
    df["amed_season_week"] = 0

    # Build a fast lookup: (commodity, mandi, week_start_date) -> payload.
    flat: dict[tuple[str, str, date], dict[str, float]] = {}
    for (commodity, mandi), bucket in volume_index.items():
        for week_start, payload in bucket.items():
            flat[(commodity, mandi, week_start)] = payload

    # Apply per row.
    for idx, csv_row in df.iterrows():
        try:
            arrival = date.fromisoformat(str(csv_row["arrival_date"]))
        except ValueError:
            continue
        key = (csv_row["commodity"], csv_row["mandi"], arrival)
        payload = flat.get(key)
        if payload is None:
            continue
        df.at[idx, "amed_belt_volume_mt"] = payload["amed_belt_volume_mt"]
        df.at[idx, "amed_fields_harvesting"] = payload["amed_fields_harvesting"]
        df.at[idx, "amed_health_pct_good"] = payload["amed_health_pct_good"]
        df.at[idx, "amed_season_week"] = payload["amed_season_week"]

    return df


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_backfill_report(df: pd.DataFrame) -> None:
    total = len(df)
    in_season_mask = df["amed_season_week"].fillna(0).astype(int) > 0
    in_season = int(in_season_mask.sum())
    fully_populated = (in_season_mask
                       & df["amed_belt_volume_mt"].notna()
                       & df["amed_fields_harvesting"].notna()
                       & df["amed_health_pct_good"].notna()
                       & df["amed_season_week"].notna())
    fully_populated_n = int(fully_populated.sum())

    pct_in_season = (100.0 * in_season / total) if total else 0.0
    pct_full = (100.0 * fully_populated_n / total) if total else 0.0

    print("=" * 60)
    print("Backfill report")
    print("=" * 60)
    print(f"Total rows:             {total:,}")
    print(f"Date range:             {df['arrival_date'].min()} -> "
          f"{df['arrival_date'].max()}")
    print(f"Rows inside an AMED season:        "
          f"{in_season:,} ({pct_in_season:5.2f}%)")
    print(f"Rows with all 4 AMED features set: "
          f"{fully_populated_n:,} ({pct_full:5.2f}%)")
    for commodity in df["commodity"].unique():
        sub = df[df["commodity"] == commodity]
        sub_in = sub["amed_season_week"].fillna(0).astype(int) > 0
        in_n = int(sub_in.sum())
        sub_vol_sum = float(sub.loc[sub_in, "amed_belt_volume_mt"].sum())
        print(f"  {commodity:11s}  rows={len(sub):4d}  "
              f"in_season={in_n:3d}  amed_volume_sum={sub_vol_sum:9.1f} MT")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_input_path() -> str:
    return os.path.join("data", "price_history_training_synthetic.csv")


def _default_output_path() -> str:
    return os.path.join("data", "price_history_training_backfilled.csv")


def _default_db_path() -> str:
    return os.path.join("data", "test.db")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Load AMED 3-year history into amed_history and backfill "
                     "the price_history_training CSV with AMED-derived "
                     "weekly volumes, fields-harvesting, health %, and "
                     "season week."),
    )
    parser.add_argument(
        "--input",
        default=_default_input_path(),
        help=("Synthetic price_history_training CSV produced by "
              "scripts/build_synthetic_price_history.py. Default: "
              "data/price_history_training_synthetic.csv"),
    )
    parser.add_argument(
        "--output",
        default=_default_output_path(),
        help=("Where to write the backfilled CSV. Default: "
              "data/price_history_training_backfilled.csv"),
    )
    parser.add_argument(
        "--db",
        default=_default_db_path(),
        help=("Path to the local SQLite test DB. Created if missing. "
              "Default: data/test.db"),
    )
    parser.add_argument(
        "--skip-postgres-hint",
        action="store_true",
        help="Suppress the PostgreSQL DDL hint print-out.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    # ---- Task 1: fetch + upsert amed_history ----
    print("[1/3] Loading AMED 3-year belt history ...")
    all_rows: list[HistoryRow] = []
    for crop in SUPPORTED_AMED_CROPS:
        rows = fetch_history(crop_type=crop, seasons=DEFAULT_SEASONS,
                             region=REGION, bbox=TASGAON_BBOX)
        print(f"  fetched {len(rows)} seasons for crop={crop}")
        all_rows.extend(rows)
    inserted = upsert_history_rows(args.db, all_rows)
    print(f"  upserted {inserted} amed_history rows into {args.db}")
    if not args.skip_postgres_hint:
        print()
        print(POSTGRES_DDL_HINT)
        print()

    # ---- Task 2: backfill price_history_training CSV ----
    print("[2/3] Backfilling price_history_training ...")
    if not os.path.exists(args.input):
        print(f"[error] Input CSV not found: {args.input}. "
              "Run scripts/build_synthetic_price_history.py first.",
              file=sys.stderr)
        return 2

    df = pd.read_csv(args.input)
    history_rows = load_history_from_db(args.db)
    volume_index = build_weekly_volume_index(history_rows)
    print(f"  built weekly volume index over "
          f"{sum(len(v) for v in volume_index.values())} week-slots "
          f"across {len(volume_index)} (commodity, mandi) pairs")

    df_backfilled = backfill_dataframe(df, volume_index)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".",
                exist_ok=True)
    df_backfilled.to_csv(args.output, index=False)
    print(f"  wrote backfilled CSV -> {args.output}")

    # ---- Task 3: report ----
    print("[3/3] Reporting ...")
    _print_backfill_report(df_backfilled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
