"""Daily trial-expiry cron for the Trader Intelligence platform.

Intended schedule: 9 AM IST every day. Walks every trader in
``subscription_status='TRIAL'`` and decides what to do based on the
distance between ``trial_ends_at`` and ``now`` (SDD §7.3):

    distance == 3 days  → reminder with days_remaining=3
    distance == 1 day   → reminder with days_remaining=1
    in grace period (≤ 7 days past trial end)
                        → reminder every 2 days, days_remaining=0
    past grace          → flip subscription_status to 'PAUSED'

The 3-day and 1-day reminders run inside a 24-hour window so the cron
catches the right day regardless of the exact minute it runs at.

Usage::

    python scripts/run_trial_expiry_cron.py
    python scripts/run_trial_expiry_cron.py --dry-run

``--dry-run`` mirrors the pattern in ``run_variety_cron.py``:
    * copies the SQLite database to a temp file before running, so any
      writes are thrown away on exit;
    * stubs ``api.trader_whatsapp.send_subscription_reminder`` with an
      in-memory recorder so no AiSensy calls escape.

The dry-run recorder is exposed on the module as
``DRY_RUN_OUTBOX`` so tests can inspect it without poking sys.modules.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


GRACE_PERIOD_DAYS_DEFAULT = 7
THREE_DAY_REMINDER_WINDOW_HOURS = 24
ONE_DAY_REMINDER_WINDOW_HOURS = 24
GRACE_REMINDER_INTERVAL_DAYS = 2


# In-memory recorder used by --dry-run. The cron clears + re-populates it
# so callers (and the test suite) can read the most recent run.
DRY_RUN_OUTBOX: list[dict] = []


def _grace_period_days() -> int:
    try:
        return int(os.getenv("TRADER_GRACE_PERIOD_DAYS", str(GRACE_PERIOD_DAYS_DEFAULT)))
    except ValueError:
        return GRACE_PERIOD_DAYS_DEFAULT


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    # Be liberal: accept the trailing Z that some Postgres serializers emit.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        # Fallback: drop microseconds / extra timezone chunks if needed.
        try:
            parsed = datetime.fromisoformat(text.split("+")[0])
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _list_trial_traders(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT id, full_name, mobile, subscription_tier,
                   subscription_status, trial_started_at, trial_ends_at,
                   notes
              FROM traders
             WHERE subscription_status = 'TRIAL'
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _pause_trader(db_path: str, trader_id: str, now_iso: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE traders
               SET subscription_status = 'PAUSED',
                   updated_at = ?
             WHERE id = ?
            """,
            (now_iso, trader_id),
        )
        conn.commit()
    finally:
        conn.close()


def _record_reminder_sent(
    db_path: str, trader_id: str, when_iso: str, kind: str
) -> None:
    """Stamp a JSON pointer in ``notes`` so the grace-period 2-day cadence
    can avoid double-sending on a re-run within the same day.
    """
    pointer = f"reminder_sent::{kind}::{when_iso[:10]}"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT notes FROM traders WHERE id = ?",
            (trader_id,),
        )
        row = cur.fetchone()
        if row is None:
            return
        notes = row[0] or ""
        if pointer in notes:
            return
        new_notes = (notes + "\n" + pointer).strip()
        conn.execute(
            "UPDATE traders SET notes = ?, updated_at = ? WHERE id = ?",
            (new_notes, when_iso, trader_id),
        )
        conn.commit()
    finally:
        conn.close()


def _last_grace_reminder_date(notes: str | None) -> datetime | None:
    if not notes:
        return None
    # Newest pointer wins.
    latest: datetime | None = None
    for line in notes.splitlines():
        line = line.strip()
        if not line.startswith("reminder_sent::grace::"):
            continue
        try:
            iso_date = line.rsplit("::", 1)[-1]
            parsed = datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest


# ---------------------------------------------------------------------------
# Sender hooks
# ---------------------------------------------------------------------------
def _install_dry_run_stub() -> None:
    """Swap ``api.trader_whatsapp`` for an in-memory stub.

    Mirrors ``run_variety_cron.py``: we register a fake module in
    ``sys.modules`` so the real Agent 3 module — if present — is shadowed
    for the duration of the cron run. The stub recorder lives at
    ``scripts.run_trial_expiry_cron.DRY_RUN_OUTBOX`` so tests can assert.
    """
    DRY_RUN_OUTBOX.clear()

    fake = types.ModuleType("api.trader_whatsapp")

    def _stub(*args: Any, **kwargs: Any) -> dict:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "args": list(args),
            "kwargs": dict(kwargs),
        }
        DRY_RUN_OUTBOX.append(record)
        return {"status": "queued", "mode": "dry-run"}

    fake.send_subscription_reminder = _stub  # type: ignore[attr-defined]
    fake.send_payment_confirmation = _stub  # type: ignore[attr-defined]
    fake.send_cancellation_confirmation = _stub  # type: ignore[attr-defined]
    fake.send_pause_confirmation = _stub  # type: ignore[attr-defined]
    fake.send_resume_confirmation = _stub  # type: ignore[attr-defined]

    api_pkg = sys.modules.get("api")
    if api_pkg is None:
        api_pkg = types.ModuleType("api")
        api_pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["api"] = api_pkg
    sys.modules["api.trader_whatsapp"] = fake


def _send_subscription_reminder(
    trader_id: str, days_remaining: int
) -> None:
    """Dispatch the reminder.

    Lazy-imports so the cron runs even before Agent 3 ships the module.
    Catches any exception so a single failing send does not abort the
    whole cron run.
    """
    try:
        from api import trader_whatsapp  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "level": "warn",
                    "trader_id": trader_id,
                    "msg": f"trader_whatsapp unavailable: {exc}",
                }
            )
        )
        return
    fn = getattr(trader_whatsapp, "send_subscription_reminder", None)
    if fn is None:
        print(
            json.dumps(
                {
                    "level": "warn",
                    "trader_id": trader_id,
                    "msg": "send_subscription_reminder not implemented",
                }
            )
        )
        return
    try:
        fn(trader_id=trader_id, days_remaining=days_remaining)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "level": "warn",
                    "trader_id": trader_id,
                    "msg": f"send failed: {exc}",
                }
            )
        )


# ---------------------------------------------------------------------------
# Core decision logic
# ---------------------------------------------------------------------------
def _decide_action(
    trader: dict,
    *,
    now: datetime,
    grace_days: int,
) -> dict:
    """Return ``{action, days_remaining, reason}`` for one trader row.

    Action values:
        ``send_reminder``    — call send_subscription_reminder.
        ``pause``            — flip subscription_status to 'PAUSED'.
        ``noop``             — do nothing this run.
    """
    trial_ends = _parse_ts(trader.get("trial_ends_at"))
    if trial_ends is None:
        return {
            "action": "noop",
            "days_remaining": None,
            "reason": "missing_trial_ends_at",
        }

    delta = trial_ends - now
    # First the future-tense windows.
    if timedelta(days=3) - timedelta(hours=THREE_DAY_REMINDER_WINDOW_HOURS) < delta <= timedelta(days=3):
        return {
            "action": "send_reminder",
            "days_remaining": 3,
            "reason": "three_day_window",
            "kind": "three_day",
        }
    if timedelta(days=1) - timedelta(hours=ONE_DAY_REMINDER_WINDOW_HOURS) < delta <= timedelta(days=1):
        return {
            "action": "send_reminder",
            "days_remaining": 1,
            "reason": "one_day_window",
            "kind": "one_day",
        }

    # Now the past-tense windows.
    grace_cutoff = trial_ends + timedelta(days=grace_days)
    if now > grace_cutoff:
        return {
            "action": "pause",
            "days_remaining": 0,
            "reason": "grace_period_exceeded",
        }

    if now > trial_ends:
        # Within grace — send a reminder every N days. The fingerprint we
        # stamped into ``notes`` on the last reminder lets us skip if we
        # already sent one less than N days ago.
        last = _last_grace_reminder_date(trader.get("notes"))
        if last is None or (now - last) >= timedelta(days=GRACE_REMINDER_INTERVAL_DAYS):
            return {
                "action": "send_reminder",
                "days_remaining": 0,
                "reason": "grace_period_due",
                "kind": "grace",
            }
        return {
            "action": "noop",
            "days_remaining": 0,
            "reason": "grace_reminder_recent",
        }

    return {
        "action": "noop",
        "days_remaining": delta.days,
        "reason": "not_in_window",
    }


def run(
    db_path: str,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, int]:
    """Walk every TRIAL trader and apply the decision tree."""
    if dry_run:
        import shutil
        import tempfile

        _install_dry_run_stub()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copyfile(db_path, tmp.name)
        db_path = tmp.name
        print(f"[dry-run] operating on a temporary copy at {db_path}")

    now = now or datetime.now(timezone.utc)
    grace_days = _grace_period_days()
    totals: dict[str, int] = {
        "processed": 0,
        "reminded_three_day": 0,
        "reminded_one_day": 0,
        "reminded_grace": 0,
        "paused": 0,
        "noop": 0,
    }

    for trader in _list_trial_traders(db_path):
        totals["processed"] += 1
        decision = _decide_action(trader, now=now, grace_days=grace_days)
        action = decision["action"]
        kind = decision.get("kind")

        if action == "send_reminder":
            _send_subscription_reminder(
                trader_id=trader["id"],
                days_remaining=int(decision["days_remaining"] or 0),
            )
            _record_reminder_sent(
                db_path,
                trader_id=trader["id"],
                when_iso=now.isoformat(),
                kind=kind or "reminder",
            )
            if kind == "three_day":
                totals["reminded_three_day"] += 1
            elif kind == "one_day":
                totals["reminded_one_day"] += 1
            else:
                totals["reminded_grace"] += 1
        elif action == "pause":
            _pause_trader(db_path, trader["id"], now.isoformat())
            totals["paused"] += 1
        else:
            totals["noop"] += 1

        print(
            json.dumps(
                {
                    "trader_id": trader["id"],
                    "mobile": trader.get("mobile"),
                    "action": action,
                    "reason": decision.get("reason"),
                    "days_remaining": decision.get("days_remaining"),
                }
            )
        )

    summary = " ".join(f"{k}={v}" for k, v in totals.items())
    print(summary)
    return totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _default_db_path() -> str:
    return os.getenv("SHETMITRA_DB_PATH", str(_REPO_ROOT / "data" / "test.db"))


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=_default_db_path(),
        help="Path to the SQLite database (default: data/test.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Operate on a temp copy of the DB and replace the WhatsApp sender "
            "with an in-memory recorder."
        ),
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    run(db_path=args.db_path, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
