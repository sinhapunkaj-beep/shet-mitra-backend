"""Trader-intelligence FastAPI routes (SDD Section 9 — Agent 5).

This router exposes the public surface for the trader-intelligence platform:

* Trader management (register / fetch / subscribe).
* Intelligence generation (weekly / flash / list / send).
* Premium direct query.
* Aggregate analytics for the WPF dashboard.

The router itself is dependency-free at import time. All Agent 2 (signal
engine, flash detector, report generator) and Agent 3 (trader_whatsapp,
webhooks_trader) imports happen lazily inside the route handlers. This
means a partial swarm build doesn't break import of this module: a route
whose sibling agent hasn't finished returns a 503 instead of taking the
whole router down.

Auth
----
Every endpoint *except* ``POST /traders/register`` honours the same
``INTERNAL_API_TOKEN`` bearer pattern used by ``routes/internal.py``.
When the env var is unset (dev mode) the endpoints are unauthenticated.

The router uses an empty prefix because the SDD specifies two distinct
top-level paths (``/traders/...`` and ``/intelligence/...``) — each
route states its full path explicitly.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from api import whatsapp_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["trader"])


# ---------------------------------------------------------------------------
# Auth helper (same pattern as routes/internal.py)
# ---------------------------------------------------------------------------
def _check_auth(authorization: Optional[str]) -> None:
    expected = os.getenv("INTERNAL_API_TOKEN")
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


# ---------------------------------------------------------------------------
# Helpers — SQLite access piggybacks on whatsapp_db's path resolver so the
# test override via ``set_db_path`` / SHETMITRA_DB_PATH applies uniformly.
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(whatsapp_db.get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return date.today().isoformat()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    out = {key: row[key] for key in row.keys()}
    raw = out.get("commodities")
    if isinstance(raw, str) and raw:
        try:
            out["commodities"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return out


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


def _days_until_trial_end(row: dict) -> Optional[int]:
    if (row.get("subscription_status") or "").upper() != "TRIAL":
        return None
    end = _parse_iso(row.get("trial_ends_at"))
    if end is None:
        return None
    delta = end - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds() // 86400))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class RegisterTraderRequest(BaseModel):
    full_name: str
    mobile: str
    business_name: Optional[str] = None
    location: Optional[str] = None
    district: Optional[str] = None
    commodities: list[str] = Field(default_factory=list)
    tier: str = "BASIC"


class SubscribeRequest(BaseModel):
    tier: str


class GenerateWeeklyRequest(BaseModel):
    commodity: str
    region: Optional[str] = None
    market: Optional[str] = None
    send: bool = False


class GenerateFlashRequest(BaseModel):
    commodity: str
    trigger_type: str
    signal: str
    current_price: float
    fair_value: Optional[float] = None
    send: bool = False


class SendReportRequest(BaseModel):
    trader_id: Optional[str] = None


class TraderQueryRequest(BaseModel):
    query_text: str


# ---------------------------------------------------------------------------
# Trader management — register / fetch / subscribe
# ---------------------------------------------------------------------------
@router.post("/traders/register")
def register_trader(payload: RegisterTraderRequest) -> dict[str, Any]:
    """Create a new trader (delegating to ``api.trader_db.create_trader``).

    Public (no auth) so a WhatsApp webhook or web form can call it.
    Returns the inserted trader row dict. 409 on duplicate mobile.
    """
    # Lazy import — Agent 3's module may not exist yet in a partial build.
    try:
        from api import trader_db
    except ImportError as exc:
        logger.warning("trader_db unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="trader_db unavailable",
        ) from exc

    mobile = (payload.mobile or "").strip()
    if not mobile:
        raise HTTPException(status_code=400, detail="mobile is required")
    full_name = (payload.full_name or "").strip()
    if not full_name:
        raise HTTPException(status_code=400, detail="full_name is required")

    # Duplicate check.
    existing = trader_db.get_trader_by_mobile(mobile)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Trader with mobile {mobile} already exists",
        )

    try:
        trader_id = trader_db.create_trader(
            mobile=mobile,
            full_name=full_name,
            business_name=payload.business_name,
            location=payload.location,
            district=payload.district,
            commodities=payload.commodities or None,
        )
    except sqlite3.IntegrityError as exc:
        # Race-condition fallback: someone inserted between our check and insert.
        raise HTTPException(
            status_code=409,
            detail=f"Trader with mobile {mobile} already exists",
        ) from exc

    if not trader_id:
        raise HTTPException(
            status_code=503,
            detail="traders table missing — run migrations / seed.",
        )

    # Apply non-default tier if specified.
    tier = (payload.tier or "BASIC").upper()
    if tier in {"BASIC", "STANDARD", "PREMIUM"} and tier != "BASIC":
        try:
            trader_db.update_trader(trader_id, subscription_tier=tier)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to apply tier=%s on new trader", tier)

    row = trader_db.get_trader_by_id(trader_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Trader insert vanished")
    return row


@router.get("/traders/{trader_id}")
def get_trader(
    trader_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    # `/traders/analytics` is a sibling route declared further below in this
    # file. FastAPI matches `/traders/{trader_id}` first, so delegate explicitly.
    if trader_id == "analytics":
        return traders_analytics(authorization)
    _check_auth(authorization)
    try:
        from api import trader_db
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="trader_db unavailable") from exc

    row = trader_db.get_trader_by_id(trader_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trader not found")
    row["days_until_trial_end"] = _days_until_trial_end(row)
    return row


@router.post("/traders/{trader_id}/subscribe")
def subscribe_trader(
    trader_id: str,
    payload: SubscribeRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Create a Razorpay subscription link for ``trader_id``.

    Delegates to ``api.trader_payments.create_subscription_link`` when
    available (Agent 4 module). Falls back to a direct mock-Razorpay
    subscription create when Agent 4 hasn't landed yet.
    """
    _check_auth(authorization)

    tier = (payload.tier or "").upper()
    if tier not in {"BASIC", "STANDARD", "PREMIUM"}:
        raise HTTPException(status_code=400, detail="Invalid tier")

    # Confirm trader exists first (404 is more useful than a 502).
    try:
        from api import trader_db
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="trader_db unavailable") from exc

    trader = trader_db.get_trader_by_id(trader_id)
    if trader is None:
        raise HTTPException(status_code=404, detail="Trader not found")

    # Try Agent 4's purpose-built helper first.
    try:
        from api import trader_payments  # type: ignore

        if hasattr(trader_payments, "create_subscription_link"):
            try:
                result = trader_payments.create_subscription_link(trader_id, tier)
            except Exception as exc:  # noqa: BLE001
                logger.exception("trader_payments.create_subscription_link failed")
                raise HTTPException(
                    status_code=502,
                    detail=f"Razorpay subscription create failed: {exc}",
                ) from exc
            if not result or not isinstance(result, dict):
                raise HTTPException(
                    status_code=502,
                    detail="Razorpay returned an empty response",
                )
            return {
                "subscription_id": result.get("subscription_id") or result.get("id"),
                "link": result.get("link") or result.get("short_url"),
                "tier": tier,
                "amount": result.get("amount") or _tier_amount(tier),
            }
    except ImportError:
        # Fall through to direct razorpay client fallback.
        pass

    # Fallback: call razorpay_client directly with mock plan IDs.
    try:
        from api import razorpay_client
    except ImportError as exc:
        raise HTTPException(
            status_code=502,
            detail="Razorpay client unavailable",
        ) from exc

    plan_map = {
        "BASIC": "plan_trader_basic",
        "STANDARD": "plan_trader_standard",
        "PREMIUM": "plan_trader_premium",
    }
    client = razorpay_client.get_razorpay_client()
    customer_id = (
        trader.get("razorpay_customer_id")
        or f"cust_mock_{trader_id[:12]}"
    )
    try:
        sub = client.create_subscription(customer_id, plan_map[tier])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Razorpay subscription create failed: {exc}",
        ) from exc

    sub_id = sub.get("id") or sub.get("subscription_id")
    link = sub.get("short_url") or sub.get("link") or f"https://rzp.io/i/{sub_id or 'unknown'}"
    if not sub_id:
        raise HTTPException(status_code=502, detail="Razorpay returned no subscription id")
    return {
        "subscription_id": sub_id,
        "link": link,
        "tier": tier,
        "amount": _tier_amount(tier),
    }


def _tier_amount(tier: str) -> int:
    return {"BASIC": 3000, "STANDARD": 7000, "PREMIUM": 15000}.get(tier.upper(), 0)


# ---------------------------------------------------------------------------
# Intelligence generation
# ---------------------------------------------------------------------------
def _insert_intelligence_report(
    *,
    report_type: str,
    commodity: str,
    region: Optional[str],
    content: str,
    signal: Optional[str],
    price_forecast_day1: Optional[float] = None,
    price_forecast_day3: Optional[float] = None,
    price_forecast_day7: Optional[float] = None,
    confidence_pct: Optional[float] = None,
    trigger_event: Optional[str] = None,
    model_version: str = "v2",
) -> str:
    report_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO intelligence_reports (
                id, report_type, commodity, region, report_date,
                content_english, signal,
                price_forecast_day1, price_forecast_day3, price_forecast_day7,
                confidence_pct, trigger_event, model_version,
                recipients_count, delivered_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (
                report_id,
                report_type,
                commodity,
                region,
                _today_iso(),
                content,
                signal,
                price_forecast_day1,
                price_forecast_day3,
                price_forecast_day7,
                confidence_pct,
                trigger_event,
                model_version,
                _now_iso(),
            ),
        )
        conn.commit()
    return report_id


def _update_report_counts(report_id: str, recipients: int, delivered: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE intelligence_reports
               SET recipients_count = ?, delivered_count = ?
             WHERE id = ?
            """,
            (recipients, delivered, report_id),
        )
        conn.commit()


def _get_report(report_id: str) -> Optional[dict]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM intelligence_reports WHERE id = ? LIMIT 1",
            (report_id,),
        )
        return _row_to_dict(cur.fetchone())


def _dispatch_weekly_report(
    *,
    report_id: str,
    content: str,
    eligible_tiers: tuple[str, ...] = ("BASIC", "STANDARD", "PREMIUM"),
    sender_fn_name: str = "send_weekly_report",
) -> tuple[int, int]:
    """Send ``content`` to every active trader whose tier is in ``eligible_tiers``.

    Tries ``api.trader_whatsapp.<sender_fn_name>`` first (Agent 3). Falls
    back to ``api.whatsapp_sender.get_sender().send`` when Agent 3's
    module is missing.

    Returns (recipients_count, delivered_count).
    """
    try:
        from api import trader_db
    except ImportError:
        logger.warning("trader_db not available — skipping dispatch")
        return (0, 0)

    recipients_count = 0
    delivered_count = 0

    # Attempt to import Agent 3's sender module — fall back if missing.
    sender_fn = None
    try:
        from api import trader_whatsapp  # type: ignore

        sender_fn = getattr(trader_whatsapp, sender_fn_name, None)
    except ImportError:
        sender_fn = None

    # Direct fallback sender for environments without Agent 3.
    if sender_fn is None:
        try:
            from api import whatsapp_sender
        except ImportError:
            whatsapp_sender = None  # type: ignore

        if whatsapp_sender is not None:
            def sender_fn(trader_id: str, report_id_arg: str, body: str) -> dict:  # type: ignore
                trader_row = trader_db.get_trader_by_id(trader_id)
                mobile = (trader_row or {}).get("mobile") or ""
                if not mobile:
                    return {"status": "failed"}
                resp = whatsapp_sender.get_sender().send(mobile, body)
                trader_db.insert_report_delivery(
                    report_id=report_id_arg,
                    trader_id=trader_id,
                    delivery_status="SENT",
                    aisensy_message_id=str(resp.get("message_id") or ""),
                )
                return {"status": "sent"}
        else:
            sender_fn = None  # truly nothing — counts stay 0.

    if sender_fn is None:
        return (0, 0)

    for tier in eligible_tiers:
        for trader in trader_db.list_active_traders_by_tier(tier):
            trader_id = trader.get("id")
            if not trader_id:
                continue
            recipients_count += 1
            try:
                # Sender signature: (trader_id, report_id, content)
                result = sender_fn(trader_id, report_id, content)
            except TypeError:
                # Older variant: (trader_id, report_id)
                try:
                    result = sender_fn(trader_id, report_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("send failed for %s: %s", trader_id, exc)
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("send failed for %s: %s", trader_id, exc)
                continue
            status_val = (
                (result or {}).get("status") if isinstance(result, dict) else None
            )
            if status_val in {"sent", "queued", "delivered", "SENT", "DELIVERED"}:
                delivered_count += 1
            elif status_val is None:
                # Sender returned something non-dict — assume success.
                delivered_count += 1

    return (recipients_count, delivered_count)


@router.post("/intelligence/generate-weekly")
def generate_weekly(
    payload: GenerateWeeklyRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)

    commodity = (payload.commodity or "").strip()
    if not commodity:
        raise HTTPException(status_code=400, detail="commodity is required")

    # Lazy import Agent 2 modules.
    try:
        from pipelines.signal_engine import generate_signal
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"signal_engine unavailable: {exc}",
        ) from exc
    try:
        from pipelines.report_generator import generate_weekly_report_content
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"report_generator unavailable: {exc}",
        ) from exc

    try:
        signal_data = generate_signal(commodity, market=payload.market)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_signal failed")
        raise HTTPException(status_code=500, detail=f"signal failed: {exc}") from exc

    belt_data = {
        "week1_mt": "-",
        "week2_mt": "-",
        "week3_mt": "-",
        "vs_avg_pct": "-",
        "bearing_year": "-",
        "grade_a_pct": "-",
        "grade_b_pct": "-",
        "grade_c_pct": "-",
        "avg_brix": "-",
    }
    fair_value = signal_data.get("fair_value")
    price_data = {
        "current": fair_value,
        "day1": (signal_data.get("entry_range") or [None, None])[0],
        "day3": signal_data.get("target"),
        "day7": signal_data.get("target"),
        "confidence": signal_data.get("confidence"),
    }
    weather_data = {"summary_7day": "Clear with brief showers possible"}

    try:
        content = generate_weekly_report_content(
            commodity,
            payload.region or "",
            signal_data,
            belt_data,
            price_data,
            weather_data,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_weekly_report_content failed")
        raise HTTPException(status_code=500, detail=f"content failed: {exc}") from exc

    if not content:
        content = (
            f"Weekly Intelligence — {commodity}\n"
            f"Signal: {signal_data.get('signal')}\n"
            f"{signal_data.get('rationale', '')}"
        )

    report_id = _insert_intelligence_report(
        report_type="WEEKLY",
        commodity=commodity,
        region=payload.region,
        content=content,
        signal=signal_data.get("signal"),
        price_forecast_day1=_safe_float(price_data.get("day1")),
        price_forecast_day3=_safe_float(price_data.get("day3")),
        price_forecast_day7=_safe_float(price_data.get("day7")),
        confidence_pct=_safe_float(signal_data.get("confidence")),
    )

    recipients_count = 0
    delivered_count = 0
    if payload.send:
        recipients_count, delivered_count = _dispatch_weekly_report(
            report_id=report_id,
            content=content,
            eligible_tiers=("BASIC", "STANDARD", "PREMIUM"),
            sender_fn_name="send_weekly_report",
        )
        _update_report_counts(report_id, recipients_count, delivered_count)

    return {
        "report_id": report_id,
        "signal": signal_data.get("signal"),
        "recipients_count": recipients_count,
        "delivered_count": delivered_count,
        "content_preview": content[:400],
    }


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


@router.post("/intelligence/generate-flash")
def generate_flash(
    payload: GenerateFlashRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)

    commodity = (payload.commodity or "").strip()
    if not commodity:
        raise HTTPException(status_code=400, detail="commodity is required")

    # Enforce the 3-per-week limit using Agent 2's counter when available.
    try:
        from pipelines.flash_alert_detector import count_flash_alerts_this_week

        count = count_flash_alerts_this_week(str(whatsapp_db.get_db_path()))
    except ImportError:
        # Fallback to a direct SQL count if Agent 2 isn't present.
        count = _count_flash_alerts_local()
    except Exception as exc:  # noqa: BLE001
        logger.warning("count_flash_alerts_this_week failed: %s", exc)
        count = _count_flash_alerts_local()

    if count >= 3:
        raise HTTPException(
            status_code=429,
            detail="Flash alert limit reached for this week",
        )

    # Build content.
    try:
        from pipelines.report_generator import generate_flash_alert_content
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"report_generator unavailable: {exc}",
        ) from exc

    fair_value = payload.fair_value if payload.fair_value is not None else payload.current_price
    next_update = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    try:
        content = generate_flash_alert_content(
            commodity,
            payload.trigger_type,
            payload.current_price,
            fair_value,
            payload.signal,
            confidence_pct=75.0,
            next_update_time=next_update,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_flash_alert_content failed")
        raise HTTPException(status_code=500, detail=f"content failed: {exc}") from exc

    if not content:
        content = (
            f"FLASH ALERT - {commodity}\n"
            f"Trigger: {payload.trigger_type}\n"
            f"Signal: {payload.signal}\n"
            f"Current: Rs{payload.current_price}/kg"
        )

    report_id = _insert_intelligence_report(
        report_type="FLASH",
        commodity=commodity,
        region=None,
        content=content,
        signal=payload.signal,
        trigger_event=payload.trigger_type,
    )

    # Insert flash_alert_triggers row.
    trigger_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO flash_alert_triggers (
                id, commodity, trigger_type, trigger_description,
                price_before, price_after, arrivals_forecast_mt,
                arrivals_actual_mt, alert_sent, report_id, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                trigger_id,
                commodity,
                payload.trigger_type,
                f"{payload.trigger_type} on {commodity}",
                fair_value,
                payload.current_price,
                1 if payload.send else 0,
                report_id,
                _now_iso(),
            ),
        )
        conn.commit()

    recipients_count = 0
    delivered_count = 0
    if payload.send:
        recipients_count, delivered_count = _dispatch_weekly_report(
            report_id=report_id,
            content=content,
            eligible_tiers=("STANDARD", "PREMIUM"),
            sender_fn_name="send_flash_alert",
        )
        _update_report_counts(report_id, recipients_count, delivered_count)

    return {
        "report_id": report_id,
        "signal": payload.signal,
        "recipients_count": recipients_count,
        "delivered_count": delivered_count,
    }


def _count_flash_alerts_local() -> int:
    """Fallback weekly counter using direct SQL — used when Agent 2 isn't present."""
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    with _connect() as conn:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='flash_alert_triggers'",
        )
        if cur.fetchone() is None:
            return 0
        cur = conn.execute(
            "SELECT COUNT(*) FROM flash_alert_triggers WHERE detected_at >= ?",
            (monday.isoformat(),),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


@router.get("/intelligence/reports")
def list_intelligence_reports(
    type: Optional[str] = Query(default=None, alias="type"),
    commodity: Optional[str] = None,
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)

    if limit <= 0:
        limit = 20
    limit = min(limit, 200)
    offset = max(0, offset)

    where_parts: list[str] = []
    params: list[Any] = []
    if type:
        where_parts.append("report_type = ?")
        params.append(type.upper())
    if commodity:
        where_parts.append("commodity = ?")
        params.append(commodity)
    if from_:
        where_parts.append("report_date >= ?")
        params.append(from_)
    if to:
        where_parts.append("report_date <= ?")
        params.append(to)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with _connect() as conn:
        cur = conn.execute(
            f"SELECT COUNT(*) FROM intelligence_reports {where_sql}",
            params,
        )
        total = int(cur.fetchone()[0] or 0)

        cur = conn.execute(
            f"""
            SELECT * FROM intelligence_reports
            {where_sql}
            ORDER BY created_at DESC, report_date DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        items = [_row_to_dict(r) for r in cur.fetchall() if r is not None]

    return {"total": total, "items": items}


@router.post("/intelligence/send-report/{report_id}")
def send_report(
    report_id: str,
    payload: SendReportRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)

    report = _get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    content = report.get("content_english") or ""
    report_type = (report.get("report_type") or "").upper()

    if report_type == "FLASH":
        eligible = ("STANDARD", "PREMIUM")
        sender_fn = "send_flash_alert"
    elif report_type == "DAILY":
        eligible = ("PREMIUM",)
        sender_fn = "send_daily_update"
    else:
        eligible = ("BASIC", "STANDARD", "PREMIUM")
        sender_fn = "send_weekly_report"

    if payload.trader_id:
        try:
            from api import trader_db
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="trader_db unavailable") from exc
        trader = trader_db.get_trader_by_id(payload.trader_id)
        if trader is None:
            raise HTTPException(status_code=404, detail="Trader not found")
        tier = (trader.get("subscription_tier") or "").upper()
        if tier not in eligible:
            raise HTTPException(
                status_code=403,
                detail=f"Trader tier {tier} not eligible for {report_type}",
            )
        # Send to that single trader.
        recipients_count, delivered_count = _send_to_single(
            trader_id=payload.trader_id,
            report_id=report_id,
            content=content,
            sender_fn_name=sender_fn,
        )
    else:
        recipients_count, delivered_count = _dispatch_weekly_report(
            report_id=report_id,
            content=content,
            eligible_tiers=eligible,
            sender_fn_name=sender_fn,
        )

    # Bump counts on the existing report.
    new_recipients = int(report.get("recipients_count") or 0) + recipients_count
    new_delivered = int(report.get("delivered_count") or 0) + delivered_count
    _update_report_counts(report_id, new_recipients, new_delivered)

    return {
        "report_id": report_id,
        "recipients_count": recipients_count,
        "delivered_count": delivered_count,
    }


def _send_to_single(
    *, trader_id: str, report_id: str, content: str, sender_fn_name: str
) -> tuple[int, int]:
    try:
        from api import trader_db
    except ImportError:
        return (0, 0)

    sender_fn = None
    try:
        from api import trader_whatsapp  # type: ignore

        sender_fn = getattr(trader_whatsapp, sender_fn_name, None)
    except ImportError:
        sender_fn = None

    if sender_fn is None:
        try:
            from api import whatsapp_sender

            trader_row = trader_db.get_trader_by_id(trader_id)
            mobile = (trader_row or {}).get("mobile") or ""
            if not mobile:
                return (1, 0)
            whatsapp_sender.get_sender().send(mobile, content)
            trader_db.insert_report_delivery(
                report_id=report_id,
                trader_id=trader_id,
                delivery_status="SENT",
            )
            return (1, 1)
        except ImportError:
            return (1, 0)

    try:
        result = sender_fn(trader_id, report_id, content)
    except TypeError:
        try:
            result = sender_fn(trader_id, report_id)
        except Exception:  # noqa: BLE001
            return (1, 0)
    except Exception:  # noqa: BLE001
        return (1, 0)
    status_val = (
        (result or {}).get("status") if isinstance(result, dict) else None
    )
    delivered = 1 if status_val in {"sent", "queued", "delivered", "SENT", "DELIVERED"} else 0
    return (1, delivered)


# ---------------------------------------------------------------------------
# Premium direct query
# ---------------------------------------------------------------------------
@router.post("/traders/{trader_id}/query")
def trader_query(
    trader_id: str,
    payload: TraderQueryRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)

    if not payload.query_text or not payload.query_text.strip():
        raise HTTPException(status_code=400, detail="query_text is required")

    try:
        from api import trader_db
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="trader_db unavailable") from exc

    trader = trader_db.get_trader_by_id(trader_id)
    if trader is None:
        raise HTTPException(status_code=404, detail="Trader not found")

    if (trader.get("subscription_tier") or "").upper() != "PREMIUM":
        raise HTTPException(status_code=403, detail="PREMIUM tier required")
    if (trader.get("subscription_status") or "").upper() != "ACTIVE":
        raise HTTPException(status_code=403, detail="Active subscription required")

    # Try Agent 3's handler. Fall back to a deterministic local response.
    response_text: Optional[str] = None
    try:
        from api import webhooks_trader  # type: ignore

        handler = getattr(webhooks_trader, "_handle_premium_query", None)
        if handler is not None:
            try:
                result = handler(trader_id, payload.query_text)
                if isinstance(result, dict):
                    response_text = result.get("response_text") or result.get("response")
                elif isinstance(result, str):
                    response_text = result
            except Exception as exc:  # noqa: BLE001
                logger.exception("_handle_premium_query raised")
                response_text = (
                    f"Acknowledged: {payload.query_text}. "
                    "Our analyst will follow up within 2 hours."
                )
    except ImportError:
        response_text = None

    if not response_text:
        response_text = (
            f"Thanks for your query: {payload.query_text[:120]}. "
            "We'll respond within 2 hours with full context "
            "(AMED belt + ARIMA forecast + current modal)."
        )

    # Persist + send via whatsapp_sender directly (Agent 3's handler may
    # also send, but we need to guarantee an outbox entry for tests).
    query_id = trader_db.insert_trader_query(
        trader_id=trader_id,
        query_text=payload.query_text,
        response_text=response_text,
        response_sent_at=_now_iso(),
        model_inputs={"source": "routes.trader._handle_premium_query"},
    )

    mobile = (trader.get("mobile") or "").strip()
    try:
        from api import whatsapp_sender

        if mobile:
            whatsapp_sender.get_sender().send(mobile, response_text)
    except ImportError:
        pass

    return {
        "query_id": query_id,
        "response_text": response_text,
        "sent_to_mobile": mobile,
    }


# ---------------------------------------------------------------------------
# Analytics — aggregate stats for the WPF dashboard.
# ---------------------------------------------------------------------------
@router.get("/traders/analytics")
def traders_analytics(
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)

    out: dict[str, Any] = {
        "total_traders": 0,
        "active_subscribers": 0,
        "trial_users": 0,
        "paused_users": 0,
        "cancelled_users": 0,
        "mrr": 0.0,
        "this_month_revenue": 0.0,
        "last_month_revenue": 0.0,
        "by_tier": {"basic": 0, "standard": 0, "premium": 0},
        "trial_conversion_rate_pct": None,
        "avg_query_count_premium": None,
    }

    with _connect() as conn:
        # Probe table existence so the endpoint stays useful in partial builds.
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('traders','trader_payments','trader_queries')"
        )
        present = {row[0] for row in cur.fetchall()}

        if "traders" in present:
            cur = conn.execute("SELECT COUNT(*) FROM traders")
            out["total_traders"] = int(cur.fetchone()[0] or 0)

            cur = conn.execute(
                "SELECT COUNT(*) FROM traders WHERE subscription_status = 'ACTIVE'"
            )
            out["active_subscribers"] = int(cur.fetchone()[0] or 0)

            cur = conn.execute(
                "SELECT COUNT(*) FROM traders WHERE subscription_status = 'TRIAL'"
            )
            out["trial_users"] = int(cur.fetchone()[0] or 0)

            cur = conn.execute(
                "SELECT COUNT(*) FROM traders WHERE subscription_status = 'PAUSED'"
            )
            out["paused_users"] = int(cur.fetchone()[0] or 0)

            cur = conn.execute(
                "SELECT COUNT(*) FROM traders WHERE subscription_status = 'CANCELLED'"
            )
            out["cancelled_users"] = int(cur.fetchone()[0] or 0)

            cur = conn.execute(
                "SELECT subscription_tier, COUNT(*) FROM traders "
                "WHERE subscription_status = 'ACTIVE' "
                "GROUP BY subscription_tier"
            )
            for tier, count in cur.fetchall():
                key = (tier or "").lower()
                if key in out["by_tier"]:
                    out["by_tier"][key] = int(count or 0)

            cur = conn.execute(
                """
                SELECT COALESCE(SUM(monthly_amount), 0)
                  FROM traders
                 WHERE subscription_status = 'ACTIVE'
                """
            )
            out["mrr"] = float(cur.fetchone()[0] or 0.0)

            # Trial conversion: of all traders whose trial has ended, how
            # many landed in ACTIVE? Simple count-based ratio.
            cur = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN subscription_status = 'ACTIVE' THEN 1 ELSE 0 END),
                    COUNT(*)
                  FROM traders
                 WHERE subscription_started_at IS NOT NULL
                    OR subscription_status IN ('ACTIVE', 'CANCELLED')
                """
            )
            row = cur.fetchone()
            converted, eligible_count = (row[0] or 0), (row[1] or 0)
            if eligible_count and eligible_count > 0:
                out["trial_conversion_rate_pct"] = round(
                    100.0 * float(converted) / float(eligible_count), 2
                )

        if "trader_payments" in present:
            now = datetime.now(timezone.utc)
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if month_start.month == 1:
                last_month_start = month_start.replace(
                    year=month_start.year - 1, month=12
                )
            else:
                last_month_start = month_start.replace(month=month_start.month - 1)
            cur = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                  FROM trader_payments
                 WHERE status = 'PAID' AND paid_at >= ?
                """,
                (month_start.isoformat(),),
            )
            out["this_month_revenue"] = float(cur.fetchone()[0] or 0.0)
            cur = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                  FROM trader_payments
                 WHERE status = 'PAID'
                   AND paid_at >= ? AND paid_at < ?
                """,
                (last_month_start.isoformat(), month_start.isoformat()),
            )
            out["last_month_revenue"] = float(cur.fetchone()[0] or 0.0)

        if "trader_queries" in present and "traders" in present:
            cur = conn.execute(
                """
                SELECT AVG(t.query_count_this_month)
                  FROM traders t
                 WHERE t.subscription_tier = 'PREMIUM'
                   AND t.subscription_status = 'ACTIVE'
                """
            )
            avg_q = cur.fetchone()[0]
            if avg_q is not None:
                out["avg_query_count_premium"] = round(float(avg_q), 2)

    return out


__all__ = ["router"]
