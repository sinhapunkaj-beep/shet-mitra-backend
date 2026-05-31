"""WhatsApp state machine for the trader-intelligence platform.

Implements the trader-side onboarding + subscription flow described in
SDD §6.1. Public surface:

    * ``start_trader_onboarding(mobile, name=None)`` — creates a trader row
      with status=TRIAL and sends the welcome message. Used both by the
      AISensy router and by tests that want to skip the first-message dance.
    * ``handle_incoming_message(mobile, body)`` — dispatches the body
      against the trader state machine (UPGRADE / B / S / P / CANCEL / etc.).
    * ``build_subscription_reminder(name, days_remaining)`` — exposed as a
      pure template-builder so Agent 4's trial-expiry cron can reuse it.
    * ``build_payment_confirmation(name, tier, amount, valid_until)`` — same.

The state machine is intentionally smaller than the variety webhook —
traders are educated, English-speaking and mostly drive themselves.

States stored on ``whatsapp_sessions.current_step`` with
``collection_flow='trader'``:

    TRADER_NEW              — never seen before (only used transiently)
    TRIAL_ACTIVE            — onboarded, awaiting commands
    AWAITING_TIER_CHOICE    — sent the tier menu, awaiting B/S/P
    AWAITING_PAYMENT        — Razorpay link sent, awaiting payment webhook
    ACTIVE_SUBSCRIBER       — paying customer; PREMIUM can ask queries
    CANCELLED               — opted out
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import trader_db, whatsapp_db
from api.whatsapp_sender import get_sender

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/trader", tags=["trader"])


# ---------------------------------------------------------------------------
# State machine steps
# ---------------------------------------------------------------------------
class Step:
    NEW = "TRADER_NEW"
    TRIAL_ACTIVE = "TRIAL_ACTIVE"
    AWAITING_TIER_CHOICE = "AWAITING_TIER_CHOICE"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    ACTIVE_SUBSCRIBER = "ACTIVE_SUBSCRIBER"
    CANCELLED = "CANCELLED"


COLLECTION_FLOW = "trader"


# Tier metadata. Amounts in INR per month.
TIERS = {
    "BASIC": {"amount": 3000, "label": "BASIC"},
    "STANDARD": {"amount": 7000, "label": "STANDARD"},
    "PREMIUM": {"amount": 15000, "label": "PREMIUM"},
}


# Single-letter aliases — what the SDD asks the trader to reply.
TIER_ALIASES = {
    "B": "BASIC",
    "BASIC": "BASIC",
    "S": "STANDARD",
    "STANDARD": "STANDARD",
    "P": "PREMIUM",
    "PREMIUM": "PREMIUM",
}


# ---------------------------------------------------------------------------
# Templates (SDD §6.1)
# ---------------------------------------------------------------------------
def _format_welcome(name: str) -> str:
    """SDD §6.1 welcome template, name-substituted."""
    return (
        f"Hello {name}!\n"
        "Welcome to ShetMitra Trader Intelligence.\n\n"
        "You have a FREE 4-week trial starting today.\n\n"
        "You will receive:\n"
        "✅ Weekly market report every Monday 6 AM\n"
        "✅ Dry Grapes + Pomegranate intelligence\n"
        "✅ Price forecasts + Buy/Sell/Hold signals\n\n"
        "To upgrade to Standard or Premium reply:\n"
        "UPGRADE\n\n"
        "Questions? Reply anytime.\n"
        "— Sahyadri Krushi Intelligence"
    )


def _format_tier_menu() -> str:
    """SDD §6.1 subscription menu — used both inline (UPGRADE) and by the
    day-25 cron via ``build_subscription_reminder``.
    """
    return (
        "Choose a subscription tier:\n\n"
        "BASIC    — Rs 3,000/month: Reply B\n"
        "STANDARD — Rs 7,000/month: Reply S\n"
        "           (adds flash alerts + all crops)\n"
        "PREMIUM  — Rs 15,000/month: Reply P\n"
        "           (adds daily update + direct queries)\n\n"
        "Reply B / S / P to subscribe.\n"
        "Payment via UPI/card after selection."
    )


def build_subscription_reminder(name: str, days_remaining: int) -> str:
    """SDD §6.1 day-25 trial reminder — exposed for Agent 4's cron."""
    safe_name = name or "trader"
    days_text = f"{days_remaining} days" if days_remaining != 1 else "1 day"
    return (
        f"Hello {safe_name},\n"
        f"Your free trial ends in {days_text}.\n\n"
        "To continue receiving intelligence:\n\n"
        "BASIC    — Rs 3,000/month: Reply B\n"
        "STANDARD — Rs 7,000/month: Reply S\n"
        "           (adds flash alerts + all crops)\n"
        "PREMIUM  — Rs 15,000/month: Reply P\n"
        "           (adds daily update + direct queries)\n\n"
        "Reply B / S / P to subscribe.\n"
        "Payment via UPI/card after selection."
    )


def build_payment_confirmation(
    name: str, tier: str, amount: float, valid_until: str
) -> str:
    """SDD §6.1 / §7.1 confirmation template after a successful payment."""
    safe_name = name or "trader"
    safe_amount = f"Rs {int(amount):,}" if amount else "Rs 0"
    return (
        f"Hello {safe_name},\n"
        f"Payment confirmed. {tier} tier is now ACTIVE.\n\n"
        f"Amount: {safe_amount}/month\n"
        f"Valid until: {valid_until}\n\n"
        "You'll start receiving reports right away.\n"
        "Reply CANCEL anytime to stop.\n"
        "— Sahyadri Krushi Intelligence"
    )


def _format_payment_link(tier: str, amount: float, link: str) -> str:
    safe_amount = f"Rs {int(amount):,}" if amount else "Rs 0"
    return (
        f"You selected {tier} — {safe_amount}/month.\n"
        f"Pay to activate: {link}.\n\n"
        "Recurring monthly. Reply CANCEL anytime."
    )


def _format_cancel_confirmation() -> str:
    return (
        "Subscription cancelled. "
        "You can resume anytime by replying RESUME."
    )


def _format_pause_confirmation() -> str:
    return (
        "Subscription paused. "
        "Reports will stop until you reply RESUME."
    )


def _format_resume_confirmation() -> str:
    return (
        "Welcome back! Your subscription is ACTIVE again."
    )


def _format_help_menu() -> str:
    return (
        "Commands:\n"
        "UPGRADE  — see tier options\n"
        "B / S / P — subscribe to Basic / Standard / Premium\n"
        "PAUSE    — pause reports\n"
        "RESUME   — resume reports\n"
        "CANCEL   — cancel subscription\n\n"
        "PREMIUM subscribers can also send any market question."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _send(to: str, body: str) -> dict:
    return get_sender().send(to, body)


def _upsert_session(
    mobile: str, current_step: str, *, session_data: Optional[dict] = None
) -> None:
    whatsapp_db.upsert_session(
        mobile,
        farmer_id=None,
        current_step=current_step,
        collection_flow=COLLECTION_FLOW,
        session_data=session_data or {},
    )


def _looks_like_name(text: str) -> bool:
    """Cheap heuristic: 2-50 chars, mostly letters, not a command word."""
    if not text:
        return False
    stripped = text.strip()
    if not (2 <= len(stripped) <= 50):
        return False
    upper = stripped.upper()
    if upper in TIER_ALIASES:
        return False
    if upper in {"UPGRADE", "CANCEL", "PAUSE", "RESUME", "HI", "HELLO"}:
        return False
    # Reject if mostly digits.
    digit_count = sum(1 for c in stripped if c.isdigit())
    if digit_count > len(stripped) / 2:
        return False
    return True


def _safe_lazy_import_payments() -> Optional[Any]:
    """Lazy-import Agent 4's payment module; degrade gracefully."""
    try:
        from api import trader_payments  # type: ignore
        return trader_payments
    except Exception as exc:
        LOG.warning(
            "trader_payments unavailable (%s) — using placeholder link",
            exc,
        )
        return None


def _create_subscription_link(trader_id: str, tier: str) -> str:
    """Call into Agent 4's module; fall back to a placeholder link."""
    payments = _safe_lazy_import_payments()
    if payments is None:
        return f"https://rzp.io/test-pending-{trader_id}"
    try:
        result = payments.create_subscription_link(trader_id, tier)
    except Exception as exc:
        LOG.warning(
            "create_subscription_link failed (%s) — using placeholder",
            exc,
        )
        return f"https://rzp.io/test-pending-{trader_id}"
    # Agent 4 may return a string or a dict shape; accept both.
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return (
            result.get("link")
            or result.get("short_url")
            or result.get("url")
            or f"https://rzp.io/test-pending-{trader_id}"
        )
    return f"https://rzp.io/test-pending-{trader_id}"


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def start_trader_onboarding(
    mobile: str, name: Optional[str] = None
) -> dict:
    """Create a new trader row, send welcome message, install session."""
    if not mobile:
        raise ValueError("mobile is required")
    safe_name = (name or "").strip() or "trader"

    existing = trader_db.get_trader_by_mobile(mobile)
    if existing:
        # Idempotent: just send the welcome again and refresh the session.
        trader_id = existing["id"]
    else:
        trader_id = trader_db.create_trader(mobile, full_name=safe_name)

    send_result = _send(mobile, _format_welcome(safe_name))
    _upsert_session(
        mobile,
        Step.TRIAL_ACTIVE,
        session_data={"trader_id": trader_id},
    )
    return {
        "status": "onboarded",
        "trader_id": trader_id,
        "mobile": mobile,
        "next_step": Step.TRIAL_ACTIVE,
        "send_result": send_result,
    }


def handle_incoming_message(mobile: str, body: str) -> dict:
    """Dispatch a single trader-side message through the state machine."""
    if not mobile:
        raise ValueError("mobile is required")
    raw_body = body if body is not None else ""
    stripped = raw_body.strip()
    upper = stripped.upper()

    trader = trader_db.get_trader_by_mobile(mobile)
    if trader is None:
        # First-touch: take the body as the trader's name only if it looks
        # like one. Otherwise default to "trader" and let them update later.
        name_hint = stripped if _looks_like_name(stripped) else None
        return start_trader_onboarding(mobile, name=name_hint)

    trader_id = trader["id"]
    session = whatsapp_db.get_session_by_mobile(mobile) or {}
    current_step = session.get("current_step") or Step.TRIAL_ACTIVE

    # ------------------------------------------------------------------
    # CANCEL — terminal command, valid from any state.
    # ------------------------------------------------------------------
    if upper == "CANCEL":
        payments = _safe_lazy_import_payments()
        if payments is not None and hasattr(payments, "cancel_subscription"):
            try:
                payments.cancel_subscription(trader_id)
            except Exception as exc:
                LOG.warning("cancel_subscription failed: %s", exc)
        trader_db.update_trader(
            trader_id, subscription_status="CANCELLED"
        )
        _send(mobile, _format_cancel_confirmation())
        _upsert_session(mobile, Step.CANCELLED)
        return {
            "status": "cancelled",
            "trader_id": trader_id,
            "next_step": Step.CANCELLED,
        }

    # ------------------------------------------------------------------
    # PAUSE / RESUME — Razorpay subscription state changes.
    # ------------------------------------------------------------------
    if upper == "PAUSE":
        if trader.get("subscription_status") != "ACTIVE":
            _send(
                mobile,
                "PAUSE is only available while a subscription is ACTIVE.",
            )
            return {
                "status": "pause_not_allowed",
                "trader_id": trader_id,
                "next_step": current_step,
            }
        payments = _safe_lazy_import_payments()
        if payments is not None and hasattr(payments, "pause_subscription"):
            try:
                payments.pause_subscription(trader_id)
            except Exception as exc:
                LOG.warning("pause_subscription failed: %s", exc)
        trader_db.update_trader(trader_id, subscription_status="PAUSED")
        _send(mobile, _format_pause_confirmation())
        return {
            "status": "paused",
            "trader_id": trader_id,
            "next_step": current_step,
        }

    if upper == "RESUME":
        payments = _safe_lazy_import_payments()
        if payments is not None and hasattr(payments, "resume_subscription"):
            try:
                payments.resume_subscription(trader_id)
            except Exception as exc:
                LOG.warning("resume_subscription failed: %s", exc)
        trader_db.update_trader(trader_id, subscription_status="ACTIVE")
        _send(mobile, _format_resume_confirmation())
        _upsert_session(mobile, Step.ACTIVE_SUBSCRIBER)
        return {
            "status": "resumed",
            "trader_id": trader_id,
            "next_step": Step.ACTIVE_SUBSCRIBER,
        }

    # ------------------------------------------------------------------
    # UPGRADE — show the tier menu.
    # ------------------------------------------------------------------
    if upper == "UPGRADE":
        _send(mobile, _format_tier_menu())
        _upsert_session(mobile, Step.AWAITING_TIER_CHOICE)
        return {
            "status": "tier_menu_sent",
            "trader_id": trader_id,
            "next_step": Step.AWAITING_TIER_CHOICE,
        }

    # ------------------------------------------------------------------
    # Tier selection — B / S / P (also full words).
    # ------------------------------------------------------------------
    if upper in TIER_ALIASES:
        tier = TIER_ALIASES[upper]
        amount = TIERS[tier]["amount"]
        link = _create_subscription_link(trader_id, tier)
        trader_db.update_trader(trader_id, subscription_tier=tier)
        _send(mobile, _format_payment_link(tier, amount, link))
        _upsert_session(
            mobile,
            Step.AWAITING_PAYMENT,
            session_data={"selected_tier": tier, "link": link},
        )
        return {
            "status": "payment_link_sent",
            "trader_id": trader_id,
            "tier": tier,
            "amount": amount,
            "link": link,
            "next_step": Step.AWAITING_PAYMENT,
        }

    # ------------------------------------------------------------------
    # Free-form text — route PREMIUM queries; otherwise help menu.
    # ------------------------------------------------------------------
    tier = trader.get("subscription_tier")
    status = trader.get("subscription_status")
    is_premium = (
        tier == "PREMIUM"
        and status in ("ACTIVE", "TRIAL")
        and current_step in (Step.ACTIVE_SUBSCRIBER, Step.TRIAL_ACTIVE)
    )
    if is_premium and stripped:
        return _handle_premium_query(trader_id, stripped)

    _send(mobile, _format_help_menu())
    return {
        "status": "help_sent",
        "trader_id": trader_id,
        "next_step": current_step,
    }


# ---------------------------------------------------------------------------
# PREMIUM direct-query handler
# ---------------------------------------------------------------------------
def _gather_query_context(trader: dict) -> dict:
    """Best-effort context for the LLM prompt. Every call is wrapped in a
    try/except so any single missing module doesn't break the response.
    """
    ctx: dict[str, Any] = {
        "trader_commodities": trader.get("commodities") or [],
        "trader_location": trader.get("location"),
    }
    try:
        from services.ceda_service import latest_modal_price  # type: ignore
        ctx["modal_price"] = latest_modal_price(
            ctx["trader_commodities"][0] if ctx["trader_commodities"] else None
        )
    except Exception:
        ctx["modal_price"] = None
    try:
        from pipelines.signal_engine import generate_signal  # type: ignore
        if ctx["trader_commodities"]:
            ctx["signal"] = generate_signal(ctx["trader_commodities"][0])
    except Exception:
        ctx["signal"] = None
    try:
        from services.weather_service import summary_7day  # type: ignore
        ctx["weather"] = summary_7day(ctx["trader_location"])
    except Exception:
        ctx["weather"] = None
    return ctx


def _handle_premium_query(
    trader_id: str,
    query_text: str,
    *,
    llm_client: Optional[Any] = None,
) -> dict:
    """Process a free-form PREMIUM trader question.

    Increments the query counter, gathers as much context as the rest of
    the system exposes, hits ``llm_client.generate(prompt)`` if one is
    supplied, and otherwise returns a deterministic stub response so the
    test suite can exercise the persistence + send paths today.
    """
    trader = trader_db.get_trader_by_id(trader_id)
    if not trader:
        raise HTTPException(
            status_code=404, detail=f"trader {trader_id} not found"
        )
    mobile = trader["mobile"]

    trader_db.increment_query_count(trader_id)

    context = _gather_query_context(trader)
    if llm_client is not None and hasattr(llm_client, "generate"):
        prompt = (
            f"You are ShetMitra's PREMIUM trader assistant.\n"
            f"Trader commodities: {context.get('trader_commodities')}\n"
            f"Modal price: {context.get('modal_price')}\n"
            f"Signal: {context.get('signal')}\n"
            f"Weather: {context.get('weather')}\n\n"
            f"Question: {query_text}\n"
            f"Respond concisely for WhatsApp."
        )
        try:
            response_text = llm_client.generate(prompt)
        except Exception as exc:
            LOG.warning("llm_client.generate failed: %s", exc)
            response_text = _stub_premium_response(query_text)
    else:
        response_text = _stub_premium_response(query_text)

    response_sent_at = _now_iso()
    query_id = trader_db.insert_trader_query(
        trader_id=trader_id,
        query_text=query_text,
        response_text=response_text,
        model_inputs=context,
        response_sent_at=response_sent_at,
    )
    send_result = _send(mobile, response_text)
    _upsert_session(
        mobile,
        Step.ACTIVE_SUBSCRIBER,
        session_data={"last_query_id": query_id},
    )
    return {
        "status": "query_handled",
        "trader_id": trader_id,
        "query_id": query_id,
        "response_text": response_text,
        "sent": send_result,
        "next_step": Step.ACTIVE_SUBSCRIBER,
    }


def _stub_premium_response(query_text: str) -> str:
    """Deterministic fallback used when no LLM client is wired in."""
    truncated = (query_text or "").strip()[:140]
    return (
        "Thanks for your question. Our analyst is reviewing:\n"
        f"\"{truncated}\"\n\n"
        "We'll get back within 2 hours with a detailed answer.\n"
        "— Sahyadri Krushi Intelligence"
    )


# ---------------------------------------------------------------------------
# FastAPI surface
# ---------------------------------------------------------------------------
class IncomingPayload(BaseModel):
    mobile: str
    body: str


@router.post("/incoming")
async def incoming(payload: IncomingPayload) -> dict:
    try:
        return handle_incoming_message(payload.mobile, payload.body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
