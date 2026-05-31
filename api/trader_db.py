"""SQLite helpers for the trader-intelligence platform.

Mirrors the structure of ``api.whatsapp_db``: uses the same DB path resolver
(``whatsapp_db.get_db_path`` so the test override env var / set_db_path hook
applies uniformly), opens parameterised connections, and degrades gracefully
when migration 006 has not yet been applied to the local mirror.

Every UPDATE goes through an allow-list so a column name from an untrusted
caller can never be interpolated into SQL.

If the ``traders`` table is missing entirely (Agent 1 hasn't finished), the
helpers log a warning EXACTLY ONCE per process and return None / False / [].
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from api import whatsapp_db

LOG = logging.getLogger(__name__)

_missing_table_warned = False
_missing_lock = threading.Lock()


# Allow-list of columns that the public update_trader() may write to. Any other
# field name passed in is rejected before SQL is built.
TRADER_UPDATABLE = {
    "subscription_tier",
    "subscription_status",
    "monthly_amount",
    "razorpay_customer_id",
    "razorpay_subscription_id",
    "subscription_started_at",
    "subscription_renewed_at",
    "query_count_this_month",
    "notes",
    "whatsapp_opted_in",
    "private_group_added",
}


# ---------------------------------------------------------------------------
# Connection / table-existence helpers
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(whatsapp_db.get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def _warn_missing_table_once(table: str) -> None:
    global _missing_table_warned
    with _missing_lock:
        if _missing_table_warned:
            return
        _missing_table_warned = True
    LOG.warning(
        "trader_db: required table %s is missing — degrading to no-op. "
        "Run scripts/seed_local_sqlite.ensure_trader_intelligence_schema().",
        table,
    )


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    record = {key: row[key] for key in row.keys()}
    # commodities is stored as a JSON-encoded TEXT column locally.
    raw = record.get("commodities")
    if isinstance(raw, str) and raw:
        try:
            record["commodities"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Leave as-is — callers can decide what to do with the raw string.
            pass
    return record


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def get_trader_by_mobile(mobile: str) -> Optional[dict]:
    if not mobile:
        return None
    with _connect() as conn:
        if not _table_exists(conn, "traders"):
            _warn_missing_table_once("traders")
            return None
        cur = conn.execute(
            "SELECT * FROM traders WHERE mobile = ? LIMIT 1",
            (mobile,),
        )
        return _row_to_dict(cur.fetchone())


def get_trader_by_id(trader_id: str) -> Optional[dict]:
    if not trader_id:
        return None
    with _connect() as conn:
        if not _table_exists(conn, "traders"):
            _warn_missing_table_once("traders")
            return None
        cur = conn.execute(
            "SELECT * FROM traders WHERE id = ? LIMIT 1",
            (trader_id,),
        )
        return _row_to_dict(cur.fetchone())


def is_trader(mobile: str) -> bool:
    """Cheap existence probe used by the AISensy router."""
    if not mobile:
        return False
    with _connect() as conn:
        if not _table_exists(conn, "traders"):
            _warn_missing_table_once("traders")
            return False
        cur = conn.execute(
            "SELECT 1 FROM traders WHERE mobile = ? LIMIT 1",
            (mobile,),
        )
        return cur.fetchone() is not None


def trader_platform_available() -> bool:
    """True iff the traders table exists on the configured DB.

    Used by the AISensy router so it only routes unknown mobiles to the
    trader onboarding flow when the platform is actually provisioned
    locally. Tests that build a DB without migration 006 (e.g. the
    variety-collection suite) get the old default-greeting behaviour.
    """
    with _connect() as conn:
        return _table_exists(conn, "traders")


def create_trader(
    mobile: str,
    *,
    full_name: str,
    business_name: Optional[str] = None,
    location: Optional[str] = None,
    district: Optional[str] = None,
    commodities: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Insert a new trader row and return the generated id.

    The trader starts in TRIAL status with a 4-week window, BASIC tier
    (per spec §7.3 — trial gets STANDARD content but the stored tier
    stays BASIC until the trader actually converts).

    Returns ``None`` (and logs once) if the traders table is missing.
    Re-raises sqlite3.IntegrityError on UNIQUE(mobile) collisions — callers
    should check via ``get_trader_by_mobile`` first.
    """
    trader_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    trial_end_iso = (now + timedelta(weeks=4)).isoformat()
    commodities_payload = (
        json.dumps(list(commodities), ensure_ascii=False)
        if commodities
        else None
    )

    with _connect() as conn:
        if not _table_exists(conn, "traders"):
            _warn_missing_table_once("traders")
            return None
        conn.execute(
            """
            INSERT INTO traders (
                id, full_name, mobile, business_name, location, district,
                commodities, subscription_tier, subscription_status,
                trial_started_at, trial_ends_at,
                whatsapp_opted_in, private_group_added,
                query_count_this_month, is_active,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, 'BASIC', 'TRIAL',
                ?, ?,
                1, 0,
                0, 1,
                ?, ?
            )
            """,
            (
                trader_id,
                full_name,
                mobile,
                business_name,
                location,
                district,
                commodities_payload,
                now_iso,
                trial_end_iso,
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
        return trader_id


def update_trader(trader_id: str, **fields: Any) -> bool:
    """Update writable columns on a trader row.

    Field names are validated against ``TRADER_UPDATABLE``; anything else
    raises ``ValueError`` before SQL is built.
    """
    if not fields:
        return False
    bad = [k for k in fields if k not in TRADER_UPDATABLE]
    if bad:
        raise ValueError(
            f"Disallowed trader update fields: {bad}. "
            f"Allowed: {sorted(TRADER_UPDATABLE)}"
        )
    with _connect() as conn:
        if not _table_exists(conn, "traders"):
            _warn_missing_table_once("traders")
            return False
        # Filter to columns that actually exist on the local mirror; SQLite
        # may legitimately lack a column the live Postgres has.
        cur = conn.execute("PRAGMA table_info(traders)")
        local_columns = {row["name"] for row in cur.fetchall()}
        applicable = {k: v for k, v in fields.items() if k in local_columns}
        missing = set(fields) - set(applicable)
        if missing:
            LOG.warning(
                "trader_db: columns missing locally on traders: %s — skipped",
                sorted(missing),
            )
        if not applicable:
            return False
        applicable["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{col} = ?" for col in applicable)
        values = list(applicable.values()) + [trader_id]
        conn.execute(
            f"UPDATE traders SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()
        return True


def increment_query_count(trader_id: str) -> bool:
    """Bump the PREMIUM rate-limit counter for the current month."""
    if not trader_id:
        return False
    with _connect() as conn:
        if not _table_exists(conn, "traders"):
            _warn_missing_table_once("traders")
            return False
        conn.execute(
            """
            UPDATE traders
               SET query_count_this_month =
                       COALESCE(query_count_this_month, 0) + 1,
                   updated_at = ?
             WHERE id = ?
            """,
            (_now_iso(), trader_id),
        )
        conn.commit()
        return True


def list_active_traders_by_tier(tier: Optional[str] = None) -> list[dict]:
    """Return all currently-active traders, optionally filtered by tier.

    "Active" here means ``is_active = 1`` AND ``subscription_status`` is
    one of ('TRIAL', 'ACTIVE'). Used by the broadcast senders.
    """
    with _connect() as conn:
        if not _table_exists(conn, "traders"):
            _warn_missing_table_once("traders")
            return []
        if tier:
            cur = conn.execute(
                """
                SELECT * FROM traders
                 WHERE is_active = 1
                   AND subscription_status IN ('TRIAL', 'ACTIVE')
                   AND subscription_tier = ?
                 ORDER BY created_at
                """,
                (tier,),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM traders
                 WHERE is_active = 1
                   AND subscription_status IN ('TRIAL', 'ACTIVE')
                 ORDER BY created_at
                """
            )
        return [_row_to_dict(row) for row in cur.fetchall() if row is not None]


# ---------------------------------------------------------------------------
# Helpers for adjacent tables that the trader webhook / senders need.
# ---------------------------------------------------------------------------
def insert_trader_query(
    trader_id: str,
    query_text: str,
    response_text: Optional[str] = None,
    model_inputs: Optional[dict] = None,
    response_sent_at: Optional[str] = None,
) -> Optional[str]:
    """Persist a PREMIUM direct-query into ``trader_queries``.

    Returns the new row id, or ``None`` if the table is missing.
    """
    if not trader_id or not query_text:
        return None
    query_id = str(uuid.uuid4())
    received_at = _now_iso()
    payload = (
        json.dumps(model_inputs, ensure_ascii=False)
        if model_inputs
        else None
    )
    with _connect() as conn:
        if not _table_exists(conn, "trader_queries"):
            _warn_missing_table_once("trader_queries")
            return None
        conn.execute(
            """
            INSERT INTO trader_queries (
                id, trader_id, query_text,
                query_received_at, response_text, response_sent_at,
                model_inputs, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query_id,
                trader_id,
                query_text,
                received_at,
                response_text,
                response_sent_at,
                payload,
                received_at,
            ),
        )
        conn.commit()
        return query_id


def insert_report_delivery(
    *,
    report_id: Optional[str],
    trader_id: str,
    delivery_status: str = "PENDING",
    aisensy_message_id: Optional[str] = None,
) -> Optional[str]:
    """Persist a ``report_deliveries`` row.

    Returns the new row id or ``None`` if the table is missing.
    """
    if not trader_id:
        return None
    delivery_id = str(uuid.uuid4())
    now = _now_iso()
    delivered_at = now if delivery_status == "SENT" else None
    with _connect() as conn:
        if not _table_exists(conn, "report_deliveries"):
            _warn_missing_table_once("report_deliveries")
            return None
        conn.execute(
            """
            INSERT INTO report_deliveries (
                id, report_id, trader_id,
                delivered_at, delivery_status,
                aisensy_message_id, retry_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                delivery_id,
                report_id,
                trader_id,
                delivered_at,
                delivery_status,
                aisensy_message_id,
                now,
            ),
        )
        conn.commit()
        return delivery_id


def list_report_deliveries(report_id: str) -> list[dict]:
    """Helper used by tests to inspect the deliveries log."""
    if not report_id:
        return []
    with _connect() as conn:
        if not _table_exists(conn, "report_deliveries"):
            return []
        cur = conn.execute(
            "SELECT * FROM report_deliveries WHERE report_id = ?",
            (report_id,),
        )
        return [_row_to_dict(row) for row in cur.fetchall() if row is not None]


def list_queries_for_trader(trader_id: str) -> list[dict]:
    if not trader_id:
        return []
    with _connect() as conn:
        if not _table_exists(conn, "trader_queries"):
            return []
        cur = conn.execute(
            "SELECT * FROM trader_queries WHERE trader_id = ? ORDER BY query_received_at DESC",
            (trader_id,),
        )
        return [_row_to_dict(row) for row in cur.fetchall() if row is not None]


def reset_missing_table_warning() -> None:
    """Test hook so a fresh test process can re-warn after schema removal."""
    global _missing_table_warned
    with _missing_lock:
        _missing_table_warned = False
