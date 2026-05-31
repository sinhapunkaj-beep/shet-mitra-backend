"""Variety collection trigger module.

Pure-logic decision layer that wires the existing AMED pipeline to the
WhatsApp variety collection conversation (Agent 2). After step 5 of
``AmedSentinelPipeline.run_full_pipeline`` we ask this module: given the
AMED reading just produced for this farmer, should we kick off the
verification chat now?

Decision tree (per AGENT 3 spec):

    1. Farmer already collected -> ``already_collected``
    2. ``variety_collection_attempts >= 3`` -> flag ``AGENT_REQUIRED``,
       return ``max_retries``
    3. ``amed_data.crop_type_detected`` is None -> ``no_crop_detected``
    4. ``amed_data.crop_type_confidence < 0.70`` -> ``low_confidence``
    5. ``variety_collection_attempted_at`` within last 24 hours -> ``too_soon``
    6. Otherwise call ``api.webhooks_variety.start_variety_collection``,
       increment attempts, update attempted_at, return ``sent``.

The webhook import is intentionally lazy so this file remains importable
even if Agent 2's module is not present (standalone testing).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)


# Confidence threshold below which we do not bother the farmer.
CROP_CONFIDENCE_THRESHOLD = 0.70

# Minimum hours between retry attempts.
MIN_HOURS_BETWEEN_ATTEMPTS = 24

# Maximum attempts before flagging for an agent visit.
MAX_ATTEMPTS = 3

# Keys the pipeline result['amed'] dict and amed_readings rows may use
# interchangeably. We accept both styles so callers do not have to
# translate.
_CROP_KEYS = ("crop_type_detected", "crop_type")
_CONFIDENCE_KEYS = ("crop_type_confidence", "crop_confidence")
_FIELD_SIZE_KEYS = (
    "field_size_acres",
    "field_size_acres_amed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_clock() -> datetime:
    return datetime.now(tz=timezone.utc)


def _pick(amed_data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in amed_data and amed_data[key] is not None:
            return amed_data[key]
    return None


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO timestamp stored in SQLite. Returns ``None`` on failure.

    SQLite stores timestamps as TEXT — production rows are written by
    ``datetime.now(tz=timezone.utc).isoformat()`` but seed rows use
    ``CURRENT_TIMESTAMP`` which is naive UTC. We normalise both cases to
    a timezone-aware UTC datetime so subtraction against ``clock()`` works.
    """
    if not value:
        return None
    try:
        # Python 3.11+ accepts the trailing 'Z' too but we strip it just in case.
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fetch_farmer(conn: sqlite3.Connection, farmer_id: str) -> dict | None:
    cur = conn.execute(
        """
        SELECT id,
               farmer_full_name,
               mobile_number,
               amed_variety_collected,
               variety_collection_attempts,
               variety_collection_status,
               variety_collection_attempted_at
          FROM farmers
         WHERE id = ?
        """,
        (farmer_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "farmer_full_name": row[1],
        "mobile_number": row[2],
        "amed_variety_collected": bool(row[3]) if row[3] is not None else False,
        "variety_collection_attempts": int(row[4] or 0),
        "variety_collection_status": row[5],
        "variety_collection_attempted_at": row[6],
    }


def _set_status_agent_required(conn: sqlite3.Connection, farmer_id: str) -> None:
    conn.execute(
        """
        UPDATE farmers
           SET variety_collection_status = 'AGENT_REQUIRED'
         WHERE id = ?
        """,
        (farmer_id,),
    )
    conn.commit()


def _record_attempt(
    conn: sqlite3.Connection,
    farmer_id: str,
    next_attempts: int,
    now: datetime,
) -> None:
    conn.execute(
        """
        UPDATE farmers
           SET variety_collection_attempts = ?,
               variety_collection_attempted_at = ?,
               variety_collection_status = 'AWAITING_REPLY'
         WHERE id = ?
        """,
        (next_attempts, now.isoformat(), farmer_id),
    )
    conn.commit()


def _result(action: str, reason: str, session_id: str | None = None) -> dict:
    return {"action": action, "reason": reason, "session_id": session_id}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def trigger_variety_collection_if_needed(
    farmer_id: str,
    plot_id: str | None,
    amed_data: Mapping[str, Any] | None,
    db_path: str = "data/test.db",
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Decide whether to send the variety-collection WhatsApp now.

    Parameters
    ----------
    farmer_id
        ``farmers.id`` UUID string.
    plot_id
        ``farm_plots.id`` UUID string — passed through to Agent 2.
    amed_data
        Dict shaped like ``result['amed']`` from
        ``AmedSentinelPipeline.run_full_pipeline`` *or* an
        ``amed_readings`` row. Both key styles are accepted.
    db_path
        SQLite database path. Defaults to ``data/test.db``.
    clock
        Callable returning the "current" timezone-aware datetime. Lets
        tests inject a fixed time without monkeypatching ``datetime``.

    Returns
    -------
    dict
        ``{"action": <decision>, "reason": <human-readable>,
        "session_id": <Agent 2's session id or None>}``.
        ``action`` is one of: ``sent``, ``skipped``, ``max_retries``,
        ``low_confidence``, ``already_collected``, ``too_soon``,
        ``no_crop_detected``.
    """
    clock_fn = clock or _default_clock
    now = clock_fn()

    conn = sqlite3.connect(db_path)
    try:
        farmer = _fetch_farmer(conn, farmer_id)
        if farmer is None:
            return _result(
                "skipped",
                f"farmer_not_found: {farmer_id}",
            )

        # Step 1 — already collected.
        if farmer["amed_variety_collected"]:
            return _result("already_collected", "Variety already collected for this farmer")

        # Step 2 — max retries.
        if farmer["variety_collection_attempts"] >= MAX_ATTEMPTS:
            _set_status_agent_required(conn, farmer_id)
            return _result(
                "max_retries",
                (
                    f"Max retries reached ({farmer['variety_collection_attempts']}); "
                    "flagged AGENT_REQUIRED"
                ),
            )

        amed_dict: Mapping[str, Any] = amed_data or {}

        # Step 3 — no crop detected.
        crop = _pick(amed_dict, _CROP_KEYS)
        if not crop:
            return _result("no_crop_detected", "AMED did not detect a crop")

        # Step 4 — low confidence.
        confidence_raw = _pick(amed_dict, _CONFIDENCE_KEYS)
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else 0.0
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < CROP_CONFIDENCE_THRESHOLD:
            return _result(
                "low_confidence",
                f"crop_type_confidence={confidence:.2f} below threshold {CROP_CONFIDENCE_THRESHOLD}",
            )

        # Step 5 — too soon.
        last_attempt = _parse_dt(farmer["variety_collection_attempted_at"])
        if last_attempt is not None:
            elapsed = now - last_attempt
            if elapsed < timedelta(hours=MIN_HOURS_BETWEEN_ATTEMPTS):
                hours = elapsed.total_seconds() / 3600.0
                return _result(
                    "too_soon",
                    f"Only {hours:.1f}h since last attempt; waiting for 24h cooldown",
                )

        # Step 6 — send.
        field_size = _pick(amed_dict, _FIELD_SIZE_KEYS)
        try:
            field_size = float(field_size) if field_size is not None else None
        except (TypeError, ValueError):
            field_size = None

        try:
            # Lazy import so this module is importable when Agent 2's
            # webhook file is missing or fails to import (e.g. while
            # running the trigger tests in isolation).
            from api.webhooks_variety import start_variety_collection
        except Exception as exc:
            logger.warning(
                "variety_trigger: could not import start_variety_collection: %s", exc
            )
            # We have not actually sent the message; do not bump
            # attempts since the failure was internal.
            return _result(
                "skipped",
                f"webhook_module_unavailable: {exc}",
            )

        try:
            send_result = start_variety_collection(
                farmer_id=farmer_id,
                plot_id=plot_id,
                amed_crop=crop,
                amed_confidence=confidence,
                amed_acres=field_size,
            )
        except Exception as exc:
            logger.warning("variety_trigger: start_variety_collection raised: %s", exc)
            return _result("skipped", f"start_variety_collection_failed: {exc}")

        session_id = None
        if isinstance(send_result, Mapping):
            session_id = send_result.get("session_id")

        next_attempts = farmer["variety_collection_attempts"] + 1
        _record_attempt(conn, farmer_id, next_attempts, now)

        return _result(
            "sent",
            f"Variety collection message dispatched (attempt {next_attempts}/{MAX_ATTEMPTS})",
            session_id=session_id,
        )
    finally:
        conn.close()


__all__ = [
    "trigger_variety_collection_if_needed",
    "CROP_CONFIDENCE_THRESHOLD",
    "MIN_HOURS_BETWEEN_ATTEMPTS",
    "MAX_ATTEMPTS",
]
