"""Razorpay subscription orchestration for the Trader Intelligence platform.

This module is the single entry point for everything money-related on the
trader side of ShetMitra. It is intentionally thin: every Razorpay HTTP
call is delegated to a :class:`api.razorpay_client.RazorpayClient`, and
every DB write goes through parameterised SQL against the local SQLite
mirror of the ``traders`` / ``trader_payments`` tables.

Public surface
--------------
:func:`create_subscription_link`
    Ensure the trader has a Razorpay customer, create a subscription
    against the plan for the chosen tier, persist the result, return
    ``{trader_id, subscription_id, link, tier, amount}``.

:func:`cancel_subscription` / :func:`pause_subscription` / :func:`resume_subscription`
    Mirror the corresponding Razorpay calls + flip the local
    ``subscription_status`` column + send a WhatsApp confirmation
    via Agent 3's senders.

:func:`handle_payment_webhook`
    Inspect a Razorpay webhook payload and update local state +
    fire WhatsApp confirmations. Optionally verifies the signature
    against ``raw_body`` first.

Notes
-----
* Tests inject a :class:`MockRazorpayClient` via the ``client=`` kwarg.
  Production callers pass nothing and pick up the live client through
  :func:`api.razorpay_client.get_razorpay_client`.
* Agent 3's WhatsApp senders are imported lazily inside each entry point
  so this module can be imported and tested even before Agent 3 lands
  ``api/trader_whatsapp.py``.
* The spec's ``traders.subscription_status`` CHECK constraint allows
  ('TRIAL','ACTIVE','PAUSED','CANCELLED'). We use ``'PAUSED'`` for the
  awaiting-payment state to stay within the constraint. The notes column
  records the actual intent. TODO: extend the CHECK constraint to add
  ``'AWAITING_PAYMENT'`` once the swarm can ship a migration 007.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from api.razorpay_client import (
    MOCK_WEBHOOK_TOKEN,
    RazorpayClient,
    get_razorpay_client,
)

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier ↔ plan ↔ price mapping. plan_id is resolved at call time from env so
# the env-var defaults from Agent 1 take effect without re-importing.
# ---------------------------------------------------------------------------
TIER_AMOUNTS = {
    "BASIC": 3000,
    "STANDARD": 7000,
    "PREMIUM": 15000,
}

_TIER_PLAN_ENV = {
    "BASIC": ("RAZORPAY_PLAN_BASIC", "plan_basic_3000_inr_monthly"),
    "STANDARD": ("RAZORPAY_PLAN_STANDARD", "plan_standard_7000_inr_monthly"),
    "PREMIUM": ("RAZORPAY_PLAN_PREMIUM", "plan_premium_15000_inr_monthly"),
}

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = str(REPO_ROOT / "data" / "test.db")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _resolve_plan_id(tier: str) -> str:
    import os

    env_var, default_value = _TIER_PLAN_ENV[tier]
    return (os.getenv(env_var) or default_value).strip()


def _normalize_tier(tier: str) -> str:
    if not tier:
        raise ValueError("tier must be one of BASIC / STANDARD / PREMIUM")
    upper = tier.strip().upper()
    if upper not in TIER_AMOUNTS:
        raise ValueError(
            f"Unknown tier {tier!r}; expected one of {sorted(TIER_AMOUNTS)}"
        )
    return upper


def _resolve_db_path(db_path: Optional[str | Path]) -> str:
    """Pick the SQLite path callers asked for.

    Priority:
        1. Explicit ``db_path`` argument (used by tests).
        2. ``api.whatsapp_db`` override (other agents may have set it).
        3. ``SHETMITRA_DB_PATH`` environment variable.
        4. ``data/test.db`` fallback.
    """
    if db_path:
        return str(db_path)
    try:
        from api import whatsapp_db  # local import keeps import cycles loose

        return str(whatsapp_db.get_db_path())
    except Exception:  # noqa: BLE001
        import os

        return os.getenv("SHETMITRA_DB_PATH", DEFAULT_DB_PATH)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_client(client: Optional[RazorpayClient]) -> RazorpayClient:
    return client if client is not None else get_razorpay_client()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _fetch_trader(conn: sqlite3.Connection, trader_id: str) -> Optional[dict]:
    cur = conn.execute(
        "SELECT * FROM traders WHERE id = ? LIMIT 1",
        (trader_id,),
    )
    return _row_to_dict(cur.fetchone())


def _fetch_trader_by_subscription_id(
    conn: sqlite3.Connection, subscription_id: str
) -> Optional[dict]:
    cur = conn.execute(
        "SELECT * FROM traders WHERE razorpay_subscription_id = ? LIMIT 1",
        (subscription_id,),
    )
    return _row_to_dict(cur.fetchone())


# Lazy import shim for Agent 3's senders. We never raise if the module
# isn't present yet — payment-state changes must succeed even when the
# WhatsApp surface is still being built.
def _try_send(method_name: str, **kwargs: Any) -> None:
    try:
        from api import trader_whatsapp  # type: ignore
    except Exception as exc:  # noqa: BLE001
        LOG.debug(
            "trader_whatsapp not available yet — skipping %s (%s)",
            method_name,
            exc,
        )
        return
    fn = getattr(trader_whatsapp, method_name, None)
    if fn is None:
        LOG.debug(
            "trader_whatsapp.%s not implemented — skipping",
            method_name,
        )
        return
    try:
        fn(**kwargs)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("trader_whatsapp.%s failed: %s", method_name, exc)


# ---------------------------------------------------------------------------
# Public API — subscription lifecycle
# ---------------------------------------------------------------------------
def create_subscription_link(
    trader_id: str,
    tier: str,
    *,
    client: Optional[RazorpayClient] = None,
    db_path: Optional[str | Path] = None,
) -> dict:
    """Create (or refresh) a Razorpay subscription for ``trader_id``.

    Side effects:
        * Lazily provisions a Razorpay customer if the trader does not
          have one yet, storing ``razorpay_customer_id`` on the row.
        * Creates a Razorpay subscription against the per-tier plan id
          resolved from ``RAZORPAY_PLAN_*`` env (defaults to the
          ``plan_<tier>_<amount>_inr_monthly`` strings).
        * Sets ``razorpay_subscription_id``, ``subscription_tier``,
          ``monthly_amount`` and flips ``subscription_status`` to
          ``'PAUSED'`` as a placeholder for the missing
          ``'AWAITING_PAYMENT'`` value (see module docstring TODO).
        * Inserts a ``trader_payments`` row in ``status='PENDING'`` for
          the current month so the deliveries audit trail is complete.

    Returns
    -------
    dict
        ``{trader_id, subscription_id, link, tier, amount}`` — ``link``
        is the short payment URL the WhatsApp message should embed.
    """
    tier = _normalize_tier(tier)
    razorpay = _resolve_client(client)
    db = _resolve_db_path(db_path)

    amount = TIER_AMOUNTS[tier]
    plan_id = _resolve_plan_id(tier)

    with _connect(db) as conn:
        trader = _fetch_trader(conn, trader_id)
        if trader is None:
            raise LookupError(f"trader_id {trader_id!r} not found")

        # 1. Ensure Razorpay customer exists.
        customer_id = (trader.get("razorpay_customer_id") or "").strip()
        if not customer_id:
            cust = razorpay.create_customer(
                full_name=trader.get("full_name") or "",
                mobile=trader.get("mobile") or "",
            )
            customer_id = cust.get("id") or ""
            if not customer_id:
                raise RuntimeError(
                    "Razorpay create_customer returned no id: " + repr(cust)
                )

        # 2. Create the subscription itself.
        sub = razorpay.create_subscription(
            customer_id=customer_id,
            plan_id=plan_id,
            total_count=12,
        )
        subscription_id = sub.get("id") or ""
        if not subscription_id:
            raise RuntimeError(
                "Razorpay create_subscription returned no id: " + repr(sub)
            )
        # Prefer the link the gateway returns. Fall back to the conventional
        # short URL so the mock path still hands the trader a usable string.
        link = (
            sub.get("short_url")
            or sub.get("link")
            or f"https://rzp.io/i/{subscription_id}"
        )

        # 3. Persist on the trader row. AWAITING_PAYMENT not in CHECK constraint
        # so we store PAUSED and capture intent in notes. TODO: migration 007.
        now_iso = _now_iso()
        existing_notes = trader.get("notes") or ""
        annotation = (
            f"[{now_iso[:19]}] AWAITING_PAYMENT for tier={tier} "
            f"(subscription_id={subscription_id})"
        )
        new_notes = (
            f"{existing_notes}\n{annotation}".strip()
            if existing_notes
            else annotation
        )
        conn.execute(
            """
            UPDATE traders
               SET razorpay_customer_id = ?,
                   razorpay_subscription_id = ?,
                   subscription_tier = ?,
                   monthly_amount = ?,
                   subscription_status = 'PAUSED',
                   notes = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (
                customer_id,
                subscription_id,
                tier,
                float(amount),
                new_notes,
                now_iso,
                trader_id,
            ),
        )

        # 4. Pending payment ledger row for this month.
        _insert_pending_payment(
            conn,
            trader_id=trader_id,
            amount=amount,
            razorpay_order_id=subscription_id,
            now_iso=now_iso,
        )
        conn.commit()

    return {
        "trader_id": trader_id,
        "subscription_id": subscription_id,
        "link": link,
        "tier": tier,
        "amount": amount,
    }


def cancel_subscription(
    trader_id: str,
    *,
    client: Optional[RazorpayClient] = None,
    db_path: Optional[str | Path] = None,
) -> dict:
    """Cancel the active subscription for ``trader_id``.

    Sets the local ``subscription_status`` to ``'CANCELLED'`` regardless
    of the Razorpay return so the trader stops receiving reports
    immediately. Fires the WhatsApp cancellation confirmation.
    """
    razorpay = _resolve_client(client)
    db = _resolve_db_path(db_path)

    with _connect(db) as conn:
        trader = _fetch_trader(conn, trader_id)
        if trader is None:
            raise LookupError(f"trader_id {trader_id!r} not found")
        subscription_id = (trader.get("razorpay_subscription_id") or "").strip()

    gateway_response: dict[str, Any] = {}
    if subscription_id:
        gateway_response = razorpay.cancel_subscription(
            subscription_id, cancel_at_cycle_end=True
        )

    with _connect(db) as conn:
        conn.execute(
            """
            UPDATE traders
               SET subscription_status = 'CANCELLED',
                   updated_at = ?
             WHERE id = ?
            """,
            (_now_iso(), trader_id),
        )
        conn.commit()

    _try_send("send_cancellation_confirmation", trader_id=trader_id)
    return {
        "trader_id": trader_id,
        "subscription_id": subscription_id,
        "status": "CANCELLED",
        "gateway": gateway_response,
    }


def pause_subscription(
    trader_id: str,
    *,
    client: Optional[RazorpayClient] = None,
    db_path: Optional[str | Path] = None,
) -> dict:
    """Pause the trader's subscription. WhatsApp deliveries halt while paused."""
    razorpay = _resolve_client(client)
    db = _resolve_db_path(db_path)

    with _connect(db) as conn:
        trader = _fetch_trader(conn, trader_id)
        if trader is None:
            raise LookupError(f"trader_id {trader_id!r} not found")
        subscription_id = (trader.get("razorpay_subscription_id") or "").strip()

    gateway_response: dict[str, Any] = {}
    if subscription_id:
        gateway_response = razorpay.pause_subscription(
            subscription_id, pause_at="now"
        )

    with _connect(db) as conn:
        conn.execute(
            """
            UPDATE traders
               SET subscription_status = 'PAUSED',
                   updated_at = ?
             WHERE id = ?
            """,
            (_now_iso(), trader_id),
        )
        conn.commit()

    _try_send("send_pause_confirmation", trader_id=trader_id)
    return {
        "trader_id": trader_id,
        "subscription_id": subscription_id,
        "status": "PAUSED",
        "gateway": gateway_response,
    }


def resume_subscription(
    trader_id: str,
    *,
    client: Optional[RazorpayClient] = None,
    db_path: Optional[str | Path] = None,
) -> dict:
    """Resume a paused subscription."""
    razorpay = _resolve_client(client)
    db = _resolve_db_path(db_path)

    with _connect(db) as conn:
        trader = _fetch_trader(conn, trader_id)
        if trader is None:
            raise LookupError(f"trader_id {trader_id!r} not found")
        subscription_id = (trader.get("razorpay_subscription_id") or "").strip()

    gateway_response: dict[str, Any] = {}
    if subscription_id:
        gateway_response = razorpay.resume_subscription(
            subscription_id, resume_at="now"
        )

    with _connect(db) as conn:
        conn.execute(
            """
            UPDATE traders
               SET subscription_status = 'ACTIVE',
                   updated_at = ?
             WHERE id = ?
            """,
            (_now_iso(), trader_id),
        )
        conn.commit()

    _try_send("send_resume_confirmation", trader_id=trader_id)
    return {
        "trader_id": trader_id,
        "subscription_id": subscription_id,
        "status": "ACTIVE",
        "gateway": gateway_response,
    }


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------
def handle_payment_webhook(
    payload: dict,
    *,
    signature: Optional[str] = None,
    raw_body: Optional[bytes] = None,
    client: Optional[RazorpayClient] = None,
    db_path: Optional[str | Path] = None,
) -> dict:
    """Process a Razorpay webhook delivery.

    Behavior:
        * If both ``raw_body`` and ``signature`` are provided, verifies
          the signature using the configured client. Returns
          ``{status: 'invalid_signature', ...}`` if verification fails;
          the FastAPI route translates that into HTTP 400.
        * Branches on ``payload['event']``:
            - ``subscription.charged``: flips trader to ACTIVE, resets
              query_count_this_month, marks PENDING ledger row PAID,
              stamps paid_at, fires payment_confirmation WhatsApp.
            - ``subscription.cancelled``: flips trader to CANCELLED.
            - ``payment.failed``: inserts a FAILED ledger row + fires the
              payment_failed WhatsApp. Trader status is left untouched.
            - everything else: returns ``{status: 'ignored', event}``.
    """
    razorpay = _resolve_client(client)
    db = _resolve_db_path(db_path)

    # 1. Signature verification (only when caller supplied both pieces).
    if raw_body is not None and signature is not None:
        if not razorpay.verify_webhook_signature(raw_body, signature):
            return {
                "status": "invalid_signature",
                "event": (payload or {}).get("event"),
                "trader_id": None,
            }

    if not isinstance(payload, dict):
        return {"status": "ignored", "event": None, "trader_id": None}

    event = payload.get("event") or ""
    subscription_id = _extract_subscription_id(payload)
    razorpay_payment_id = _extract_payment_id(payload)
    amount = _extract_amount_inr(payload)

    if event == "subscription.charged":
        return _handle_subscription_charged(
            db,
            subscription_id=subscription_id,
            razorpay_payment_id=razorpay_payment_id,
            amount_inr=amount,
        )
    if event == "subscription.cancelled":
        return _handle_subscription_cancelled(
            db, subscription_id=subscription_id
        )
    if event == "payment.failed":
        return _handle_payment_failed(
            db,
            subscription_id=subscription_id,
            razorpay_payment_id=razorpay_payment_id,
            amount_inr=amount,
        )

    return {
        "status": "ignored",
        "event": event,
        "trader_id": None,
    }


# ---------------------------------------------------------------------------
# Webhook event branches
# ---------------------------------------------------------------------------
def _handle_subscription_charged(
    db: str,
    *,
    subscription_id: str,
    razorpay_payment_id: Optional[str],
    amount_inr: Optional[float],
) -> dict:
    now_iso = _now_iso()
    with _connect(db) as conn:
        trader = (
            _fetch_trader_by_subscription_id(conn, subscription_id)
            if subscription_id
            else None
        )
        if trader is None:
            return {
                "status": "trader_not_found",
                "event": "subscription.charged",
                "trader_id": None,
                "subscription_id": subscription_id,
            }
        trader_id = trader["id"]
        amount = (
            float(amount_inr)
            if amount_inr is not None
            else float(trader.get("monthly_amount") or 0)
        )

        conn.execute(
            """
            UPDATE traders
               SET subscription_status = 'ACTIVE',
                   subscription_renewed_at = ?,
                   subscription_started_at = COALESCE(subscription_started_at, ?),
                   query_count_this_month = 0,
                   updated_at = ?
             WHERE id = ?
            """,
            (now_iso, now_iso, now_iso, trader_id),
        )

        updated = _mark_pending_payment_paid(
            conn,
            trader_id=trader_id,
            amount=amount,
            razorpay_order_id=subscription_id,
            razorpay_payment_id=razorpay_payment_id,
            paid_at=now_iso,
        )
        if not updated:
            _insert_paid_payment(
                conn,
                trader_id=trader_id,
                amount=amount,
                razorpay_order_id=subscription_id,
                razorpay_payment_id=razorpay_payment_id,
                paid_at=now_iso,
            )
        conn.commit()

    _try_send(
        "send_payment_confirmation",
        trader_id=trader_id,
        amount=amount,
        tier=trader.get("subscription_tier"),
    )
    return {
        "status": "ok",
        "event": "subscription.charged",
        "trader_id": trader_id,
        "subscription_id": subscription_id,
    }


def _handle_subscription_cancelled(
    db: str,
    *,
    subscription_id: str,
) -> dict:
    with _connect(db) as conn:
        trader = (
            _fetch_trader_by_subscription_id(conn, subscription_id)
            if subscription_id
            else None
        )
        if trader is None:
            return {
                "status": "trader_not_found",
                "event": "subscription.cancelled",
                "trader_id": None,
                "subscription_id": subscription_id,
            }
        trader_id = trader["id"]
        conn.execute(
            """
            UPDATE traders
               SET subscription_status = 'CANCELLED',
                   updated_at = ?
             WHERE id = ?
            """,
            (_now_iso(), trader_id),
        )
        conn.commit()

    _try_send("send_cancellation_confirmation", trader_id=trader_id)
    return {
        "status": "ok",
        "event": "subscription.cancelled",
        "trader_id": trader_id,
        "subscription_id": subscription_id,
    }


def _handle_payment_failed(
    db: str,
    *,
    subscription_id: str,
    razorpay_payment_id: Optional[str],
    amount_inr: Optional[float],
) -> dict:
    now_iso = _now_iso()
    with _connect(db) as conn:
        trader = (
            _fetch_trader_by_subscription_id(conn, subscription_id)
            if subscription_id
            else None
        )
        if trader is None:
            return {
                "status": "trader_not_found",
                "event": "payment.failed",
                "trader_id": None,
                "subscription_id": subscription_id,
            }
        trader_id = trader["id"]
        amount = (
            float(amount_inr)
            if amount_inr is not None
            else float(trader.get("monthly_amount") or 0)
        )
        _insert_failed_payment(
            conn,
            trader_id=trader_id,
            amount=amount,
            razorpay_order_id=subscription_id,
            razorpay_payment_id=razorpay_payment_id,
            created_at=now_iso,
        )
        conn.commit()

    _try_send(
        "send_payment_failed",
        trader_id=trader_id,
        amount=amount,
        tier=trader.get("subscription_tier"),
    )
    return {
        "status": "ok",
        "event": "payment.failed",
        "trader_id": trader_id,
        "subscription_id": subscription_id,
    }


# ---------------------------------------------------------------------------
# Payload accessors — defensive against missing keys.
# ---------------------------------------------------------------------------
def _payload_root(payload: dict) -> dict:
    inner = payload.get("payload")
    return inner if isinstance(inner, dict) else {}


def _extract_entity(payload: dict, key: str) -> dict:
    inner = _payload_root(payload).get(key)
    if not isinstance(inner, dict):
        return {}
    entity = inner.get("entity")
    return entity if isinstance(entity, dict) else {}


def _extract_subscription_id(payload: dict) -> str:
    sub_entity = _extract_entity(payload, "subscription")
    sub_id = sub_entity.get("id")
    if sub_id:
        return str(sub_id)
    # Fallbacks for payment-only events that still reference a subscription.
    payment_entity = _extract_entity(payload, "payment")
    pay_sub = payment_entity.get("subscription_id") or payment_entity.get(
        "order_id"
    )
    return str(pay_sub) if pay_sub else ""


def _extract_payment_id(payload: dict) -> Optional[str]:
    payment_entity = _extract_entity(payload, "payment")
    pid = payment_entity.get("id")
    return str(pid) if pid else None


def _extract_amount_inr(payload: dict) -> Optional[float]:
    payment_entity = _extract_entity(payload, "payment")
    amount_paise = payment_entity.get("amount")
    if amount_paise is None:
        sub_entity = _extract_entity(payload, "subscription")
        amount_paise = sub_entity.get("amount")
    if amount_paise is None:
        return None
    try:
        return float(amount_paise) / 100.0
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# trader_payments row helpers (parameterised SQL only)
# ---------------------------------------------------------------------------
def _payment_month_iso() -> str:
    """First-of-month ISO date so the unique-per-month invariant holds."""
    today = _now().date()
    return date(today.year, today.month, 1).isoformat()


def _insert_pending_payment(
    conn: sqlite3.Connection,
    *,
    trader_id: str,
    amount: float,
    razorpay_order_id: Optional[str],
    now_iso: str,
) -> str:
    payment_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO trader_payments (
            id, trader_id, amount, currency,
            payment_month, razorpay_order_id,
            razorpay_payment_id, status, paid_at, created_at
        ) VALUES (?, ?, ?, 'INR', ?, ?, NULL, 'PENDING', NULL, ?)
        """,
        (
            payment_id,
            trader_id,
            float(amount),
            _payment_month_iso(),
            razorpay_order_id,
            now_iso,
        ),
    )
    return payment_id


def _insert_paid_payment(
    conn: sqlite3.Connection,
    *,
    trader_id: str,
    amount: float,
    razorpay_order_id: Optional[str],
    razorpay_payment_id: Optional[str],
    paid_at: str,
) -> str:
    payment_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO trader_payments (
            id, trader_id, amount, currency,
            payment_month, razorpay_order_id,
            razorpay_payment_id, status, paid_at, created_at
        ) VALUES (?, ?, ?, 'INR', ?, ?, ?, 'PAID', ?, ?)
        """,
        (
            payment_id,
            trader_id,
            float(amount),
            _payment_month_iso(),
            razorpay_order_id,
            razorpay_payment_id,
            paid_at,
            paid_at,
        ),
    )
    return payment_id


def _insert_failed_payment(
    conn: sqlite3.Connection,
    *,
    trader_id: str,
    amount: float,
    razorpay_order_id: Optional[str],
    razorpay_payment_id: Optional[str],
    created_at: str,
) -> str:
    payment_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO trader_payments (
            id, trader_id, amount, currency,
            payment_month, razorpay_order_id,
            razorpay_payment_id, status, paid_at, created_at
        ) VALUES (?, ?, ?, 'INR', ?, ?, ?, 'FAILED', NULL, ?)
        """,
        (
            payment_id,
            trader_id,
            float(amount),
            _payment_month_iso(),
            razorpay_order_id,
            razorpay_payment_id,
            created_at,
        ),
    )
    return payment_id


def _mark_pending_payment_paid(
    conn: sqlite3.Connection,
    *,
    trader_id: str,
    amount: float,
    razorpay_order_id: Optional[str],
    razorpay_payment_id: Optional[str],
    paid_at: str,
) -> bool:
    """Flip the most recent PENDING row for this trader to PAID.

    Returns True if a row was updated, False if no PENDING row was
    available (callers then insert a fresh PAID row).
    """
    cur = conn.execute(
        """
        SELECT id FROM trader_payments
         WHERE trader_id = ? AND status = 'PENDING'
         ORDER BY created_at DESC LIMIT 1
        """,
        (trader_id,),
    )
    row = cur.fetchone()
    if row is None:
        return False
    conn.execute(
        """
        UPDATE trader_payments
           SET status = 'PAID',
               amount = ?,
               razorpay_order_id = COALESCE(?, razorpay_order_id),
               razorpay_payment_id = ?,
               paid_at = ?
         WHERE id = ?
        """,
        (
            float(amount),
            razorpay_order_id,
            razorpay_payment_id,
            paid_at,
            row["id"],
        ),
    )
    return True


__all__ = [
    "TIER_AMOUNTS",
    "create_subscription_link",
    "cancel_subscription",
    "pause_subscription",
    "resume_subscription",
    "handle_payment_webhook",
    "MOCK_WEBHOOK_TOKEN",
]
