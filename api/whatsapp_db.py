"""SQLite helpers for the WhatsApp variety-collection flow.

Talks to the local mirror at ``data/test.db`` (overridable through the
``SHETMITRA_DB_PATH`` env var so tests can point at a tmp copy). Every
query uses parameterised SQL; column names accepted by the ``update_*``
helpers are whitelisted to prevent injection through field names.

If a table required by a helper is missing (e.g. tests are running
before Agent 1's seed touched ``farmers``) we log a warning and return
``None`` / no-op rather than crash the webhook. Tests opt in to the
seeded DB through the fixture in ``tests/test_variety_webhook.py``.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "test.db"

_db_path_override: Optional[Path] = None
_lock = threading.Lock()


def set_db_path(path: Path | str) -> None:
    """Test hook: override the SQLite path used by all helpers."""
    global _db_path_override
    with _lock:
        _db_path_override = Path(path)


def reset_db_path() -> None:
    global _db_path_override
    with _lock:
        _db_path_override = None


def get_db_path() -> Path:
    if _db_path_override is not None:
        return _db_path_override
    env = os.getenv("SHETMITRA_DB_PATH")
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Column allow-lists. Anything not in these sets is rejected by update_* so
# we never interpolate untrusted field names into SQL.
# ---------------------------------------------------------------------------
FARMER_UPDATABLE = {
    "farmer_full_name",
    "village",
    "taluka",
    "alternate_mobile",
    "amed_variety_collected",
    "amed_variety_collected_at",
    "variety_collection_attempts",
    "variety_collection_status",
    "variety_collection_attempted_at",
}

PLOT_UPDATABLE = {
    "current_crop_variety",
    "self_reported_acres",
    "amed_crop_verified",
    "amed_verification_date",
    "area_mismatch_pct",
    "variety_source",
}

VARIETY_RESPONSE_UPDATABLE = {
    "variety_reported",
    "name_confirmed",
    "phone_confirmed",
    "village_confirmed",
    "acres_reported",
    "acres_mismatch_pct",
    "mismatch_resolution",
    "collection_started_at",
    "collection_completed_at",
    "status",
}


# ---------------------------------------------------------------------------
# Farmer / plot reads + writes
# ---------------------------------------------------------------------------
def get_farmer_by_mobile(mobile: str) -> Optional[dict]:
    with _connect() as conn:
        if not _table_exists(conn, "farmers"):
            LOG.warning("farmers table missing; skipping lookup")
            return None
        cur = conn.execute(
            "SELECT * FROM farmers WHERE mobile_number = ? LIMIT 1",
            (mobile,),
        )
        return _row_to_dict(cur.fetchone())


def get_farmer_by_id(farmer_id: str) -> Optional[dict]:
    with _connect() as conn:
        if not _table_exists(conn, "farmers"):
            return None
        cur = conn.execute(
            "SELECT * FROM farmers WHERE id = ? LIMIT 1",
            (farmer_id,),
        )
        return _row_to_dict(cur.fetchone())


def get_plot_by_id(plot_id: str) -> Optional[dict]:
    with _connect() as conn:
        if not _table_exists(conn, "farm_plots"):
            return None
        cur = conn.execute(
            "SELECT * FROM farm_plots WHERE id = ? LIMIT 1",
            (plot_id,),
        )
        return _row_to_dict(cur.fetchone())


def _update_with_allowlist(
    table: str,
    pk_column: str,
    pk_value: Any,
    allowed: set[str],
    fields: dict[str, Any],
) -> bool:
    if not fields:
        return False
    bad = [k for k in fields if k not in allowed]
    if bad:
        raise ValueError(
            f"Disallowed update fields for {table}: {bad}. "
            f"Allowed: {sorted(allowed)}"
        )
    with _connect() as conn:
        if not _table_exists(conn, table):
            LOG.warning("%s table missing; skipping update", table)
            return False
        # Filter to columns that actually exist locally so missing-on-mirror
        # columns degrade gracefully instead of erroring out.
        cur = conn.execute(f"PRAGMA table_info({table})")
        local_columns = {row["name"] for row in cur.fetchall()}
        applicable = {k: v for k, v in fields.items() if k in local_columns}
        missing = set(fields) - set(applicable)
        if missing:
            LOG.warning(
                "Columns missing locally on %s: %s — skipped",
                table,
                sorted(missing),
            )
        if not applicable:
            return False
        set_clause = ", ".join(f"{col} = ?" for col in applicable)
        values = list(applicable.values()) + [pk_value]
        conn.execute(
            f"UPDATE {table} SET {set_clause} WHERE {pk_column} = ?",
            values,
        )
        conn.commit()
        return True


def update_farmer(farmer_id: str, **fields: Any) -> bool:
    return _update_with_allowlist(
        "farmers", "id", farmer_id, FARMER_UPDATABLE, fields
    )


def update_plot(plot_id: str, **fields: Any) -> bool:
    return _update_with_allowlist(
        "farm_plots", "id", plot_id, PLOT_UPDATABLE, fields
    )


# ---------------------------------------------------------------------------
# whatsapp_sessions
# ---------------------------------------------------------------------------
def _deserialise_session(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    record = _row_to_dict(row)
    raw = record.get("session_data") if record else None
    if isinstance(raw, str) and raw:
        try:
            record["session_data"] = json.loads(raw)
        except json.JSONDecodeError:
            record["session_data"] = {}
    elif raw is None:
        record["session_data"] = {}
    return record


def get_session_by_mobile(mobile: str) -> Optional[dict]:
    with _connect() as conn:
        if not _table_exists(conn, "whatsapp_sessions"):
            return None
        cur = conn.execute(
            "SELECT * FROM whatsapp_sessions WHERE mobile_number = ? LIMIT 1",
            (mobile,),
        )
        return _deserialise_session(cur.fetchone())


def upsert_session(
    mobile: str,
    *,
    farmer_id: Optional[str],
    current_step: str,
    collection_flow: str,
    session_data: dict,
) -> Optional[dict]:
    now = _now_iso()
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    payload = json.dumps(session_data or {}, ensure_ascii=False)
    with _connect() as conn:
        if not _table_exists(conn, "whatsapp_sessions"):
            LOG.warning("whatsapp_sessions missing; cannot upsert session")
            return None
        existing = conn.execute(
            "SELECT id FROM whatsapp_sessions WHERE mobile_number = ?",
            (mobile,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE whatsapp_sessions
                   SET farmer_id = ?,
                       current_step = ?,
                       collection_flow = ?,
                       session_data = ?,
                       updated_at = ?,
                       expires_at = ?
                 WHERE mobile_number = ?
                """,
                (
                    farmer_id,
                    current_step,
                    collection_flow,
                    payload,
                    now,
                    expires,
                    mobile,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO whatsapp_sessions (
                    id, mobile_number, farmer_id, current_step,
                    collection_flow, session_data,
                    created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    mobile,
                    farmer_id,
                    current_step,
                    collection_flow,
                    payload,
                    now,
                    now,
                    expires,
                ),
            )
        conn.commit()
        cur = conn.execute(
            "SELECT * FROM whatsapp_sessions WHERE mobile_number = ?",
            (mobile,),
        )
        return _deserialise_session(cur.fetchone())


def get_active_session(mobile: str) -> Optional[dict]:
    """Return the session iff it has not expired.

    If expired, also mark any in-progress variety_responses for that
    mobile's farmer as ``ABANDONED`` per the spec.
    """
    session = get_session_by_mobile(mobile)
    if not session:
        return None
    expires = _parse_iso(session.get("expires_at"))
    if expires is None:
        return session  # no expiry recorded -> treat as active
    if expires > datetime.now(timezone.utc):
        return session
    # Expired: abandon any in-progress variety responses for this farmer.
    farmer_id = session.get("farmer_id")
    if farmer_id:
        with _connect() as conn:
            if _table_exists(conn, "variety_responses"):
                conn.execute(
                    """
                    UPDATE variety_responses
                       SET status = 'ABANDONED'
                     WHERE farmer_id = ?
                       AND status = 'IN_PROGRESS'
                    """,
                    (farmer_id,),
                )
                conn.commit()
    return None


# ---------------------------------------------------------------------------
# amed_readings lookup
# ---------------------------------------------------------------------------
def latest_amed_reading_for_plot(plot_id: str) -> Optional[dict]:
    with _connect() as conn:
        if not _table_exists(conn, "amed_readings"):
            return None
        cur = conn.execute(
            """
            SELECT * FROM amed_readings
             WHERE plot_id = ?
             ORDER BY fetch_date DESC, created_at DESC
             LIMIT 1
            """,
            (plot_id,),
        )
        return _row_to_dict(cur.fetchone())


# ---------------------------------------------------------------------------
# variety_responses
# ---------------------------------------------------------------------------
def create_variety_response(
    farmer_id: str,
    plot_id: str,
    amed_crop_detected: Optional[str],
    amed_confidence: Optional[float],
) -> Optional[str]:
    response_id = str(uuid.uuid4())
    with _connect() as conn:
        if not _table_exists(conn, "variety_responses"):
            LOG.warning("variety_responses missing; cannot create row")
            return None
        conn.execute(
            """
            INSERT INTO variety_responses (
                id, farmer_id, plot_id,
                amed_crop_detected, amed_confidence,
                collection_started_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                response_id,
                farmer_id,
                plot_id,
                amed_crop_detected,
                amed_confidence,
                _now_iso(),
                "IN_PROGRESS",
                _now_iso(),
            ),
        )
        conn.commit()
        return response_id


def update_variety_response(response_id: str, **fields: Any) -> bool:
    return _update_with_allowlist(
        "variety_responses",
        "id",
        response_id,
        VARIETY_RESPONSE_UPDATABLE,
        fields,
    )


def get_variety_response(response_id: str) -> Optional[dict]:
    with _connect() as conn:
        if not _table_exists(conn, "variety_responses"):
            return None
        cur = conn.execute(
            "SELECT * FROM variety_responses WHERE id = ? LIMIT 1",
            (response_id,),
        )
        return _row_to_dict(cur.fetchone())


def latest_variety_response_for_farmer(farmer_id: str) -> Optional[dict]:
    with _connect() as conn:
        if not _table_exists(conn, "variety_responses"):
            return None
        cur = conn.execute(
            """
            SELECT * FROM variety_responses
             WHERE farmer_id = ?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (farmer_id,),
        )
        return _row_to_dict(cur.fetchone())
