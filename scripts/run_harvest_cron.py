"""Daily harvest-outcome cron runner.

Iterates over every farmer where harvest actuals have not yet been
collected and the retry budget is not exhausted, looks up their most
recent amed_readings row that has a ``harvest_date_predicted``, derives a
season label from that date, and asks
``pipelines.harvest_trigger.trigger_harvest_collection_if_needed``
whether to send the WhatsApp message.

Usage
-----
    python scripts/run_harvest_cron.py
    python scripts/run_harvest_cron.py --dry-run

The ``--dry-run`` flag swaps the real webhook send for a no-op stub for
the duration of the run AND operates on a temp copy of the SQLite file
so attempt counters / timestamps are thrown away on exit.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

# Ensure the repository root is on sys.path so this script can be invoked
# either as ``python scripts/run_harvest_cron.py`` or as a module.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.cache import DEFAULT_DB_PATH  # noqa: E402
from pipelines.harvest_trigger import (  # noqa: E402
    trigger_harvest_collection_if_needed,
)


def _candidates(db_path: str) -> list[dict]:
    """Return farmers eligible for a harvest-collection attempt."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id,
                   farmer_full_name,
                   mobile_number,
                   harvest_collection_attempts,
                   harvest_collection_status
              FROM farmers
             WHERE COALESCE(harvest_actuals_collected, 0) = 0
               AND COALESCE(harvest_collection_attempts, 0) < 3
            """,
        )
        return [
            {
                "id": row[0],
                "farmer_full_name": row[1],
                "mobile_number": row[2],
                "harvest_collection_attempts": row[3],
                "harvest_collection_status": row[4],
            }
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


def _latest_amed_for_farmer_with_harvest(db_path: str, farmer_id: str) -> dict | None:
    """Return the most recent amed_readings row with a non-null
    ``harvest_date_predicted`` for any of the farmer's plots."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT r.plot_id,
                   r.crop_type_detected,
                   r.harvest_date_predicted,
                   r.fetch_date
              FROM amed_readings r
              JOIN farm_plots p ON p.id = r.plot_id
             WHERE p.farmer_id = ?
               AND r.harvest_date_predicted IS NOT NULL
          ORDER BY r.fetch_date DESC,
                   r.created_at DESC
             LIMIT 1
            """,
            (farmer_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "plot_id": row[0],
            "crop_type_detected": row[1],
            "harvest_date_predicted": row[2],
            "fetch_date": row[3],
        }
    finally:
        conn.close()


def _season_label_from_harvest(harvest_date: date) -> str:
    """Derive a coarse season label.

    Rule (matches spec): month <= 6 => "<year>-rabi", otherwise
    "<year>-kharif".
    """
    season = "rabi" if harvest_date.month <= 6 else "kharif"
    return f"{harvest_date.year}-{season}"


def _parse_harvest_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None


def _install_dry_run_stub() -> None:
    """Replace ``start_harvest_collection`` with a no-op stub."""
    import types

    fake = types.ModuleType("api.webhooks_harvest")

    def _stub(
        farmer_id: str,
        plot_id: Optional[str],
        crop: str,
        variety: Optional[str],
        season_label: str,
        amed_predicted_yield_kg: Optional[float] = None,
        amed_predicted_grade: Optional[str] = None,
    ) -> dict:
        return {
            "session_id": None,
            "actual_id": None,
            "sent": False,
            "dry_run": True,
        }

    fake.start_harvest_collection = _stub  # type: ignore[attr-defined]

    api_pkg = sys.modules.get("api")
    if api_pkg is None:
        api_pkg = types.ModuleType("api")
        api_pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["api"] = api_pkg
    sys.modules["api.webhooks_harvest"] = fake


def run(db_path: str, dry_run: bool = False) -> dict[str, int]:
    if dry_run:
        _install_dry_run_stub()
        import shutil
        import tempfile

        tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_db.close()
        shutil.copyfile(db_path, tmp_db.name)
        db_path = tmp_db.name
        print(f"[dry-run] operating on a temporary copy at {db_path}")

    totals: dict[str, int] = {
        "processed": 0,
        "sent": 0,
        "skipped": 0,
        "max_retries": 0,
        "too_soon": 0,
        "too_early": 0,
        "too_late": 0,
        "no_harvest_date": 0,
        "already_collected": 0,
    }

    candidates = _candidates(db_path)
    for farmer in candidates:
        totals["processed"] += 1
        amed = _latest_amed_for_farmer_with_harvest(db_path, farmer["id"])
        if amed is None:
            totals["no_harvest_date"] += 1
            print(
                f"farmer={farmer['id']} name={farmer['farmer_full_name']!r} "
                f"action=no_harvest_date reason='no_amed_with_harvest_date'"
            )
            continue

        harvest_date = _parse_harvest_date(amed.get("harvest_date_predicted"))
        if harvest_date is None:
            totals["no_harvest_date"] += 1
            print(
                f"farmer={farmer['id']} name={farmer['farmer_full_name']!r} "
                f"action=no_harvest_date reason='harvest_date_unparseable'"
            )
            continue

        season_label = _season_label_from_harvest(harvest_date)

        decision = trigger_harvest_collection_if_needed(
            farmer_id=farmer["id"],
            plot_id=amed.get("plot_id"),
            season_label=season_label,
            db_path=db_path,
        )
        action = decision.get("action") or "skipped"
        totals[action] = totals.get(action, 0) + 1
        print(
            f"farmer={farmer['id']} name={farmer['farmer_full_name']!r} "
            f"season={season_label} action={action} "
            f"reason={decision.get('reason')!r} "
            f"session_id={decision.get('session_id')}"
        )

    summary_keys = (
        "processed",
        "sent",
        "skipped",
        "max_retries",
        "too_soon",
        "too_early",
        "too_late",
        "no_harvest_date",
    )
    summary = " ".join(f"{k}={totals.get(k, 0)}" for k in summary_keys)
    print(summary)
    return totals


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=os.getenv("SHETMITRA_DB_PATH", DEFAULT_DB_PATH),
        help="Path to the SQLite database (default: data/test.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk the decision tree but do not actually send WhatsApp messages",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    run(db_path=args.db_path, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
