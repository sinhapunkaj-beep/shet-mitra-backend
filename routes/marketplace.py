"""routes/marketplace.py — SDD §8

Trader-Farmer Connect marketplace FastAPI surface.

Endpoints (SDD §8):
    POST   /marketplace/lots                        — create farmer lot
    GET    /marketplace/lots                        — list lots (filters)
    POST   /marketplace/requirements                — create trader requirement
    GET    /marketplace/matches/{lot_id}            — matches for a lot
    POST   /marketplace/matches/{match_id}/respond  — farmer / trader response
    POST   /marketplace/trades/confirm              — confirm trade completion
    GET    /marketplace/analytics                   — trade volume / fees
    POST   /internal/run-matching                   — manual matching trigger

The handlers persist through the SQLite mirror (``api.whatsapp_db``)
mirrored to Supabase by the existing CDC scripts. Every DB write
returns the inserted row's UUID. Cross-module imports (matching engine,
WhatsApp dispatchers) are lazy: a partial swarm build degrades to 503
instead of breaking import.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api import whatsapp_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["marketplace"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_table(name: str) -> None:
    """503 the request if the marketplace table is missing (migration 009 not applied)."""
    with whatsapp_db._connect() as conn:  # noqa: SLF001
        if not whatsapp_db._table_exists(conn, name):  # noqa: SLF001
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"table '{name}' not present — apply migration "
                    "009_bagaan_sathi_marketplace.sql first"
                ),
            )


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _parse_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        # accept YYYY-MM-DD or DD/MM-DD/MM-style; we restrict to ISO here
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _generate_lot_ref() -> str:
    """Human-friendly lot ref: ``LOT-YYYYMMDD-XXXX``."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"LOT-{today}-{uuid.uuid4().hex[:6].upper()}"


def _generate_aggregation_ref() -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"AGG-{today}-{uuid.uuid4().hex[:6].upper()}"


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class LotCreate(BaseModel):
    farmer_id: str
    plot_id: Optional[str] = None
    region_code: str = "MH"
    commodity: str
    variety: Optional[str] = None
    quantity_kg_estimated: float = Field(..., gt=0)
    quantity_kg_min_acceptable: Optional[float] = None
    grade_predicted: Optional[str] = None
    brix_estimated_min: Optional[float] = None
    brix_estimated_max: Optional[float] = None
    harvest_date_from: str
    harvest_date_to: str
    farm_district: Optional[str] = None
    farm_state: Optional[str] = None
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None
    min_price_per_kg: Optional[float] = None
    auto_created: bool = False


class RequirementCreate(BaseModel):
    trader_id: str
    region_code: str = "MH"
    commodity: str
    variety: Optional[str] = None
    quantity_kg_min: float = Field(..., gt=0)
    quantity_kg_max: Optional[float] = None
    grade: list[str] = Field(default_factory=lambda: ["A", "B"])
    price_per_kg_offered: Optional[float] = None
    collection_from: str
    collection_to: str
    location_district: Optional[str] = None
    location_state: Optional[str] = None
    farm_pickup: bool = True
    gi_required: bool = False


class MatchResponse(BaseModel):
    actor: str = Field(..., pattern="^(farmer|trader)$")
    response: str = Field(..., pattern="^(ACCEPTED|REJECTED|COUNTER|NEGOTIATING|NO_RESPONSE)$")
    counter_price: Optional[float] = None


class TradeConfirm(BaseModel):
    match_id: str
    actual_price_per_kg: float = Field(..., ge=0)
    actual_quantity_kg: float = Field(..., gt=0)
    payment_mode: Optional[str] = None
    confirmed_by: str = Field(..., pattern="^(farmer|trader|both)$")


# --------------------------------------------------------------------------- #
# Lots
# --------------------------------------------------------------------------- #


@router.post("/marketplace/lots", status_code=201)
def create_lot(payload: LotCreate) -> dict:
    """Create a ``farmer_lots`` row (manual or auto)."""
    _ensure_table("farmer_lots")
    lot_id = str(uuid.uuid4())
    lot_ref = _generate_lot_ref()

    with whatsapp_db._connect() as conn:  # noqa: SLF001
        conn.execute(
            """
            INSERT INTO farmer_lots (
              id, lot_ref, farmer_id, plot_id, region_code, commodity, variety,
              quantity_kg_estimated, quantity_kg_min_acceptable, grade_predicted,
              brix_estimated_min, brix_estimated_max,
              harvest_date_from, harvest_date_to,
              farm_district, farm_state, centroid_lat, centroid_lng,
              min_price_per_kg, auto_created, status, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'AVAILABLE', ?)
            """,
            (
                lot_id, lot_ref, payload.farmer_id, payload.plot_id,
                payload.region_code, payload.commodity, payload.variety,
                payload.quantity_kg_estimated, payload.quantity_kg_min_acceptable,
                payload.grade_predicted, payload.brix_estimated_min,
                payload.brix_estimated_max,
                payload.harvest_date_from, payload.harvest_date_to,
                payload.farm_district, payload.farm_state,
                payload.centroid_lat, payload.centroid_lng,
                payload.min_price_per_kg, 1 if payload.auto_created else 0,
                _now_iso(),
            ),
        )
        conn.commit()

    # Lazy import — run matching after lot creation (Agent 3 builds engine).
    try:
        from pipelines import matching_engine  # type: ignore

        matching_engine.run_daily_matching()
    except Exception as exc:  # noqa: BLE001
        logger.debug("immediate matching skipped: %s", exc)

    return {"id": lot_id, "lot_ref": lot_ref, "status": "AVAILABLE"}


@router.get("/marketplace/lots")
def list_lots(
    region: Optional[str] = None,
    commodity: Optional[str] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    week: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    _ensure_table("farmer_lots")
    sql = "SELECT * FROM farmer_lots WHERE 1=1"
    params: list[Any] = []
    if region:
        sql += " AND region_code = ?"
        params.append(region.upper())
    if commodity:
        sql += " AND commodity = ?"
        params.append(commodity)
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter.upper())
    if week:
        wk = _parse_date(week)
        if wk:
            sql += " AND harvest_date_from <= ? AND harvest_date_to >= ?"
            params.extend([wk + timedelta(days=6), wk])
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with whatsapp_db._connect() as conn:  # noqa: SLF001
        cur = conn.execute(sql, tuple(params))
        return [_row_to_dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Requirements
# --------------------------------------------------------------------------- #


@router.post("/marketplace/requirements", status_code=201)
def create_requirement(payload: RequirementCreate) -> dict:
    _ensure_table("trader_requirements")
    req_id = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    with whatsapp_db._connect() as conn:  # noqa: SLF001
        conn.execute(
            """
            INSERT INTO trader_requirements (
              id, trader_id, region_code, commodity, variety,
              quantity_kg_min, quantity_kg_max, grade, price_per_kg_offered,
              collection_from, collection_to, location_district, location_state,
              farm_pickup, gi_required, status, created_at, expires_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE', ?, ?)
            """,
            (
                req_id, payload.trader_id, payload.region_code,
                payload.commodity, payload.variety,
                payload.quantity_kg_min, payload.quantity_kg_max,
                json.dumps(payload.grade), payload.price_per_kg_offered,
                payload.collection_from, payload.collection_to,
                payload.location_district, payload.location_state,
                1 if payload.farm_pickup else 0,
                1 if payload.gi_required else 0,
                _now_iso(), expires_at,
            ),
        )
        conn.commit()

    return {"id": req_id, "status": "ACTIVE", "expires_at": expires_at}


# --------------------------------------------------------------------------- #
# Matches
# --------------------------------------------------------------------------- #


@router.get("/marketplace/matches/{lot_id}")
def get_matches_for_lot(lot_id: str) -> list[dict]:
    _ensure_table("lot_matches")
    with whatsapp_db._connect() as conn:  # noqa: SLF001
        cur = conn.execute(
            "SELECT * FROM lot_matches WHERE lot_id = ? ORDER BY created_at DESC",
            (lot_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


@router.post("/marketplace/matches/{match_id}/respond")
def respond_to_match(match_id: str, payload: MatchResponse) -> dict:
    _ensure_table("lot_matches")
    now = _now_iso()
    with whatsapp_db._connect() as conn:  # noqa: SLF001
        # Ensure the match exists first.
        cur = conn.execute(
            "SELECT * FROM lot_matches WHERE id = ?", (match_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="match not found")

        if payload.actor == "farmer":
            conn.execute(
                "UPDATE lot_matches SET farmer_response = ?, "
                "farmer_counter_price = ?, farmer_responded_at = ? "
                "WHERE id = ?",
                (payload.response, payload.counter_price, now, match_id),
            )
        else:  # trader
            conn.execute(
                "UPDATE lot_matches SET trader_response = ?, "
                "trader_counter_price = ?, trader_notified_at = ? "
                "WHERE id = ?",
                (payload.response, payload.counter_price, now, match_id),
            )

        # When both sides accept, flip connection_made.
        cur = conn.execute(
            "SELECT farmer_response, trader_response FROM lot_matches WHERE id = ?",
            (match_id,),
        )
        upd = cur.fetchone()
        if (
            upd
            and upd["farmer_response"] == "ACCEPTED"
            and upd["trader_response"] == "ACCEPTED"
        ):
            conn.execute(
                "UPDATE lot_matches SET connection_made = 1, "
                "connection_made_at = ? WHERE id = ?",
                (now, match_id),
            )
        conn.commit()

    return {"id": match_id, "actor": payload.actor, "response": payload.response}


# --------------------------------------------------------------------------- #
# Trade confirmation
# --------------------------------------------------------------------------- #


@router.post("/marketplace/trades/confirm")
def confirm_trade(payload: TradeConfirm) -> dict:
    _ensure_table("farmer_trades")
    _ensure_table("lot_matches")

    with whatsapp_db._connect() as conn:  # noqa: SLF001
        cur = conn.execute(
            "SELECT * FROM lot_matches WHERE id = ?", (payload.match_id,)
        )
        match = cur.fetchone()
        if match is None:
            raise HTTPException(status_code=404, detail="match not found")

        trade_id = str(uuid.uuid4())
        total_value = payload.actual_price_per_kg * payload.actual_quantity_kg
        platform_fee_pct = 2.0
        platform_fee_amount = total_value * platform_fee_pct / 100.0

        confirmed_by_farmer = payload.confirmed_by in ("farmer", "both")
        confirmed_by_trader = payload.confirmed_by in ("trader", "both")

        conn.execute(
            """
            INSERT INTO farmer_trades (
              id, lot_id, farmer_id, trader_id, match_id, region_code,
              commodity, quantity_kg_actual, price_per_kg_actual,
              total_value, trade_date, payment_mode, platform_fee_pct,
              platform_fee_amount, confirmed_by_farmer, confirmed_by_trader,
              created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade_id, match["lot_id"], match["farmer_id"],
                match["trader_id"], payload.match_id,
                match["region_code"] if "region_code" in match.keys() else "MH",
                match["commodity"] if "commodity" in match.keys() else "",
                payload.actual_quantity_kg, payload.actual_price_per_kg,
                total_value, date.today().isoformat(), payload.payment_mode,
                platform_fee_pct, platform_fee_amount,
                1 if confirmed_by_farmer else 0,
                1 if confirmed_by_trader else 0,
                _now_iso(),
            ),
        )
        conn.commit()

    return {
        "id": trade_id,
        "total_value": total_value,
        "platform_fee_amount": platform_fee_amount,
    }


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #


@router.get("/marketplace/analytics")
def get_analytics() -> dict:
    """Aggregate metrics for the WPF dashboard stat cards."""
    out = {
        "active_lots": 0,
        "active_requirements": 0,
        "matches_this_week": 0,
        "trades_completed": 0,
        "platform_fees_this_month": 0.0,
        "total_volume_kg": 0,
    }
    try:
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if whatsapp_db._table_exists(conn, "farmer_lots"):  # noqa: SLF001
                cur = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(quantity_kg_estimated), 0) "
                    "FROM farmer_lots WHERE status='AVAILABLE'"
                )
                row = cur.fetchone()
                out["active_lots"] = int(row[0])
                out["total_volume_kg"] = float(row[1])
            if whatsapp_db._table_exists(conn, "trader_requirements"):  # noqa: SLF001
                cur = conn.execute(
                    "SELECT COUNT(*) FROM trader_requirements "
                    "WHERE status='ACTIVE'"
                )
                out["active_requirements"] = int(cur.fetchone()[0])
            if whatsapp_db._table_exists(conn, "lot_matches"):  # noqa: SLF001
                week_ago = (
                    datetime.now(timezone.utc) - timedelta(days=7)
                ).isoformat()
                cur = conn.execute(
                    "SELECT COUNT(*) FROM lot_matches WHERE created_at >= ?",
                    (week_ago,),
                )
                out["matches_this_week"] = int(cur.fetchone()[0])
            if whatsapp_db._table_exists(conn, "farmer_trades"):  # noqa: SLF001
                cur = conn.execute("SELECT COUNT(*) FROM farmer_trades")
                out["trades_completed"] = int(cur.fetchone()[0])
                month_start = date.today().replace(day=1).isoformat()
                cur = conn.execute(
                    "SELECT COALESCE(SUM(platform_fee_amount), 0) "
                    "FROM farmer_trades WHERE trade_date >= ?",
                    (month_start,),
                )
                out["platform_fees_this_month"] = float(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("analytics aggregation failed: %s", exc)
    return out


# --------------------------------------------------------------------------- #
# Manual matching trigger
# --------------------------------------------------------------------------- #


@router.post("/internal/run-matching")
def run_matching_now() -> dict:
    """Manually trigger the matching engine (Agent 3 owns the engine)."""
    try:
        from pipelines import matching_engine  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"matching_engine not available: {exc}",
        )
    summary = matching_engine.run_daily_matching()
    if summary is None:
        summary = {"status": "ok"}
    return summary
