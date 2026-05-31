"""
ShetMitra AMED migration runner.

================================================================
WARNING — THIS SCRIPT CAN MODIFY THE PRODUCTION SUPABASE DATABASE
================================================================
By default this script is in DRY-RUN mode and only prints which files would
run. To actually execute the migrations against the database configured in
config.DB_CONFIG (or the DB_* environment variables) you must pass the
explicit --apply flag.

All three migration files are idempotent (CREATE TABLE IF NOT EXISTS,
ADD COLUMN IF NOT EXISTS, ON CONFLICT DO NOTHING) so re-running is safe,
but as a matter of policy you should prefer the Supabase SQL editor for
production schema changes whenever possible.

Usage:
    python scripts/apply_migrations.py             # dry-run (default)
    python scripts/apply_migrations.py --apply     # actually execute
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List

# Make sure imports resolve when run from repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MIGRATIONS_DIR = REPO_ROOT / "migrations"
MIGRATION_FILES = [
    "001_amed_new_tables.sql",
    "002_amed_alter_existing.sql",
    "003_seed_amed_history.sql",
]

PREVIEW_CHARS = 200


def load_connection_params() -> Dict[str, object]:
    """Load DB connection params. Env vars win over config.DB_CONFIG."""
    try:
        from config import DB_CONFIG  # type: ignore
    except Exception as exc:  # pragma: no cover
        print(f"[warn] Could not import config.DB_CONFIG: {exc}")
        DB_CONFIG = {}

    params: Dict[str, object] = {
        "host": os.environ.get("DB_HOST", DB_CONFIG.get("host")),
        "port": int(os.environ.get("DB_PORT", DB_CONFIG.get("port", 5432))),
        "dbname": os.environ.get("DB_NAME", DB_CONFIG.get("database")),
        "user": os.environ.get("DB_USER", DB_CONFIG.get("user")),
        "password": os.environ.get("DB_PASSWORD", DB_CONFIG.get("password")),
    }
    return params


def list_migration_paths() -> List[Path]:
    paths: List[Path] = []
    for name in MIGRATION_FILES:
        p = MIGRATIONS_DIR / name
        if not p.exists():
            print(f"[error] Missing migration file: {p}")
        paths.append(p)
    return paths


def preview(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"<unreadable: {exc}>"
    snippet = text[:PREVIEW_CHARS].replace("\n", " ")
    if len(text) > PREVIEW_CHARS:
        snippet += " ..."
    return snippet


def dry_run() -> int:
    print("=" * 70)
    print("ShetMitra AMED migrations — DRY RUN (no DB changes)")
    print("=" * 70)
    params = load_connection_params()
    safe_params = {
        k: ("***" if k == "password" and v else v) for k, v in params.items()
    }
    print(f"Target DB params (password masked): {safe_params}")
    print()

    paths = list_migration_paths()
    for idx, path in enumerate(paths, 1):
        print(f"[{idx}/{len(paths)}] WOULD RUN: {path}")
        print(f"        preview: {preview(path)}")
        print()

    print("Re-run with --apply to execute these against the configured database.")
    return 0


def apply() -> int:
    print("=" * 70)
    print("ShetMitra AMED migrations — APPLY MODE")
    print("WARNING: this will modify the configured database.")
    print("=" * 70)

    try:
        import psycopg2  # type: ignore
    except ImportError:
        try:
            import psycopg  # type: ignore  # noqa: F401

            driver = "psycopg"
        except ImportError:
            print(
                "[error] Neither psycopg2 nor psycopg is installed. "
                "Install with `pip install psycopg2-binary`."
            )
            return 2
    else:
        driver = "psycopg2"

    params = load_connection_params()
    if not all([params.get("host"), params.get("user"), params.get("dbname")]):
        print(f"[error] Incomplete DB params: {params}")
        return 2

    print(f"Connecting via {driver} to {params['host']}:{params['port']}/{params['dbname']} ...")

    if driver == "psycopg2":
        import psycopg2  # type: ignore

        conn = psycopg2.connect(**params)
    else:
        import psycopg  # type: ignore

        conn = psycopg.connect(**params)

    conn.autocommit = False

    paths = list_migration_paths()
    failures: List[str] = []

    try:
        for idx, path in enumerate(paths, 1):
            print()
            print(f"[{idx}/{len(paths)}] APPLYING: {path}")
            print(f"        preview: {preview(path)}")
            sql = path.read_text(encoding="utf-8")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
                print(f"        OK: {path.name}")
            except Exception as exc:
                conn.rollback()
                msg = f"FAILED {path.name}: {exc}"
                print(f"        {msg}")
                failures.append(msg)
    finally:
        conn.close()

    print()
    if failures:
        print(f"Done with {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All migrations applied successfully.")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually execute migrations. Without this flag the script runs in dry-run mode.",
    )
    args = parser.parse_args(argv)

    if args.apply:
        return apply()
    return dry_run()


if __name__ == "__main__":
    raise SystemExit(main())
