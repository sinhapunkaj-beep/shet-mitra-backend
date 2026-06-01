"""One-off applier for migrations 009c then 009b — see git history for why.

This script is intentionally tactical. It exists only because:

  * `apply_migrations.py` has a hardcoded migration list (001..003) and
    no `--file` flag.
  * `009b_mandi_jharkhand_seed.sql` previously failed on a pre-existing
    `mandi_config` table that lacked the columns the seed references.
  * `009c_mandi_config_columns.sql` adds those columns + backfills the
    7 legacy Maharashtra rows so `009b` can finally land cleanly.

Apply order locked here:  009c  →  009b. Each runs in its own transaction.
"""

from __future__ import annotations

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
    (f"db.{PROJECT_REF}.supabase.co", 5432, "postgres", "direct"),
    ("aws-1-ap-south-1.pooler.supabase.com", 5432, f"postgres.{PROJECT_REF}", "pooler-aws1-session"),
    ("aws-0-ap-south-1.pooler.supabase.com", 5432, f"postgres.{PROJECT_REF}", "pooler-aws0-session"),
    ("aws-1-ap-southeast-1.pooler.supabase.com", 5432, f"postgres.{PROJECT_REF}", "pooler-aws1-se1-session"),
]

PLAN = ["009c_mandi_config_columns.sql", "009b_mandi_jharkhand_seed.sql"]


def get_password() -> str:
    env = dotenv_values(NANO_ENV_PATH)
    pw = env.get("SUPABASE_DB_PASSWORD") or env.get("SUPABASE_PASSWORD")
    if not pw:
        sys.exit(f"!! no SUPABASE_DB_PASSWORD in {NANO_ENV_PATH}")
    return pw


def connect():
    pw = get_password()
    last_err: Exception | None = None
    for host, port, user, label in CONNECT_ATTEMPTS:
        try:
            print(f"-> trying {label}: {user}@{host}:{port}")
            conn = psycopg2.connect(
                host=host, port=port, dbname=TARGET_DB,
                user=user, password=pw, sslmode="require",
                connect_timeout=10,
            )
            conn.autocommit = False
            print(f"OK connected via {label}")
            return conn
        except Exception as exc:  # noqa: BLE001
            print(f"   FAIL {label}: {exc}")
            last_err = exc
    raise SystemExit(f"!! all Supabase connect attempts failed; last: {last_err}")


def apply_one(conn, filename: str) -> None:
    path = MIGRATIONS_DIR / filename
    if not path.exists():
        sys.exit(f"!! migration not found: {path}")
    sql = path.read_text(encoding="utf-8")
    print(f"\n== applying {filename} ({len(sql):,} bytes) ==")
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
            conn.commit()
            print(f"   committed.")
        except Exception as exc:
            conn.rollback()
            print(f"!! ROLLBACK {filename}: {exc}")
            raise


def verify(conn) -> None:
    print("\n== verification ==")
    with conn.cursor() as cur:
        # 1. mandi_config column inventory
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='mandi_config' "
            "ORDER BY ordinal_position"
        )
        cols = [r[0] for r in cur.fetchall()]
        print(f"-- mandi_config columns ({len(cols)}):")
        for c in cols:
            print(f"     {c}")

        for required in (
            "region_code", "commodity_primary", "varieties_traded",
            "role", "is_price_setter", "is_gi_specialist",
            "ceda_state_code", "notes",
        ):
            assert required in cols, f"missing column: {required}"
        print("   all 8 Bagaan Sathi columns present.")

        # 2. row counts by region
        cur.execute(
            "SELECT COALESCE(region_code,'(null)') AS rc, COUNT(*) "
            "FROM mandi_config GROUP BY rc ORDER BY rc"
        )
        print("\n-- mandi rows by region_code:")
        total = 0
        for rc, n in cur.fetchall():
            print(f"     {rc:>8s} : {n}")
            total += n
        print(f"     {'TOTAL':>8s} : {total}")

        # 3. row counts by state (the SDD verification block)
        cur.execute(
            "SELECT state, COUNT(*) FROM mandi_config "
            "GROUP BY state ORDER BY state"
        )
        print("\n-- mandi rows by state:")
        for st, n in cur.fetchall():
            print(f"     {st:>20s} : {n}")

        # 4. Spot-check a few JH mandis
        cur.execute(
            "SELECT mandi_name, role, is_gi_specialist "
            "FROM mandi_config "
            "WHERE region_code='JH' ORDER BY mandi_name"
        )
        print("\n-- Jharkhand mandis seeded:")
        for name, role, gi in cur.fetchall():
            badge = " [GI]" if gi else ""
            print(f"     {name:<22s} {role}{badge}")


def main() -> None:
    print(f"-> migrations dir: {MIGRATIONS_DIR}")
    conn = connect()
    try:
        for filename in PLAN:
            apply_one(conn, filename)
        verify(conn)
        print("\nOK all migrations applied + verified.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
