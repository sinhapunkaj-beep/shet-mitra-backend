"""pipelines/lot_aggregator.py — SDD §4.3

Weekly aggregation job that groups small farmer lots into tradeable
aggregated lots. Runs Sundays at 23:00 IST.

Aggregation rules (SDD §4.3):
  - For each active region × primary crop:
      - For each of the next 3 harvest weeks:
          - For each grade in {A, B}:
              - Collect AVAILABLE lots whose harvest window falls in
                that week.
              - If we have ≥ 2 farms AND total ≥ 1000 kg, create an
                ``lot_aggregations`` row and broadcast to traders.

All DB / WhatsApp side effects route through helper functions that
tests monkeypatch. The core grouping logic is pure.

Public API
----------
    run_weekly_aggregation() -> dict
    create_aggregated_lot(region_code, commodity, grade, harvest_week_start,
        lots, total_kg) -> dict
    broadcast_aggregated_lot_to_traders(...) -> None
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


MIN_FARMS_FOR_AGGREGATION = 2
MIN_TOTAL_KG_FOR_AGGREGATION = 1000.0
DEFAULT_GRADES = ("A", "B")
WEEKS_AHEAD = 3


# --------------------------------------------------------------------------- #
# Data shims — overridable by tests
# --------------------------------------------------------------------------- #


def get_active_regions() -> list[dict]:
    """Return active region rows. Falls back to the SDD §2.1 seed if the
    ``regions`` table is missing on the local mirror.
    """
    try:
        from api import whatsapp_db
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if not whatsapp_db._table_exists(conn, "regions"):  # noqa: SLF001
                raise RuntimeError("regions table missing")
            cur = conn.execute(
                "SELECT region_code, region_name, primary_crops FROM regions "
                "WHERE is_active = 1 OR is_active = TRUE"
            )
            return [dict(zip(("region_code", "region_name", "primary_crops"), r))
                    for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_active_regions: fallback (%s)", exc)
        return [
            {
                "region_code": "MH",
                "region_name": "Maharashtra",
                "primary_crops": ["Grapes", "Pomegranate", "Mango"],
            },
            {
                "region_code": "JH",
                "region_name": "Jharkhand",
                "primary_crops": ["Mango"],
            },
        ]


def get_next_3_weeks(today: Optional[date] = None) -> list[date]:
    """Return the Monday of each of the next 3 weeks starting today."""
    base = today or date.today()
    # Walk to the upcoming Monday so weeks are calendar-aligned.
    days_to_monday = (7 - base.weekday()) % 7
    if days_to_monday == 0:
        days_to_monday = 0  # already Monday — start with this week
    start = base + timedelta(days=days_to_monday)
    return [start + timedelta(days=7 * i) for i in range(WEEKS_AHEAD)]


def get_lots_in_window(
    region_code: str,
    commodity: str,
    harvest_from: date,
    harvest_to: date,
    status: str = "AVAILABLE",
) -> list[dict]:
    """Return ``farmer_lots`` rows whose harvest window overlaps [from, to]."""
    try:
        from api import whatsapp_db
    except Exception:  # noqa: BLE001
        return []
    try:
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if not whatsapp_db._table_exists(conn, "farmer_lots"):  # noqa: SLF001
                return []
            cur = conn.execute(
                "SELECT * FROM farmer_lots WHERE region_code = ? "
                "AND commodity = ? AND status = ? "
                "AND harvest_date_from <= ? AND harvest_date_to >= ?",
                (
                    region_code,
                    commodity,
                    status,
                    harvest_to.isoformat(),
                    harvest_from.isoformat(),
                ),
            )
            return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_lots_in_window failed: %s", exc)
        return []


def create_aggregated_lot(
    region_code: str,
    commodity: str,
    grade: str,
    harvest_week_start: date,
    lots: list[dict],
    total_kg: float,
) -> dict:
    """Persist a ``lot_aggregations`` row and return its summary."""
    agg_id = str(uuid.uuid4())
    agg_ref = (
        f"AGG-{harvest_week_start.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    )
    week_end = harvest_week_start + timedelta(days=6)
    lot_ids = [str(l.get("id") or l.get("lot_id") or "") for l in lots if l]
    farmer_ids = [str(l.get("farmer_id") or "") for l in lots if l]

    # Best-effort persistence — tests with no DB will simply get the
    # summary dict back. Real production writes via Supabase mirror.
    try:
        from api import whatsapp_db

        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if whatsapp_db._table_exists(conn, "lot_aggregations"):  # noqa: SLF001
                conn.execute(
                    "INSERT INTO lot_aggregations (id, aggregation_ref, "
                    "region_code, commodity, grade_predicted, "
                    "harvest_week_start, harvest_week_end, total_quantity_kg, "
                    "farm_count, lot_ids, farmer_ids, status, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        agg_id, agg_ref, region_code, commodity, grade,
                        harvest_week_start.isoformat(), week_end.isoformat(),
                        total_kg, len(lots),
                        ",".join(lot_ids), ",".join(farmer_ids),
                        "OPEN", datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_aggregated_lot: persist skipped (%s)", exc)

    return {
        "id": agg_id,
        "aggregation_ref": agg_ref,
        "region_code": region_code,
        "commodity": commodity,
        "grade": grade,
        "harvest_week_start": harvest_week_start.isoformat(),
        "harvest_week_end": week_end.isoformat(),
        "total_quantity_kg": total_kg,
        "farm_count": len(lots),
        "lot_ids": lot_ids,
        "farmer_ids": farmer_ids,
    }


def broadcast_aggregated_lot_to_traders(
    region_code: str,
    commodity: str,
    grade: str,
    total_kg: float,
    harvest_week: date,
) -> None:
    """Notify traders about the aggregated lot.

    Delegates to :mod:`api.marketplace_whatsapp` when available; otherwise
    logs and returns — broadcast must never block aggregation.
    """
    try:
        from api import marketplace_whatsapp

        send_fn = getattr(marketplace_whatsapp, "send_trader_lot_alert", None)
        if callable(send_fn):
            send_fn(
                region_code=region_code,
                commodity=commodity,
                grade=grade,
                total_kg=total_kg,
                harvest_week=harvest_week,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("broadcast_aggregated_lot_to_traders skipped: %s", exc)


# --------------------------------------------------------------------------- #
# Pure logic — grouping
# --------------------------------------------------------------------------- #


def _filter_by_grade(lots: list[dict], grade: str) -> list[dict]:
    return [l for l in lots if str(l.get("grade_predicted") or "").upper() == grade]


def _total_kg(lots: list[dict]) -> float:
    return sum(float(l.get("quantity_kg_estimated") or 0.0) for l in lots)


def _qualifies_for_aggregation(lots: list[dict]) -> bool:
    return (
        len(lots) >= MIN_FARMS_FOR_AGGREGATION
        and _total_kg(lots) >= MIN_TOTAL_KG_FOR_AGGREGATION
    )


# --------------------------------------------------------------------------- #
# Main runner
# --------------------------------------------------------------------------- #


def run_weekly_aggregation(today: Optional[date] = None) -> dict:
    """Group small farmer lots into tradeable aggregated lots.

    Returns a summary dict ``{ "aggregations_created": n, "regions": k,
    "commodities": m }`` — convenient for the scheduler to log.
    """
    weeks = get_next_3_weeks(today)
    aggregations: list[dict] = []
    regions = get_active_regions()

    for region in regions:
        region_code = region.get("region_code") or "MH"
        commodities = region.get("primary_crops") or []
        if isinstance(commodities, str):
            # Some mirrors persist primary_crops as comma-separated TEXT.
            commodities = [c.strip() for c in commodities.split(",") if c.strip()]

        for commodity in commodities:
            for week_start in weeks:
                week_end = week_start + timedelta(days=6)
                lots = get_lots_in_window(
                    region_code=region_code,
                    commodity=commodity,
                    harvest_from=week_start,
                    harvest_to=week_end,
                )
                if not lots:
                    continue
                for grade in DEFAULT_GRADES:
                    grade_lots = _filter_by_grade(lots, grade)
                    if not _qualifies_for_aggregation(grade_lots):
                        continue
                    summary = create_aggregated_lot(
                        region_code=region_code,
                        commodity=commodity,
                        grade=grade,
                        harvest_week_start=week_start,
                        lots=grade_lots,
                        total_kg=_total_kg(grade_lots),
                    )
                    aggregations.append(summary)
                    broadcast_aggregated_lot_to_traders(
                        region_code=region_code,
                        commodity=commodity,
                        grade=grade,
                        total_kg=summary["total_quantity_kg"],
                        harvest_week=week_start,
                    )

    return {
        "aggregations_created": len(aggregations),
        "regions": len(regions),
        "weeks_scanned": len(weeks),
        "aggregations": aggregations,
    }


__all__ = [
    "MIN_FARMS_FOR_AGGREGATION",
    "MIN_TOTAL_KG_FOR_AGGREGATION",
    "DEFAULT_GRADES",
    "WEEKS_AHEAD",
    "get_active_regions",
    "get_next_3_weeks",
    "get_lots_in_window",
    "create_aggregated_lot",
    "broadcast_aggregated_lot_to_traders",
    "run_weekly_aggregation",
]
