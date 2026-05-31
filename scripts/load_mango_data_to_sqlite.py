"""Load synthetic mango market + USD/INR CSVs into the SQLite test DB.

ShetMitra Mango Agent 2 - Mango data loader for ``data/test.db``.

Reads:
    data/price_history_mango_synthetic.csv
    data/forex_rates_synthetic.csv

Writes:
    price_history_training   (commodity='Mango', variety set)
    forex_rates              (rate_date + usd_inr_rate)

The loader is intentionally tolerant of partial schemas. The Postgres
migration that creates ``price_history_training`` columns
(``bearing_year_flag``, ``export_demand_proxy``, ``variety``) and the
``forex_rates`` table is owned by Mango Agent 1 (migration 007). If those
tables / columns are not yet present in the local SQLite mirror we log
a warning and skip that source - the script still exits 0 so the rest of
the swarm can proceed.

All inserts use ``INSERT OR IGNORE`` to be idempotent.

Run::

    python scripts/load_mango_data_to_sqlite.py
    python scripts/load_mango_data_to_sqlite.py \
        --mango-csv data/price_history_mango_synthetic.csv \
        --forex-csv data/forex_rates_synthetic.csv \
        --db        data/test.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Iterable

import pandas as pd


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Schema discovery
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
        (table_name,),
    )
    return cur.fetchone() is not None


def _column_set(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table_name});")
    return {row[1] for row in cur.fetchall()}


# Minimal SQLite-side CREATE statements that we use ONLY when Agent 1's
# migration has not yet been mirrored locally and the user explicitly asks
# us to bootstrap (default off). We keep them here as documentation but
# the script will NOT create tables silently - it just warns and skips.

_PRICE_HISTORY_TRAINING_BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS price_history_training (
    id TEXT PRIMARY KEY,
    arrival_date TEXT NOT NULL,
    commodity TEXT NOT NULL,
    mandi TEXT,
    mandi_name TEXT,
    variety TEXT,
    price_modal_kg REAL,
    arrivals_mt REAL,
    price_lag_1 REAL,
    price_lag_7 REAL,
    price_lag_14 REAL,
    arrivals_lag_1 REAL,
    arrivals_7day_avg REAL,
    season_week INTEGER,
    month INTEGER,
    year INTEGER,
    price_yoy REAL,
    arrivals_yoy REAL,
    bearing_year_flag INTEGER DEFAULT 0,
    export_demand_proxy REAL,
    flowering_weather_score REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (arrival_date, commodity, variety, mandi_name)
);
""".strip()


_FOREX_RATES_BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS forex_rates (
    id TEXT PRIMARY KEY,
    rate_date TEXT UNIQUE NOT NULL,
    usd_inr_rate REAL NOT NULL,
    source TEXT DEFAULT 'synthetic',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
""".strip()


def _maybe_bootstrap_schemas(conn: sqlite3.Connection, bootstrap: bool) -> None:
    """When --bootstrap-schema is passed we create the SQLite mirrors of
    Agent 1's migration 007 tables / columns so this script can run
    standalone before Agent 1's SQLite seeder has shipped.
    """
    if not bootstrap:
        return
    cur = conn.cursor()
    if not _table_exists(conn, "price_history_training"):
        cur.execute(_PRICE_HISTORY_TRAINING_BOOTSTRAP_DDL)
    else:
        cols = _column_set(conn, "price_history_training")
        for col, decl in (
            ("variety", "TEXT"),
            ("mandi_name", "TEXT"),
            ("bearing_year_flag", "INTEGER DEFAULT 0"),
            ("export_demand_proxy", "REAL"),
            ("flowering_weather_score", "REAL"),
        ):
            if col not in cols:
                try:
                    cur.execute(
                        f"ALTER TABLE price_history_training "
                        f"ADD COLUMN {col} {decl};"
                    )
                except sqlite3.OperationalError:
                    pass
    if not _table_exists(conn, "forex_rates"):
        cur.execute(_FOREX_RATES_BOOTSTRAP_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Mango loader
# ---------------------------------------------------------------------------

def _load_mango_rows(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
) -> tuple[int, int, str]:
    """Insert mango rows into ``price_history_training``.

    Returns ``(inserted, skipped, status)`` where status is one of
    ``ok`` | ``skipped_missing_table`` | ``skipped_missing_columns``.
    """
    if not _table_exists(conn, "price_history_training"):
        print(
            "[warn] price_history_training table missing - "
            "Agent 1 migration 007 has not run against this SQLite DB. "
            "Skipping mango row insert.",
            file=sys.stderr,
        )
        return 0, len(df), "skipped_missing_table"

    cols = _column_set(conn, "price_history_training")
    required = {
        "arrival_date", "commodity",
        "price_modal_kg", "arrivals_mt",
        "variety", "bearing_year_flag", "export_demand_proxy",
    }
    missing = required - cols
    if missing:
        print(
            f"[warn] price_history_training missing required columns "
            f"{sorted(missing)} - run Agent 1 migration 007 first. "
            "Skipping mango row insert.",
            file=sys.stderr,
        )
        return 0, len(df), "skipped_missing_columns"

    # Figure out which mandi column name the live schema uses. Migration 007
    # leaves the pre-existing column alone (it could be ``mandi`` or
    # ``mandi_name`` depending on the deployment). We accept whichever is
    # present and fall back to NULL if neither.
    mandi_column: str | None = None
    if "mandi_name" in cols:
        mandi_column = "mandi_name"
    elif "mandi" in cols:
        mandi_column = "mandi"

    has_id = "id" in cols
    has_season_week = "season_week" in cols
    has_month = "month" in cols
    has_year = "year" in cols
    has_price_lag_1 = "price_lag_1" in cols
    has_price_lag_7 = "price_lag_7" in cols
    has_price_lag_14 = "price_lag_14" in cols
    has_arrivals_lag_1 = "arrivals_lag_1" in cols
    has_arrivals_7day_avg = "arrivals_7day_avg" in cols
    has_price_yoy = "price_yoy" in cols
    has_arrivals_yoy = "arrivals_yoy" in cols
    has_flowering = "flowering_weather_score" in cols

    insert_cols: list[str] = []
    if has_id:
        insert_cols.append("id")
    insert_cols.extend(["arrival_date", "commodity", "variety"])
    if mandi_column:
        insert_cols.append(mandi_column)
    insert_cols.extend(["price_modal_kg", "arrivals_mt"])
    if has_price_lag_1:
        insert_cols.append("price_lag_1")
    if has_price_lag_7:
        insert_cols.append("price_lag_7")
    if has_price_lag_14:
        insert_cols.append("price_lag_14")
    if has_arrivals_lag_1:
        insert_cols.append("arrivals_lag_1")
    if has_arrivals_7day_avg:
        insert_cols.append("arrivals_7day_avg")
    if has_season_week:
        insert_cols.append("season_week")
    if has_month:
        insert_cols.append("month")
    if has_year:
        insert_cols.append("year")
    if has_price_yoy:
        insert_cols.append("price_yoy")
    if has_arrivals_yoy:
        insert_cols.append("arrivals_yoy")
    insert_cols.extend(["bearing_year_flag", "export_demand_proxy"])
    if has_flowering:
        insert_cols.append("flowering_weather_score")

    placeholders = ", ".join("?" * len(insert_cols))
    columns_sql = ", ".join(insert_cols)
    sql = (
        f"INSERT OR IGNORE INTO price_history_training "
        f"({columns_sql}) VALUES ({placeholders})"
    )

    inserted = 0
    skipped = 0
    cur = conn.cursor()
    for _, row in df.iterrows():
        values: list[object] = []
        if has_id:
            values.append(str(uuid.uuid4()))
        values.append(str(row.get("arrival_date")))
        values.append(str(row.get("commodity") or "Mango"))
        values.append(str(row.get("variety") or ""))
        if mandi_column:
            values.append(str(row.get("mandi_name") or row.get("mandi") or ""))
        values.append(_safe_float(row.get("price_modal_kg")))
        values.append(_safe_float(row.get("arrivals_mt")))
        if has_price_lag_1:
            values.append(_safe_float(row.get("price_lag_1")))
        if has_price_lag_7:
            values.append(_safe_float(row.get("price_lag_7")))
        if has_price_lag_14:
            values.append(_safe_float(row.get("price_lag_14")))
        if has_arrivals_lag_1:
            values.append(_safe_float(row.get("arrivals_lag_1")))
        if has_arrivals_7day_avg:
            values.append(_safe_float(row.get("arrivals_7day_avg")))
        if has_season_week:
            values.append(_safe_int(row.get("season_week")))
        if has_month:
            values.append(_safe_int(row.get("month")))
        if has_year:
            values.append(_safe_int(row.get("year")))
        if has_price_yoy:
            values.append(_safe_float(row.get("price_yoy")))
        if has_arrivals_yoy:
            values.append(_safe_float(row.get("arrivals_yoy")))
        values.append(_safe_int(row.get("bearing_year_flag")))
        values.append(_safe_float(row.get("export_demand_proxy")))
        if has_flowering:
            values.append(_safe_float(row.get("flowering_weather_score")))

        cur.execute(sql, values)
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return inserted, skipped, "ok"


# ---------------------------------------------------------------------------
# Forex loader
# ---------------------------------------------------------------------------

def _load_forex_rows(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
) -> tuple[int, int, str]:
    if not _table_exists(conn, "forex_rates"):
        print(
            "[warn] forex_rates table missing - "
            "Agent 1 migration 007 has not run against this SQLite DB. "
            "Skipping forex row insert.",
            file=sys.stderr,
        )
        return 0, len(df), "skipped_missing_table"

    cols = _column_set(conn, "forex_rates")
    required = {"rate_date", "usd_inr_rate"}
    missing = required - cols
    if missing:
        print(
            f"[warn] forex_rates missing required columns "
            f"{sorted(missing)} - run Agent 1 migration 007 first. "
            "Skipping forex row insert.",
            file=sys.stderr,
        )
        return 0, len(df), "skipped_missing_columns"

    has_id = "id" in cols
    has_source = "source" in cols

    insert_cols: list[str] = []
    if has_id:
        insert_cols.append("id")
    insert_cols.extend(["rate_date", "usd_inr_rate"])
    if has_source:
        insert_cols.append("source")

    placeholders = ", ".join("?" * len(insert_cols))
    columns_sql = ", ".join(insert_cols)
    sql = (
        f"INSERT OR IGNORE INTO forex_rates "
        f"({columns_sql}) VALUES ({placeholders})"
    )

    inserted = 0
    skipped = 0
    cur = conn.cursor()
    for _, row in df.iterrows():
        values: list[object] = []
        if has_id:
            values.append(str(uuid.uuid4()))
        values.append(str(row.get("date") or row.get("rate_date") or ""))
        values.append(_safe_float(row.get("usd_inr_rate")))
        if has_source:
            values.append(str(row.get("source") or "synthetic"))
        cur.execute(sql, values)
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return inserted, skipped, "ok"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN guard
        return None
    return out


def _safe_int(value: object) -> int | None:
    f = _safe_float(value)
    if f is None:
        return None
    return int(f)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_mango_csv() -> str:
    return os.path.join("data", "price_history_mango_synthetic.csv")


def _default_forex_csv() -> str:
    return os.path.join("data", "forex_rates_synthetic.csv")


def _default_db_path() -> str:
    return os.path.join("data", "test.db")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Load synthetic mango market + USD/INR forex CSVs into the "
            "local SQLite test DB. Degrades gracefully when Agent 1's "
            "migration 007 schema is not yet in place."
        ),
    )
    parser.add_argument(
        "--mango-csv",
        default=_default_mango_csv(),
        help="Path to the mango CSV produced by import_mango_market_data.py.",
    )
    parser.add_argument(
        "--forex-csv",
        default=_default_forex_csv(),
        help="Path to the forex CSV produced by import_usd_inr_forex.py.",
    )
    parser.add_argument(
        "--db",
        default=_default_db_path(),
        help="Path to the SQLite test DB. Default: data/test.db.",
    )
    parser.add_argument(
        "--bootstrap-schema",
        action="store_true",
        help=(
            "Create minimal SQLite mirrors of Agent 1's price_history_training "
            "and forex_rates tables when missing. Default OFF - the loader "
            "will warn and skip instead."
        ),
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    os.makedirs(
        os.path.dirname(os.path.abspath(args.db)) or ".",
        exist_ok=True,
    )
    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        _maybe_bootstrap_schemas(conn, args.bootstrap_schema)

        mango_status = "skipped_missing_csv"
        mango_inserted = mango_skipped = 0
        if os.path.exists(args.mango_csv):
            mango_df = pd.read_csv(args.mango_csv)
            mango_inserted, mango_skipped, mango_status = _load_mango_rows(
                conn, mango_df
            )
        else:
            print(
                f"[warn] Mango CSV not found at {args.mango_csv}. "
                "Run scripts/import_mango_market_data.py first.",
                file=sys.stderr,
            )

        forex_status = "skipped_missing_csv"
        forex_inserted = forex_skipped = 0
        if os.path.exists(args.forex_csv):
            forex_df = pd.read_csv(args.forex_csv)
            forex_inserted, forex_skipped, forex_status = _load_forex_rows(
                conn, forex_df
            )
        else:
            print(
                f"[warn] Forex CSV not found at {args.forex_csv}. "
                "Run scripts/import_usd_inr_forex.py first.",
                file=sys.stderr,
            )

        print("=" * 60)
        print("Mango / forex SQLite load summary")
        print("=" * 60)
        print(f"DB:                {args.db}")
        print(
            f"Mango rows:        inserted={mango_inserted} "
            f"skipped={mango_skipped} status={mango_status}"
        )
        print(
            f"Forex rows:        inserted={forex_inserted} "
            f"skipped={forex_skipped} status={forex_status}"
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
