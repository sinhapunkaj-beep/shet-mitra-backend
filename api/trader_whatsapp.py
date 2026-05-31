"""Outbound broadcast senders for the trader-intelligence platform.

Each function picks the right template, enforces tier eligibility (per
SDD §6.2), calls ``get_sender().send(...)`` and persists a row into
``report_deliveries`` so the dashboard can report sent / delivered /
failed counts.

Tier eligibility rules (single source of truth — must match SDD §6.2):

    Weekly report  -> BASIC, STANDARD, PREMIUM
    Flash alert    -> STANDARD, PREMIUM only
    Daily update   -> PREMIUM only
    Pre-season     -> BASIC, STANDARD, PREMIUM
    Sub reminder   -> any
    Pay confirm    -> any
    Trial welcome  -> any
"""

from __future__ import annotations

import logging
from typing import Optional

from api import trader_db, webhooks_trader
from api.whatsapp_sender import get_sender

LOG = logging.getLogger(__name__)


TIER_ELIGIBILITY = {
    "weekly": {"BASIC", "STANDARD", "PREMIUM"},
    "flash": {"STANDARD", "PREMIUM"},
    "daily": {"PREMIUM"},
    "pre_season": {"BASIC", "STANDARD", "PREMIUM"},
}


def _eligible(trader: dict, category: str) -> bool:
    allowed = TIER_ELIGIBILITY.get(category, set())
    return trader.get("subscription_tier") in allowed


def _dispatch_one(
    trader: dict,
    body: str,
    *,
    report_id: Optional[str],
    log_category: str,
) -> dict:
    """Send a single message and persist a delivery row.

    Returns a dict describing the dispatch outcome — callers stitch many
    of these together for batch operations.
    """
    mobile = trader.get("mobile") or ""
    trader_id = trader.get("id")
    if not mobile or not trader_id:
        LOG.warning(
            "trader_whatsapp: %s skipped — missing mobile/id on trader %r",
            log_category,
            trader_id,
        )
        return {
            "status": "skipped",
            "reason": "missing mobile or id",
            "trader_id": trader_id,
        }

    send_result = get_sender().send(mobile, body)
    raw_status = (send_result or {}).get("status")
    is_ok = raw_status in ("queued", "sent", "delivered")
    delivery_status = "SENT" if is_ok else "FAILED"
    aisensy_message_id = (send_result or {}).get("message_id")

    delivery_id = trader_db.insert_report_delivery(
        report_id=report_id,
        trader_id=trader_id,
        delivery_status=delivery_status,
        aisensy_message_id=aisensy_message_id,
    )

    return {
        "status": delivery_status.lower(),
        "trader_id": trader_id,
        "report_id": report_id,
        "delivery_id": delivery_id,
        "send_result": send_result,
    }


# ---------------------------------------------------------------------------
# Public senders
# ---------------------------------------------------------------------------
def send_weekly_report(
    trader_id: str,
    report_id: Optional[str],
    content: str,
) -> dict:
    """Weekly report — eligible to all 3 tiers."""
    trader = trader_db.get_trader_by_id(trader_id)
    if not trader:
        LOG.warning("send_weekly_report: trader %s not found", trader_id)
        return {"status": "skipped", "reason": "trader not found",
                "trader_id": trader_id}
    if not _eligible(trader, "weekly"):
        LOG.warning(
            "send_weekly_report: tier %s not eligible (trader %s)",
            trader.get("subscription_tier"),
            trader_id,
        )
        return {"status": "skipped", "reason": "tier not eligible",
                "trader_id": trader_id}
    return _dispatch_one(
        trader, content, report_id=report_id, log_category="weekly_report"
    )


def send_flash_alert(
    trader_ids: list[str],
    report_id: Optional[str],
    content: str,
) -> list[dict]:
    """Flash alert — STANDARD + PREMIUM only.

    Traders that fail the tier check are skipped with a single warning
    line each (per spec: one warning per filtered recipient).
    """
    results: list[dict] = []
    for trader_id in trader_ids:
        trader = trader_db.get_trader_by_id(trader_id)
        if not trader:
            LOG.warning("send_flash_alert: trader %s not found", trader_id)
            results.append({
                "status": "skipped",
                "reason": "trader not found",
                "trader_id": trader_id,
            })
            continue
        if not _eligible(trader, "flash"):
            LOG.warning(
                "send_flash_alert: filtered out trader %s (tier=%s)",
                trader_id,
                trader.get("subscription_tier"),
            )
            results.append({
                "status": "skipped",
                "reason": "tier not eligible",
                "trader_id": trader_id,
            })
            continue
        results.append(
            _dispatch_one(
                trader, content, report_id=report_id,
                log_category="flash_alert",
            )
        )
    return results


def send_daily_update(trader_id: str, content: str) -> dict:
    """Daily update — PREMIUM only.

    Per spec, daily updates are not tied to a report row, so the
    ``report_id`` on the deliveries row is None.
    """
    trader = trader_db.get_trader_by_id(trader_id)
    if not trader:
        LOG.warning("send_daily_update: trader %s not found", trader_id)
        return {"status": "skipped", "reason": "trader not found",
                "trader_id": trader_id}
    if not _eligible(trader, "daily"):
        LOG.warning(
            "send_daily_update: tier %s not eligible (trader %s)",
            trader.get("subscription_tier"),
            trader_id,
        )
        return {"status": "skipped", "reason": "tier not eligible",
                "trader_id": trader_id}
    return _dispatch_one(
        trader, content, report_id=None, log_category="daily_update"
    )


def send_subscription_reminder(
    trader_id: str, days_remaining: int
) -> dict:
    """Day-25 trial reminder. No tier filter (everyone in trial gets it)."""
    trader = trader_db.get_trader_by_id(trader_id)
    if not trader:
        LOG.warning(
            "send_subscription_reminder: trader %s not found", trader_id
        )
        return {"status": "skipped", "reason": "trader not found",
                "trader_id": trader_id}
    body = webhooks_trader.build_subscription_reminder(
        trader.get("full_name") or "trader", days_remaining
    )
    return _dispatch_one(
        trader, body, report_id=None, log_category="subscription_reminder"
    )


def send_payment_confirmation(
    trader_id: str,
    tier: str,
    amount: float,
    valid_until: str,
) -> dict:
    """Sent after Razorpay webhook flips the trader to ACTIVE."""
    trader = trader_db.get_trader_by_id(trader_id)
    if not trader:
        LOG.warning(
            "send_payment_confirmation: trader %s not found", trader_id
        )
        return {"status": "skipped", "reason": "trader not found",
                "trader_id": trader_id}
    body = webhooks_trader.build_payment_confirmation(
        trader.get("full_name") or "trader", tier, amount, valid_until
    )
    return _dispatch_one(
        trader, body, report_id=None, log_category="payment_confirmation"
    )


def send_trial_welcome(trader_id: str) -> dict:
    """Re-send the SDD §6.1 welcome (handy for admin re-trigger)."""
    trader = trader_db.get_trader_by_id(trader_id)
    if not trader:
        LOG.warning("send_trial_welcome: trader %s not found", trader_id)
        return {"status": "skipped", "reason": "trader not found",
                "trader_id": trader_id}
    body = webhooks_trader._format_welcome(
        trader.get("full_name") or "trader"
    )
    return _dispatch_one(
        trader, body, report_id=None, log_category="trial_welcome"
    )
