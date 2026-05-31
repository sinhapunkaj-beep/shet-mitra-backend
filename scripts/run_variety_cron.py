"""Daily variety-collection cron runner.

Iterates over every farmer where the variety has not yet been collected
and we have not blown through the retry budget, looks up their most
recent ``amed_readings`` row, and asks
``pipelines.variety_trigger.trigger_variety_collection_if_needed``
whether to send the WhatsApp message.

Usage
-----
    python scripts/run_variety_cron.py
    python scripts/run_variety_cron.py --dry-run

The ``--dry-run`` flag short-circuits the actual webhook send by
swapping ``api.webhooks_variety.start_variety_collection`` for a no-op
stub for the duration of the run, so attempt counters and timestamps
are not mutated.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

# Ensure the repository root is on sys.path so this script can be
# invoked as either ``python scripts/run_variety_cron.py`` or
# ``python -m scripts.run_variety_cron``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.cache import DEFAULT_DB_PATH  # noqa: E402
from pipelines.variety_trigger import (  # noqa: E402
    trigger_variety_collection_if_needed,
)


def _candidates(db_path: str) -> list[dict]:
    """Return farmers eligible for a variety-collection retry."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id,
                   farmer_full_name,
                   mobile_number,
                   variety_collection_attempts,
                   variety_collection_status
              FROM farmers
             WHERE COALESCE(amed_variety_collected, 0) = 0
               AND COALESCE(variety_collection_attempts, 0) < 3
            """,
        )
        return [
            {
                "id": row[0],
                "farmer_full_name": row[1],
                "mobile_number": row[2],
                "variety_collection_attempts": row[3],
                "variety_collection_status": row[4],
            }
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


def _latest_amed_for_farmer(db_path: str, farmer_id: str) -> dict | None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT r.plot_id,
                   r.crop_type_detected,
                   r.crop_type_confidence,
                   r.field_size_acres_amed
              FROM amed_readings r
              JOIN farm_plots p ON p.id = r.plot_id
             WHERE p.farmer_id = ?
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
            "crop_type": row[1],
            "crop_type_confidence": row[2],
            "crop_confidence": row[2],
            "field_size_acres": row[3],
            "field_size_acres_amed": row[3],
        }
    finally:
        conn.close()


def _install_dry_run_stub() -> None:
    """Replace start_variety_collection with a no-op so the trigger
    still walks its decision tree but no message is actually sent.

    We register a fake ``api.webhooks_variety`` module in
    ``sys.modules`` so the trigger's lazy import resolves to our stub
    even when Agent 2's real module is also importable.
    """
    import types

    fake = types.ModuleType("api.webhooks_variety")

    def _stub(
        farmer_id: str,
        plot_id: str | None,
        amed_crop: str,
        amed_confidence: float,
        amed_acres: float | None,
    ) -> dict:
        return {
            "session_id": None,
            "sent": False,
            "dry_run": True,
        }

    fake.start_variety_collection = _stub  # type: ignore[attr-defined]

    # Make sure the api package exists too.
    api_pkg = sys.modules.get("api")
    if api_pkg is None:
        api_pkg = types.ModuleType("api")
        api_pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["api"] = api_pkg
    sys.modules["api.webhooks_variety"] = fake


def run(db_path: str, dry_run: bool = False) -> dict[str, int]:
    if dry_run:
        # 1. Stub out the webhook send.
        _install_dry_run_stub()
        # 2. Mirror the DB to a temp file so attempt counters / timestamps
        #    written by the trigger are thrown away when the job ends.
        import shutil
        import tempfile

        tmp_db = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False
        )
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
        "low_confidence": 0,
        "no_crop_detected": 0,
        "already_collected": 0,
    }

    candidates = _candidates(db_path)
    for farmer in candidates:
        totals["processed"] += 1
        amed = _latest_amed_for_farmer(db_path, farmer["id"])
        if amed is None:
            totals["skipped"] += 1
            print(
                f"farmer={farmer['id']} name={farmer['farmer_full_name']!r} "
                f"action=skipped reason=no_amed_reading"
            )
            continue

        decision = trigger_variety_collection_if_needed(
            farmer_id=farmer["id"],
            plot_id=amed.get("plot_id"),
            amed_data=amed,
            db_path=db_path,
        )
        action = decision.get("action") or "skipped"
        totals[action] = totals.get(action, 0) + 1
        print(
            f"farmer={farmer['id']} name={farmer['farmer_full_name']!r} "
            f"action={action} reason={decision.get('reason')!r} "
            f"session_id={decision.get('session_id')}"
        )

    summary_keys = (
        "processed",
        "sent",
        "skipped",
        "max_retries",
        "too_soon",
        "low_confidence",
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
