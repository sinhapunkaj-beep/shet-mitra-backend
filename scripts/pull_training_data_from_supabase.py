"""Pull price_history_training from live Supabase into the trainer CSV shape.

Output: data/price_history_training_backfilled.csv  (so train_price_model.py
picks it up without flag changes).

Column mapping:
  date         -> arrival_date
  modal_price  -> price_modal_kg
  market_name  -> mandi
  arrivals_mt  -> (unchanged)
  ...lag/season/AMED columns pass through.

Computed:
  price_lag_14         -> derived per (commodity, mandi) ordered by date
  amed_season_timing_dev -> 0.0 (placeholder until AMED history backfill lands)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import dotenv_values

NANO = Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env")
OUT = Path(__file__).resolve().parent.parent / "data" / "price_history_training_backfilled.csv"


def main() -> int:
    creds = dotenv_values(NANO)
    pw = creds.get("SUPABASE_DB_PASSWORD")
    if not pw:
        sys.exit("FATAL: SUPABASE_DB_PASSWORD missing from nano.env")

    print(f"[pull] connecting to live Supabase TEST")
    conn = psycopg2.connect(
        host="db.euydubpywdsettjywkms.supabase.co",
        port=5432, dbname="postgres", user="postgres", password=pw,
        sslmode="require", connect_timeout=20,
    )
    sql = """
        SELECT
            date AS arrival_date,
            commodity,
            market_name AS mandi,
            modal_price AS price_modal_kg,
            arrivals_mt,
            price_lag_1, price_lag_7, arrivals_lag_1,
            COALESCE(price_7day_avg, 0) AS price_7day_avg,
            arrivals_7day_avg,
            season_week, month, year,
            price_yoy, arrivals_yoy,
            amed_belt_volume_mt, amed_fields_harvesting,
            amed_health_pct_good, amed_season_week,
            variety
        FROM price_history_training
        WHERE commodity IN ('Dry Grapes','Pomegranate')
        ORDER BY commodity, mandi, arrival_date
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    conn.close()
    print(f"[pull] fetched {len(rows)} rows")

    # Compute price_lag_14 per (commodity, mandi) — order is already by date.
    last14: dict[tuple[str, str], list[float]] = {}
    # AMED forecast lookahead: lag1 = next-week forecast, lag2 = week-after
    # (per SDD 5.3 - these are the leading indicators that improve short-term
    # accuracy). Implemented as shift(-1) / shift(-2) over the in-order series.
    belt_buf: dict[tuple[str, str], list] = {}
    # Year-over-year same-week-prior-year price for seasonal commodities.
    yoy_buf: dict[tuple[str, str, int, int], float] = {}

    # First pass: collect & basic lags.
    for r in rows:
        key = (r["commodity"], r["mandi"])
        buf = last14.setdefault(key, [])
        r["price_lag_14"] = buf[-14] if len(buf) >= 14 else r.get("price_lag_7") or r["price_modal_kg"]
        buf.append(float(r["price_modal_kg"]) if r["price_modal_kg"] is not None else 0.0)
        r["amed_season_timing_dev"] = 0.0
        if r["commodity"] == "Dry Grapes":
            r["commodity"] = "Dry_Grapes"
        # Index for YoY lookup by (commodity, mandi, year, isoweek)
        dt = r["arrival_date"]
        if dt is not None:
            iso_year, iso_week = dt.isocalendar()[:2]
            yoy_buf[(r["commodity"], r["mandi"], iso_year, iso_week)] = (
                float(r["price_modal_kg"]) if r["price_modal_kg"] is not None else 0.0
            )

    # Second pass: AMED forecast lag1 / lag2 (look-ahead) and prev-year-same-week.
    by_key: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        by_key.setdefault((r["commodity"], r["mandi"]), []).append(i)
    for idxs in by_key.values():
        for pos, i in enumerate(idxs):
            r = rows[i]
            nxt1 = rows[idxs[pos + 1]] if pos + 1 < len(idxs) else None
            nxt2 = rows[idxs[pos + 2]] if pos + 2 < len(idxs) else None
            r["amed_belt_volume_mt_lag1"] = (
                nxt1.get("amed_belt_volume_mt") if nxt1 else None
            )
            r["amed_belt_volume_mt_lag2"] = (
                nxt2.get("amed_belt_volume_mt") if nxt2 else None
            )
            dt = r["arrival_date"]
            if dt is not None:
                iso_year, iso_week = dt.isocalendar()[:2]
                r["prev_year_same_week_price"] = yoy_buf.get(
                    (r["commodity"], r["mandi"], iso_year - 1, iso_week)
                )
            else:
                r["prev_year_same_week_price"] = None

    OUT.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "arrival_date", "commodity", "mandi", "price_modal_kg", "arrivals_mt",
        "price_lag_1", "price_lag_7", "price_lag_14",
        "arrivals_lag_1", "price_7day_avg", "arrivals_7day_avg",
        "season_week", "month", "year", "price_yoy", "arrivals_yoy",
        "amed_belt_volume_mt", "amed_belt_volume_mt_lag1",
        "amed_belt_volume_mt_lag2",
        "amed_fields_harvesting",
        "amed_health_pct_good", "amed_season_week",
        "amed_season_timing_dev",
        "prev_year_same_week_price",
        "variety",
    ]
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})

    by_commodity: dict[str, int] = {}
    for r in rows:
        by_commodity[r["commodity"]] = by_commodity.get(r["commodity"], 0) + 1
    print(f"[pull] wrote -> {OUT}")
    for k, v in by_commodity.items():
        print(f"  {k}: {v} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
