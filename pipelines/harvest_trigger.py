"""Harvest-outcome collection trigger.

Decision layer that wires the AMED harvest-date prediction to the
WhatsApp harvest-outcome conversation. After a plot's predicted harvest
date passes (plus a small post-harvest grace window for the farmer to
sell and recover), this module asks: should we open the chat now?

Decision tree:

    1. Farmer already collected            -> ``already_collected``
    2. ``harvest_collection_attempts >= 3`` -> mark FAILED, ``max_retries``
    3. No ``harvest_date_predicted`` row   -> ``no_harvest_date``
    4. today < harvest+7 days              -> ``too_early``
    5. today > harvest+60 days             -> ``too_late``
    6. Attempted within last 48h           -> ``too_soon``
    7. Otherwise call
       ``api.webhooks_harvest.start_harvest_collection``, bump
       attempts/attempted_at, return ``sent``.

The webhook import is intentionally lazy so this module remains
importable in environments where the webhook layer is absent.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 3
MIN_HOURS_BETWEEN_ATTEMPTS = 48
POST_HARVEST_GRACE_DAYS = 7
COLLECTION_DEADLINE_DAYS = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _default_clock() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
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


def _parse_date(value: str | None) -> Optional[date]:
    if not value:
        return None
    text = value.strip()
    try:
        # ISO date.
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _fetch_farmer(conn: sqlite3.Connection, farmer_id: str) -> dict | None:
    cur = conn.execute("PRAGMA table_info(farmers);")
    cols = {row[1] for row in cur.fetchall()}
    needed = (
        "id",
        "farmer_full_name",
        "mobile_number",
        "harvest_actuals_collected",
        "harvest_collection_attempts",
        "harvest_collection_status",
        "harvest_collection_attempted_at",
    )
    select_cols = [c for c in needed if c in cols]
    if not select_cols:
        return None
    cur = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM farmers WHERE id = ?",
        (farmer_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    record = dict(zip(select_cols, row))
    return {
        "id": record.get("id"),
        "farmer_full_name": record.get("farmer_full_name"),
        "mobile_number": record.get("mobile_number"),
        "harvest_actuals_collected": bool(record.get("harvest_actuals_collected")) if record.get("harvest_actuals_collected") is not None else False,
        "harvest_collection_attempts": int(record.get("harvest_collection_attempts") or 0),
        "harvest_collection_status": record.get("harvest_collection_status"),
        "harvest_collection_attempted_at": record.get("harvest_collection_attempted_at"),
    }


def _latest_amed_for_plot(conn: sqlite3.Connection, plot_id: str) -> dict | None:
    cur = conn.execute(
        """
        SELECT plot_id,
               crop_type_detected,
               harvest_date_predicted,
               fetch_date,
               field_size_acres_amed
          FROM amed_readings
         WHERE plot_id = ?
      ORDER BY fetch_date DESC,
               created_at DESC
         LIMIT 1
        """,
        (plot_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "plot_id": row[0],
        "crop_type_detected": row[1],
        "harvest_date_predicted": row[2],
        "fetch_date": row[3],
        "field_size_acres_amed": row[4],
    }


def _fetch_plot(conn: sqlite3.Connection, plot_id: str) -> dict | None:
    cur = conn.execute("PRAGMA table_info(farm_plots);")
    cols = {row[1] for row in cur.fetchall()}
    needed = ("id", "current_crop", "current_crop_variety")
    select_cols = [c for c in needed if c in cols]
    if not select_cols:
        return None
    cur = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM farm_plots WHERE id = ?",
        (plot_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(select_cols, row))


def _set_status(conn: sqlite3.Connection, farmer_id: str, status: str) -> None:
    conn.execute(
        "UPDATE farmers SET harvest_collection_status = ? WHERE id = ?",
        (status, farmer_id),
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
           SET harvest_collection_attempts = ?,
               harvest_collection_attempted_at = ?,
               harvest_collection_status = 'AWAITING_REPLY'
         WHERE id = ?
        """,
        (next_attempts, now.isoformat(), farmer_id),
    )
    conn.commit()


def _result(action: str, reason: str, session_id: str | None = None, **extra: Any) -> dict:
    out = {"action": action, "reason": reason, "session_id": session_id}
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def trigger_harvest_collection_if_needed(
    farmer_id: str,
    plot_id: str | None,
    *,
    season_label: str,
    db_path: str = "data/test.db",
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Decide whether to send the harvest-outcome WhatsApp now."""
    clock_fn = clock or _default_clock
    now = clock_fn()
    today = now.date()

    conn = sqlite3.connect(db_path)
    try:
        farmer = _fetch_farmer(conn, farmer_id)
        if farmer is None:
            return _result("skipped", f"farmer_not_found: {farmer_id}")

        # Step 1 — already collected.
        if farmer["harvest_actuals_collected"]:
            return _result("already_collected", "Harvest actuals already collected for this farmer")

        # Step 2 — max retries.
        if farmer["harvest_collection_attempts"] >= MAX_ATTEMPTS:
            _set_status(conn, farmer_id, "FAILED")
            return _result(
                "max_retries",
                f"Max retries reached ({farmer['harvest_collection_attempts']}); flagged FAILED",
            )

        # Steps 3-5 — harvest date window.
        amed = _latest_amed_for_plot(conn, plot_id) if plot_id else None
        if amed is None or not amed.get("harvest_date_predicted"):
            return _result("no_harvest_date", "No amed harvest_date_predicted available")

        harvest_date = _parse_date(amed["harvest_date_predicted"])
        if harvest_date is None:
            return _result("no_harvest_date", "harvest_date_predicted unparseable")

        if today < harvest_date + timedelta(days=POST_HARVEST_GRACE_DAYS):
            return _result(
                "too_early",
                (
                    f"today={today.isoformat()} < harvest+{POST_HARVEST_GRACE_DAYS}d "
                    f"({(harvest_date + timedelta(days=POST_HARVEST_GRACE_DAYS)).isoformat()})"
                ),
            )

        if today > harvest_date + timedelta(days=COLLECTION_DEADLINE_DAYS):
            return _result(
                "too_late",
                (
                    f"today={today.isoformat()} > harvest+{COLLECTION_DEADLINE_DAYS}d "
                    f"({(harvest_date + timedelta(days=COLLECTION_DEADLINE_DAYS)).isoformat()})"
                ),
            )

        # Step 6 — too soon.
        last_attempt = _parse_dt(farmer["harvest_collection_attempted_at"])
        if last_attempt is not None:
            elapsed = now - last_attempt
            if elapsed < timedelta(hours=MIN_HOURS_BETWEEN_ATTEMPTS):
                hours = elapsed.total_seconds() / 3600.0
                return _result(
                    "too_soon",
                    f"Only {hours:.1f}h since last attempt; waiting for {MIN_HOURS_BETWEEN_ATTEMPTS}h cooldown",
                )

        # Step 7 — send.
        plot = _fetch_plot(conn, plot_id) if plot_id else None
        crop = (
            (amed.get("crop_type_detected") if amed else None)
            or (plot.get("current_crop") if plot else None)
            or "Unknown"
        )
        variety = plot.get("current_crop_variety") if plot else None

        try:
            from api.webhooks_harvest import start_harvest_collection
        except Exception as exc:  # noqa: BLE001
            logger.warning("harvest_trigger: could not import start_harvest_collection: %s", exc)
            return _result("skipped", f"webhook_module_unavailable: {exc}")

        try:
            send_result = start_harvest_collection(
                farmer_id=farmer_id,
                plot_id=plot_id,
                crop=crop,
                variety=variety,
                season_label=season_label,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("harvest_trigger: start_harvest_collection raised: %s", exc)
            return _result("skipped", f"start_harvest_collection_failed: {exc}")

        session_id = None
        if isinstance(send_result, dict):
            session_id = send_result.get("session_id") or send_result.get("actual_id")

        next_attempts = farmer["harvest_collection_attempts"] + 1
        _record_attempt(conn, farmer_id, next_attempts, now)

        return _result(
            "sent",
            f"Harvest collection dispatched (attempt {next_attempts}/{MAX_ATTEMPTS})",
            session_id=session_id,
        )
    finally:
        conn.close()


__all__ = [
    "trigger_harvest_collection_if_needed",
    "MAX_ATTEMPTS",
    "MIN_HOURS_BETWEEN_ATTEMPTS",
    "POST_HARVEST_GRACE_DAYS",
    "COLLECTION_DEADLINE_DAYS",
]
