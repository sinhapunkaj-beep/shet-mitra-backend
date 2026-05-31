"""
Tests for the variety-collection migration (004) and its local SQLite mirror.

We do not touch production Supabase. We:
  * sanity-check the migration SQL parses and contains the key DDL fragments
  * build a fresh SQLite mirror via scripts.seed_local_sqlite.build_local_db
  * assert the variety tables and columns exist
  * assert the 2 seeded farmers are present (one Grapes, one Pomegranate)
  * exercise foreign-key behaviour on variety_responses
  * exercise the UNIQUE(mobile_number) constraint on whatsapp_sessions
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

# Make repo root importable so scripts.seed_local_sqlite resolves.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.seed_local_sqlite import (  # noqa: E402
    SEED_FARMER_GRAPES_ID,
    SEED_FARMER_POMEGRANATE_ID,
    SEED_PLOT_GRAPES_ID,
    build_local_db,
)

MIGRATION_PATH = REPO_ROOT / "migrations" / "004_variety_collection.sql"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    target = tmp_path / "test.db"
    build_local_db(target)
    return target


@pytest.fixture()
def conn(db_path: Path):
    c = sqlite3.connect(str(db_path))
    # Variety-flow tests rely on real FK enforcement.
    c.execute("PRAGMA foreign_keys = ON;")
    try:
        yield c
    finally:
        c.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    return {row[1] for row in cur.fetchall()}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# 1. SQL file sanity
# ---------------------------------------------------------------------------


def test_004_sql_idempotent():
    """The migration file must contain the expected DDL fragments and use the
    idempotent CREATE / ADD COLUMN IF NOT EXISTS pattern throughout."""
    assert MIGRATION_PATH.exists(), f"Migration file not found: {MIGRATION_PATH}"
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    # Required fragments per the spec.
    assert "CREATE TABLE IF NOT EXISTS variety_responses" in sql
    assert "ALTER TABLE farmers" in sql
    assert "variety_collection_status" in sql
    # The CHECK literal for the status column must appear verbatim.
    assert "'AWAITING_REPLY'" in sql
    assert "'AGENT_REQUIRED'" in sql

    # Idempotency hygiene: no destructive verbs.
    upper = sql.upper()
    assert "DROP TABLE" not in upper
    assert "TRUNCATE" not in upper

    # The pipeline-trigger throttle column from Agent 3.
    assert "variety_collection_attempted_at" in sql


# ---------------------------------------------------------------------------
# 2. SQLite mirror has the expected variety tables + columns
# ---------------------------------------------------------------------------


def test_local_sqlite_has_variety_tables(conn):
    tables = _table_names(conn)
    expected = {
        "farmers",
        "farm_plots",
        "spray_advisories",
        "whatsapp_sessions",
        "variety_responses",
    }
    missing = expected - tables
    assert not missing, f"Missing variety tables: {missing}"

    farmer_cols = _columns(conn, "farmers")
    required_farmer_cols = {
        "amed_variety_collected",
        "variety_collection_status",
        "variety_collection_attempts",
        "alternate_mobile",
        "variety_collection_attempted_at",
    }
    missing_cols = required_farmer_cols - farmer_cols
    assert not missing_cols, f"farmers missing columns: {missing_cols}"


# ---------------------------------------------------------------------------
# 3. Seed data
# ---------------------------------------------------------------------------


def test_farmers_seed_has_two_rows(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM farmers;")
    assert cur.fetchone()[0] == 2

    cur.execute(
        """
        SELECT fp.current_crop
        FROM farmers f
        JOIN farm_plots fp ON fp.farmer_id = f.id
        ORDER BY fp.current_crop;
        """
    )
    crops = [r[0] for r in cur.fetchall()]
    assert crops == ["Grapes", "Pomegranate"], crops


# ---------------------------------------------------------------------------
# 4. variety_responses foreign keys
# ---------------------------------------------------------------------------


def test_variety_responses_foreign_keys(conn):
    cur = conn.cursor()

    # Valid insert tied to seeded farmer + plot.
    new_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO variety_responses (
            id, farmer_id, plot_id, variety_reported, status
        ) VALUES (?, ?, ?, ?, 'IN_PROGRESS')
        """,
        (new_id, SEED_FARMER_GRAPES_ID, SEED_PLOT_GRAPES_ID, "Thompson Seedless"),
    )
    conn.commit()

    cur.execute(
        "SELECT variety_reported FROM variety_responses WHERE id = ?;",
        (new_id,),
    )
    row = cur.fetchone()
    assert row is not None and row[0] == "Thompson Seedless"

    # Invalid farmer_id must violate the FK with foreign_keys = ON.
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            """
            INSERT INTO variety_responses (
                id, farmer_id, plot_id, variety_reported, status
            ) VALUES (?, ?, ?, ?, 'IN_PROGRESS')
            """,
            (
                str(uuid.uuid4()),
                "00000000-0000-0000-0000-000000000000",
                SEED_PLOT_GRAPES_ID,
                "Sharad Seedless",
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 5. whatsapp_sessions UNIQUE(mobile_number)
# ---------------------------------------------------------------------------


def test_whatsapp_sessions_unique_mobile(conn):
    cur = conn.cursor()
    # The seed already inserted a row for 9876543210; a second insert for the
    # same mobile must fail.
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            """
            INSERT INTO whatsapp_sessions (
                id, mobile_number, farmer_id, current_step, collection_flow
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "9876543210",
                SEED_FARMER_POMEGRANATE_ID,
                "AWAITING_VARIETY",
                "variety_collection",
            ),
        )
        conn.commit()
