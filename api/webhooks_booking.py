"""Booking WhatsApp flow — minimal stub.

Agent 2 owns this file only as a placeholder so the AISensy router has
something concrete to route ``collection_flow='booking'`` to. The full
booking state machine is out of scope for the variety-collection swarm.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks/booking", tags=["booking"])


def handle_incoming_message(mobile: str, body: str) -> dict:
    """Stub: echoes the incoming message back without persisting anything."""
    return {
        "status": "booking_flow_not_implemented",
        "echo": body,
        "mobile": mobile,
    }
