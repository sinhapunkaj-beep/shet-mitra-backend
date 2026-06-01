"""pipelines/plant_supply_queue.py — SDD §4.4

Tasgaon plant priority queue (Maharashtra only). Runs daily at 17:00
IST after the matching engine. Fills the plant's weekly capacity with
satellite-verified Grade A grape supply before external traders get
access to those farms.

Public API
----------
    run_plant_priority_queue(today=None) -> dict
    send_plant_priority_offer(farmer_id, **kwargs) -> None

The economics calc is the SDD §4.4 formula:
    net_to_farmer    = plant_price - processing_fee
    plant_price      = mandi_price * (1 + PLANT_PREMIUM_PCT)
    premium_vs_mandi = net_to_farmer - mandi_price
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


PLANT_PREMIUM_PCT = 0.12          # 12% above mandi for Grade A
DEFAULT_PROCESSING_FEE_PER_KG = 3.0
DEFAULT_PLANT_WEEKLY_CAPACITY_KG = 50_000.0
MIN_REMAINING_CAPACITY_KG = 500.0

# Only Maharashtra grapes use the Tasgaon plant.
PLANT_REGION = "MH"
PLANT_COMMODITY = "Dry Grapes"  # mandi reference commodity
PLANT_FARM_COMMODITY = "Grapes"  # farm-side label used in farmer_lots


# --------------------------------------------------------------------------- #
# Data shims (test-overridable)
# --------------------------------------------------------------------------- #


def get_plant_weekly_capacity() -> float:
    """Return the plant's weekly capacity in kg.

    Reads from ``data/plant_config.json`` if present, else returns the
    default. Wrapped in try/except so a config glitch never crashes the job.
    """
    try:
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "data" / "plant_config.json"
        )
        if path.exists():
            cfg = json.loads(path.read_text(encoding="utf-8"))
            cap = float(cfg.get("weekly_capacity_kg", DEFAULT_PLANT_WEEKLY_CAPACITY_KG))
            return cap
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_plant_weekly_capacity fallback: %s", exc)
    return DEFAULT_PLANT_WEEKLY_CAPACITY_KG


def get_plant_bookings_this_week() -> float:
    """Return total kg already booked into the plant this calendar week."""
    try:
        from api import whatsapp_db
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if not whatsapp_db._table_exists(conn, "farmer_trades"):  # noqa: SLF001
                return 0.0
            # Sum trades booked Mon→today against the Tasgaon plant trader,
            # if a trader_id convention exists. We tolerate the column not
            # being present by catching the OperationalError below.
            today = date.today()
            week_start = today.fromordinal(
                today.toordinal() - today.weekday()
            )
            cur = conn.execute(
                "SELECT COALESCE(SUM(quantity_kg_actual), 0) "
                "FROM farmer_trades "
                "WHERE trade_date >= ? AND region_code = ?",
                (week_start.isoformat(), PLANT_REGION),
            )
            row = cur.fetchone()
            return float(row[0] or 0.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_plant_bookings_this_week fallback: %s", exc)
        return 0.0


def get_available_farms(
    region_code: str = PLANT_REGION,
    commodity: str = PLANT_FARM_COMMODITY,
    harvest_this_week: bool = True,
    grade_predicted: str = "A",
    not_yet_matched: bool = True,
) -> list[dict]:
    """Return Grade-A farmer_lots harvesting this week, not yet matched."""
    try:
        from api import whatsapp_db
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if not whatsapp_db._table_exists(conn, "farmer_lots"):  # noqa: SLF001
                return []
            today = date.today()
            week_start = today.fromordinal(
                today.toordinal() - today.weekday()
            )
            week_end = week_start.fromordinal(week_start.toordinal() + 6)

            sql = (
                "SELECT * FROM farmer_lots WHERE region_code = ? "
                "AND commodity = ? AND grade_predicted = ?"
            )
            params: list[Any] = [region_code, commodity, grade_predicted]
            if harvest_this_week:
                sql += " AND harvest_date_from <= ? AND harvest_date_to >= ?"
                params.extend([week_end.isoformat(), week_start.isoformat()])
            if not_yet_matched:
                sql += " AND status = 'AVAILABLE'"
            sql += " ORDER BY quantity_kg_estimated DESC"

            cur = conn.execute(sql, tuple(params))
            return [
                dict(zip([d[0] for d in cur.description], r))
                for r in cur.fetchall()
            ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_available_farms fallback: %s", exc)
        return []


def get_current_mandi_price(commodity: str, mandi_name: str) -> float:
    """Return the latest closing modal price for the (commodity, mandi)."""
    try:
        from api import whatsapp_db
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if not whatsapp_db._table_exists(conn, "price_history_training"):  # noqa: SLF001
                return 0.0
            cur = conn.execute(
                "SELECT modal_price FROM price_history_training "
                "WHERE commodity = ? AND mandi_name = ? "
                "ORDER BY date DESC LIMIT 1",
                (commodity, mandi_name),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_current_mandi_price fallback: %s", exc)
        return 0.0


def get_processing_fee(activity: str = "Washing, Sorting & Cleaning") -> float:
    """Return the per-kg processing fee from a config table.

    Falls back to :data:`DEFAULT_PROCESSING_FEE_PER_KG` (₹3/kg) when no
    rate sheet is reachable.
    """
    try:
        from api import whatsapp_db
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if whatsapp_db._table_exists(conn, "plant_rates"):  # noqa: SLF001
                cur = conn.execute(
                    "SELECT rate_per_kg FROM plant_rates WHERE activity = ?",
                    (activity,),
                )
                row = cur.fetchone()
                if row:
                    return float(row[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_processing_fee fallback: %s", exc)
    return DEFAULT_PROCESSING_FEE_PER_KG


def send_plant_priority_offer(
    farmer_id: str,
    estimated_kg: float,
    plant_price: float,
    processing_fee: float,
    net_price: float,
    premium: float,
    mandi_price: float,
) -> None:
    """Dispatch the Marathi plant priority offer (SDD §4.6 Flow 2).

    Delegates to :mod:`api.marketplace_whatsapp` when available so the
    Marathi template + region-aware sender are honoured.
    """
    try:
        from api import marketplace_whatsapp

        fn = getattr(marketplace_whatsapp, "send_plant_priority_offer", None)
        if callable(fn):
            fn(
                farmer_id=farmer_id,
                estimated_kg=estimated_kg,
                plant_price=plant_price,
                processing_fee=processing_fee,
                net_price=net_price,
                premium=premium,
                mandi_price=mandi_price,
            )
            return
    except Exception as exc:  # noqa: BLE001
        logger.debug("send_plant_priority_offer delegate failed: %s", exc)

    logger.info(
        "plant offer (stub) farmer=%s est=%skg net=₹%s/kg premium=₹%s/kg",
        farmer_id, estimated_kg, round(net_price, 2), round(premium, 2),
    )


# --------------------------------------------------------------------------- #
# Pure economics
# --------------------------------------------------------------------------- #


def compute_plant_economics(
    mandi_price: float,
    processing_fee: float = DEFAULT_PROCESSING_FEE_PER_KG,
    premium_pct: float = PLANT_PREMIUM_PCT,
) -> dict:
    """Return ``{plant_price, processing_fee, net_to_farmer, premium_vs_mandi}``.

    SDD §4.4 formula, exposed for unit testing without touching the DB.
    """
    plant_price = round(mandi_price * (1.0 + premium_pct), 2)
    net_to_farmer = round(plant_price - processing_fee, 2)
    premium_vs_mandi = round(net_to_farmer - mandi_price, 2)
    return {
        "plant_price": plant_price,
        "processing_fee": round(processing_fee, 2),
        "net_to_farmer": net_to_farmer,
        "premium_vs_mandi": premium_vs_mandi,
        "mandi_price": round(mandi_price, 2),
    }


# --------------------------------------------------------------------------- #
# Main runner
# --------------------------------------------------------------------------- #


def run_plant_priority_queue(today: Optional[date] = None) -> dict:
    """Fill the Tasgaon plant with farm supply before traders get access.

    Returns ``{ "offers_sent": n, "remaining_capacity_kg": x }``.
    """
    capacity = get_plant_weekly_capacity()
    booked = get_plant_bookings_this_week()
    remaining = capacity - booked

    if remaining < MIN_REMAINING_CAPACITY_KG:
        logger.info(
            "plant priority: full for the week (capacity=%s, booked=%s)",
            capacity, booked,
        )
        return {"offers_sent": 0, "remaining_capacity_kg": remaining,
                "status": "PLANT_FULL"}

    farms = get_available_farms(
        region_code=PLANT_REGION,
        commodity=PLANT_FARM_COMMODITY,
        harvest_this_week=True,
        grade_predicted="A",
        not_yet_matched=True,
    )

    mandi_price = get_current_mandi_price(PLANT_COMMODITY, "Tasgaon APMC")
    if mandi_price <= 0:
        logger.info("plant priority: no mandi price available, skipping")
        return {"offers_sent": 0, "remaining_capacity_kg": remaining,
                "status": "NO_MANDI_PRICE"}

    processing_fee = get_processing_fee()
    offers_sent = 0

    for farm in farms:
        if remaining <= 0:
            break
        est_kg = float(farm.get("quantity_kg_estimated") or 0.0)
        if est_kg <= 0:
            continue

        econ = compute_plant_economics(
            mandi_price=mandi_price,
            processing_fee=processing_fee,
            premium_pct=PLANT_PREMIUM_PCT,
        )
        if econ["premium_vs_mandi"] <= 0:
            continue

        send_plant_priority_offer(
            farmer_id=str(farm.get("farmer_id")),
            estimated_kg=est_kg,
            plant_price=econ["plant_price"],
            processing_fee=econ["processing_fee"],
            net_price=econ["net_to_farmer"],
            premium=econ["premium_vs_mandi"],
            mandi_price=econ["mandi_price"],
        )
        offers_sent += 1
        remaining -= est_kg

    return {
        "offers_sent": offers_sent,
        "remaining_capacity_kg": round(remaining, 2),
        "mandi_price": round(mandi_price, 2),
        "status": "OK",
    }


__all__ = [
    "PLANT_PREMIUM_PCT",
    "DEFAULT_PROCESSING_FEE_PER_KG",
    "DEFAULT_PLANT_WEEKLY_CAPACITY_KG",
    "MIN_REMAINING_CAPACITY_KG",
    "compute_plant_economics",
    "run_plant_priority_queue",
    "send_plant_priority_offer",
    "get_available_farms",
    "get_plant_weekly_capacity",
    "get_plant_bookings_this_week",
    "get_current_mandi_price",
    "get_processing_fee",
]
