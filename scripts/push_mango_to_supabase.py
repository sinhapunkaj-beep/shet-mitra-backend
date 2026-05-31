"""Push mango market rows from the local synthetic CSV (produced by
scripts/import_mango_market_data.py) into the live ShetMitra TEST Supabase
price_history_training table.

Idempotent: on re-run, DELETEs any existing commodity='Mango' rows first
to avoid duplicates, then bulk-INSERTs all rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import dotenv_values

NANO = Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env")
CSV = Path(__file__).resolve().parent.parent / "data" / "price_history_mango_synthetic.csv"


def main() -> int:
    if not CSV.exists():
        sys.exit(f"FATAL: {CSV} missing. Run scripts/import_mango_market_data.py first.")

    creds = dotenv_values(NANO)
    pw = creds.get("SUPABASE_DB_PASSWORD")
    if not pw:
        sys.exit("FATAL: SUPABASE_DB_PASSWORD missing from nano.env")

    print(f"[load] reading {CSV.name}")
    df = pd.read_csv(CSV, parse_dates=["arrival_date"])
    df = df.rename(columns={
        "arrival_date": "date",
        "price_modal_kg": "modal_price",
        "mandi_name": "market_name",
    })
    # Light feature additions: min/max as +/-5% of modal so the schema columns
    # are not empty (downstream code occasionally falls back to these).
    df["min_price"] = (df["modal_price"] * 0.95).round(2)
    df["max_price"] = (df["modal_price"] * 1.05).round(2)
    df["week_number"] = df["date"].dt.isocalendar().week.astype(int)

    # AMED features stay NULL — mango AMED belt fetch is a follow-up.
    keep_cols = [
        "commodity", "market_name", "date", "min_price", "max_price",
        "modal_price", "arrivals_mt", "year", "month", "week_number",
        "season_week", "price_lag_1", "price_lag_7", "arrivals_lag_1",
        "arrivals_7day_avg", "price_yoy", "arrivals_yoy",
        "bearing_year_flag", "export_demand_proxy", "flowering_weather_score",
        "variety",
    ]
    # Some columns may not be in the CSV; only keep what's there.
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].copy()
    print(f"[load] {len(df)} rows ready")

    print("[connect] live Supabase TEST")
    conn = psycopg2.connect(
        host="db.euydubpywdsettjywkms.supabase.co",
        port=5432, dbname="postgres", user="postgres", password=pw,
        sslmode="require", connect_timeout=20,
    )
    conn.autocommit = False
    cur = conn.cursor()

    print("[step 1] delete any existing Mango rows (idempotent)")
    cur.execute("DELETE FROM price_history_training WHERE commodity = 'Mango'")
    print(f"  deleted: {cur.rowcount}")

    # The id sequence may have drifted out of sync with MAX(id) because
    # rows were originally inserted with explicit ids. Realign so nextval()
    # does not collide with existing primary keys.
    cur.execute(
        "SELECT setval('price_history_training_id_seq', "
        "COALESCE((SELECT MAX(id) FROM price_history_training), 0) + 1, false)"
    )
    cur.execute("SELECT last_value FROM price_history_training_id_seq")
    print(f"  id sequence realigned -> next value: {cur.fetchone()[0]}")

    print("[step 2] bulk-insert mango rows")
    cols = ",".join(keep_cols)
    rows = [tuple(None if pd.isna(v) else v for v in r) for r in df.itertuples(index=False, name=None)]
    psycopg2.extras.execute_values(
        cur,
        f"INSERT INTO price_history_training ({cols}) VALUES %s",
        rows, page_size=500,
    )
    print(f"  inserted: {len(rows)}")

    conn.commit()

    cur.execute("SELECT variety, COUNT(*) FROM price_history_training "
                "WHERE commodity='Mango' GROUP BY variety ORDER BY variety")
    print("\nVERIFY mango rows by variety:")
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1]}")
    cur.execute("SELECT COUNT(*) FROM price_history_training WHERE commodity='Mango'")
    print(f"VERIFY mango total: {cur.fetchone()[0]}")

    cur.close()
    conn.close()
    print("\n[ok] mango CEDA push committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
