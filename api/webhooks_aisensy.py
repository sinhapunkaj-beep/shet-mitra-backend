"""AISensy inbound webhook.

Accepts the standard WhatsApp Business API payload shape AISensy mirrors,
extracts the farmer's mobile + message body, and dispatches based on the
active ``collection_flow`` recorded in ``whatsapp_sessions``:

    * ``variety_collection`` -> ``api.webhooks_variety``
    * ``booking``            -> ``api.webhooks_booking``
    * ``registration``       -> stubbed
    * no active session      -> queue a default greeting

Idempotency: incoming message IDs are deduped via an in-process set so
AISensy retries do not double-fire state transitions. Restart resets the
set; production should back this with a real store, but for the swarm
tests in-memory is enough.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from api import webhooks_booking, webhooks_variety, whatsapp_db
from api.whatsapp_sender import get_sender

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/aisensy", tags=["aisensy"])

# TODO: Replace with Redis SET NX for production
# Current in-process set loses state on restart
# Use: redis.set(message_id, 1, ex=86400, nx=True)
_DEDUP_MAX_ENTRIES = 5000
_DEDUP_TTL_SECONDS = 3600
_seen_message_ids: "OrderedDict[str, float]" = OrderedDict()
_seen_lock = threading.Lock()


def _dedup_check(message_id: Optional[str]) -> bool:
    """Return True if the message is new, False if it was already seen.

    Uses a process-local LRU dict (``OrderedDict``) capped at 5000 entries
    with an hourly per-entry expiry. This is a stand-in for the production
    path, which should use ``redis.set(message_id, 1, ex=86400, nx=True)``
    so dedupe state survives restarts and scales horizontally.
    """
    if not message_id:
        # No ID means we can't dedupe — let the caller process it.
        return True
    now = time.monotonic()
    with _seen_lock:
        # Drop expired entries from the front of the LRU.
        cutoff = now - _DEDUP_TTL_SECONDS
        while _seen_message_ids:
            oldest_id, ts = next(iter(_seen_message_ids.items()))
            if ts < cutoff:
                _seen_message_ids.popitem(last=False)
            else:
                break
        if message_id in _seen_message_ids:
            # Refresh recency without flagging as new.
            _seen_message_ids.move_to_end(message_id)
            return False
        _seen_message_ids[message_id] = now
        # Cap size — evict oldest.
        while len(_seen_message_ids) > _DEDUP_MAX_ENTRIES:
            _seen_message_ids.popitem(last=False)
        return True


DEFAULT_GREETING = (
    "नमस्ते! ShetMitra मध्ये आपले स्वागत आहे.\n"
    "Welcome to ShetMitra.\n\n"
    "मेनू / Menu:\n"
    "1) मंडी दर / Mandi prices\n"
    "2) नोंदणी / Registration\n"
    "3) पीक सल्ला / Crop advisory"
)


def _strip_country_code(wa_id: str) -> str:
    if not wa_id:
        return ""
    wa_id = wa_id.strip()
    if wa_id.startswith("+91"):
        return wa_id[3:]
    if len(wa_id) == 12 and wa_id.startswith("91"):
        return wa_id[2:]
    return wa_id


def _extract_payload(payload: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (mobile, body, message_id) from an AISensy/WABA payload."""
    messages = payload.get("messages") or []
    if not messages:
        return None, None, None
    first = messages[0] or {}
    raw_mobile = first.get("from")
    if not raw_mobile:
        contacts = payload.get("contacts") or []
        if contacts:
            raw_mobile = contacts[0].get("wa_id")
    mobile = _strip_country_code(raw_mobile or "")

    text = first.get("text") or {}
    body = text.get("body") if isinstance(text, dict) else None
    if not body and isinstance(first.get("button"), dict):
        body = first["button"].get("text")

    message_id = first.get("id") or first.get("message_id")
    return mobile or None, body, message_id


def _already_processed(message_id: Optional[str]) -> bool:
    if not message_id:
        return False
    # _dedup_check returns True for "new", so invert for "already seen".
    return not _dedup_check(message_id)


@router.post("/incoming")
async def incoming(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    mobile, body, message_id = _extract_payload(payload)
    if not mobile or body is None:
        raise HTTPException(
            status_code=400,
            detail="Payload missing mobile (messages[0].from) or body",
        )

    if _already_processed(message_id):
        return {
            "status": "duplicate",
            "message_id": message_id,
            "mobile": mobile,
        }

    session = whatsapp_db.get_active_session(mobile)

    # Trader-platform routing (additive — purely a sibling of the existing
    # farmer flows). If this mobile is a known trader with no active session
    # yet, hand off to the trader onboarding/state machine. Also, if the
    # mobile is neither a known farmer nor in any active session, treat it
    # as a new trader (per SDD §6.1: first message from unknown mobile
    # routes to TRADER_NEW). Wrapped in a try/except so a missing
    # api.trader_db falls through to the original farmer-greeting logic
    # with a logged warning.
    if not session:
        try:
            from api import trader_db, webhooks_trader  # type: ignore
            is_known_trader = trader_db.is_trader(mobile)
            if is_known_trader:
                result = webhooks_trader.handle_incoming_message(mobile, body)
                # Preserve inner status (e.g. "query_handled", "onboarded") since
                # tests rely on it; only inject `flow` and a fallback status.
                return {"status": "routed", "flow": "trader", **result}
            farmer = whatsapp_db.get_farmer_by_mobile(mobile)
            if farmer is None:
                result = webhooks_trader.handle_incoming_message(mobile, body)
                return {"status": "routed", "flow": "trader", **result}
        except Exception as exc:  # noqa: BLE001 - degrade quietly
            LOG.warning("trader routing unavailable: %s", exc)

    if not session:
        # No active session — queue a default greeting/menu.
        get_sender().send(mobile, DEFAULT_GREETING)
        return {
            "status": "no_active_session",
            "menu_sent": True,
            "mobile": mobile,
        }

    flow = session.get("collection_flow")
    if flow == "variety_collection":
        result = webhooks_variety.handle_incoming_message(mobile, body)
        return {"status": "routed", "flow": flow, **result}
    if flow == "booking":
        result = webhooks_booking.handle_incoming_message(mobile, body)
        return {"status": "routed", "flow": flow, **result}
    if flow == "trader":
        try:
            from api.webhooks_trader import (  # type: ignore
                handle_incoming_message as trader_handler,
            )
            result = trader_handler(mobile, body)
            return {"status": "routed", "flow": flow, **result}
        except Exception as exc:  # noqa: BLE001
            LOG.warning("trader flow unavailable for session: %s", exc)
    if flow == "registration":
        return {
            "status": "registration_not_implemented",
            "mobile": mobile,
        }

    # Unknown flow — fall back to greeting so the farmer is not stranded.
    get_sender().send(mobile, DEFAULT_GREETING)
    return {
        "status": "unknown_flow",
        "flow": flow,
        "menu_sent": True,
        "mobile": mobile,
    }
