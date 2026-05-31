"""Pull live mango rows from price_history_training into the local mango CSV
that scripts/train_mango_models.py reads (data/price_history_mango_synthetic.csv).

Column mapping (live -> trainer schema):
  date        -> arrival_date
  market_name -> mandi_name
  modal_price -> price_modal_kg
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import dotenv_values

NANO = Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env")
OUT = Path(__file__).resolve().parent.parent / "data" / "price_history_mango_synthetic.csv"


def main() -> int:
    creds = dotenv_values(NANO)
    pw = creds.get("SUPABASE_DB_PASSWORD")
    if not pw:
        sys.exit("FATAL: SUPABASE_DB_PASSWORD missing from nano.env")

    print("[connect] live Supabase TEST")
    conn = psycopg2.connect(
        host="db.euydubpywdsettjywkms.supabase.co",
        port=5432, dbname="postgres", user="postgres", password=pw,
        sslmode="require", connect_timeout=20,
    )
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                date         AS arrival_date,
                commodity,
                variety,
                market_name  AS mandi_name,
                modal_price  AS price_modal_kg,
                arrivals_mt,
                price_lag_1, price_lag_7,
                arrivals_lag_1, arrivals_7day_avg,
                season_week, month, year,
                price_yoy, arrivals_yoy,
                bearing_year_flag, export_demand_proxy, flowering_weather_score
            FROM price_history_training
            WHERE commodity = 'Mango'
            ORDER BY variety, mandi_name, arrival_date
        """)
        rows = cur.fetchall()
    conn.close()
    print(f"[pull] fetched {len(rows)} mango rows")

    # Add price_lag_14 derived per (variety, mandi_name).
    last14: dict[tuple, list[float]] = {}
    for r in rows:
        key = (r["variety"], r["mandi_name"])
        buf = last14.setdefault(key, [])
        r["price_lag_14"] = buf[-14] if len(buf) >= 14 else r.get("price_lag_7") or r["price_modal_kg"]
        buf.append(float(r["price_modal_kg"]) if r["price_modal_kg"] is not None else 0.0)

    cols = [
        "arrival_date", "commodity", "variety", "mandi_name", "price_modal_kg",
        "arrivals_mt", "price_lag_1", "price_lag_7", "price_lag_14",
        "arrivals_lag_1", "arrivals_7day_avg", "season_week", "month", "year",
        "price_yoy", "arrivals_yoy",
        "bearing_year_flag", "export_demand_proxy", "flowering_weather_score",
    ]
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})

    by_var: dict[str, int] = {}
    for r in rows:
        by_var[r["variety"]] = by_var.get(r["variety"], 0) + 1
    print(f"[pull] wrote -> {OUT}")
    for k, v in sorted(by_var.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
