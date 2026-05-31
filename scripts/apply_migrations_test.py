"""Apply migrations 001..004 to ShetMitra TEST Supabase (euydubpywdsettjywkms).

Reads the password from shetmitra_test/nano.env (the canonical password store for
the TEST project) so we never bake it into source under this repo. Each migration
runs in its own transaction; on failure we ROLLBACK and stop without proceeding
to later migrations.

After each migration we run a verification query.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
NANO_ENV_PATH = Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env")

PROJECT_REF = "euydubpywdsettjywkms"
TARGET_DB = "postgres"
CONNECT_ATTEMPTS = [
    # (host, port, user, label)
    (f"db.{PROJECT_REF}.supabase.co", 5432, "postgres", "direct"),
    ("aws-1-ap-south-1.pooler.supabase.com", 5432, f"postgres.{PROJECT_REF}", "pooler-aws1-session"),
    ("aws-0-ap-south-1.pooler.supabase.com", 5432, f"postgres.{PROJECT_REF}", "pooler-aws0-session"),
    ("aws-1-ap-southeast-1.pooler.supabase.com", 5432, f"postgres.{PROJECT_REF}", "pooler-aws1-se1-session"),
]

MIGRATION_PLAN = [
    {
        "file": "001_amed_new_tables.sql",
        "verify_sql": (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' "
            "AND table_name IN ('amed_readings','amed_belt_data','amed_history') "
            "ORDER BY table_name"
        ),
        "expect_rows": {"amed_belt_data", "amed_history", "amed_readings"},
    },
    {
        "file": "002_amed_alter_existing.sql",
        "verify_sql": (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='farm_plots' "
            "AND column_name IN ('amed_crop_verified','amed_field_id','amed_last_fetch',"
            "'crop_type_mismatch','area_mismatch_pct') ORDER BY column_name"
        ),
        "expect_rows": {
            "amed_crop_verified",
            "amed_field_id",
            "amed_last_fetch",
            "area_mismatch_pct",
            "crop_type_mismatch",
        },
    },
    {
        "file": "003_seed_amed_history.sql",
        "verify_sql": (
            "SELECT season_label FROM amed_history "
            "WHERE region='Tasgaon_Sangli_belt' AND crop_type='Grapes' "
            "ORDER BY season_label"
        ),
        "expect_rows": {"2022-23", "2023-24", "2024-25"},
    },
    {
        "file": "004_variety_collection.sql",
        "verify_sql": (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' "
            "AND table_name IN ('whatsapp_sessions','variety_responses') "
            "ORDER BY table_name"
        ),
        "expect_rows": {"variety_responses", "whatsapp_sessions"},
    },
    {
        "file": "005_harvest_actuals.sql",
        "verify_sql": (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='farm_harvest_actuals'"
        ),
        "expect_rows": {"farm_harvest_actuals"},
    },
    {
        "file": "006_trader_intelligence.sql",
        "verify_sql": (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' "
            "AND table_name IN ('traders','intelligence_reports','report_deliveries',"
            "'trader_queries','trader_payments','flash_alert_triggers') "
            "ORDER BY table_name"
        ),
        "expect_rows": {
            "traders", "intelligence_reports", "report_deliveries",
            "trader_queries", "trader_payments", "flash_alert_triggers",
        },
    },
    {
        "file": "007_mango_crop_expansion.sql",
        "verify_sql": (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' "
            "AND table_name IN ('mango_phenology_log','mango_belt_data','agents','forex_rates') "
            "ORDER BY table_name"
        ),
        "expect_rows": {
            "mango_phenology_log", "mango_belt_data", "agents", "forex_rates",
        },
    },
    {
        "file": "008_model_registry_and_cron_log.sql",
        "verify_sql": (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' "
            "AND table_name IN ('model_registry','cron_run_log') "
            "ORDER BY table_name"
        ),
        "expect_rows": {"model_registry", "cron_run_log"},
    },
]


def _load_password() -> str:
    if not NANO_ENV_PATH.exists():
        sys.exit(f"FATAL: nano.env not found at {NANO_ENV_PATH}")
    vals = dotenv_values(NANO_ENV_PATH)
    pw = vals.get("SUPABASE_DB_PASSWORD")
    if not pw:
        sys.exit("FATAL: SUPABASE_DB_PASSWORD missing from nano.env")
    return pw


def _connect(password: str) -> psycopg2.extensions.connection:
    last_exc = None
    for host, port, user, label in CONNECT_ATTEMPTS:
        print(f"[connect:{label}] {host}:{port} user={user} sslmode=require")
        try:
            return psycopg2.connect(
                host=host,
                port=port,
                dbname=TARGET_DB,
                user=user,
                password=password,
                sslmode="require",
                connect_timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  -> failed: {type(exc).__name__}: {str(exc).strip().splitlines()[0]}")
            last_exc = exc
    raise last_exc


def _smoke_check(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, version()")
        db, usr, ver = cur.fetchone()
        print(f"[smoke] db={db} user={usr}")
        print(f"[smoke] {ver.splitlines()[0]}")
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        )
        (cnt,) = cur.fetchone()
        print(f"[smoke] public table count: {cnt}")
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' "
            "AND table_name IN ('farmers','farm_plots','spray_advisories','price_history_training') "
            "ORDER BY table_name"
        )
        expected = [r[0] for r in cur.fetchall()]
        print(f"[smoke] required base tables present: {expected}")
        missing = {"farmers", "farm_plots"} - set(expected)
        if missing:
            print(f"[smoke] WARNING: missing base tables: {sorted(missing)}")


def _apply_one(conn, plan: dict) -> bool:
    sql_path = MIGRATIONS_DIR / plan["file"]
    print()
    print("=" * 70)
    print(f"[migration] {plan['file']}")
    print("=" * 70)
    if not sql_path.exists():
        print(f"  SKIP - file not found at {sql_path}")
        return False
    sql_text = sql_path.read_text(encoding="utf-8")
    print(f"  bytes={len(sql_text)} preview={sql_text[:160]!r}...")
    # End any transaction that the smoke-check or prior verify left open,
    # then switch into explicit-transaction mode for this migration.
    try:
        conn.rollback()
    except Exception:
        pass
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
        conn.commit()
        print(f"  applied OK (committed)")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"  FAILED -> rolled back. {type(exc).__name__}: {exc}")
        return False
    # verify
    with conn.cursor() as cur:
        cur.execute(plan["verify_sql"])
        rows = {r[0] for r in cur.fetchall()}
    missing = plan["expect_rows"] - rows
    extra = rows - plan["expect_rows"]
    if missing:
        print(f"  VERIFY FAIL -> missing: {sorted(missing)}")
        return False
    print(f"  VERIFY OK  -> found: {sorted(rows)}")
    if extra:
        print(f"  (also present, ignored: {sorted(extra)})")
    return True


def main() -> int:
    password = _load_password()
    try:
        conn = _connect(password)
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: could not connect: {type(exc).__name__}: {exc}")
        return 2
    try:
        _smoke_check(conn)
        all_ok = True
        for plan in MIGRATION_PLAN:
            ok = _apply_one(conn, plan)
            if not ok:
                all_ok = False
                print("[stop] not proceeding to remaining migrations")
                break
        return 0 if all_ok else 1
    finally:
        conn.close()
        print("\n[disconnect] closed.")


if __name__ == "__main__":
    sys.exit(main())
