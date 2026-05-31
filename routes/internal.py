"""Internal / admin FastAPI router for ShetMitra.

Exposes endpoints intended for the WPF dashboard and the cron runner
rather than the WhatsApp webhook surface.

Endpoints
---------
POST /internal/trigger-variety-collection
    Body: {"farmer_id": "<uuid>"}
    Looks up the farmer's most recent ``amed_readings`` row, builds an
    ``amed_data`` dict shaped like the pipeline result's ``amed`` key,
    and delegates to
    :func:`pipelines.variety_trigger.trigger_variety_collection_if_needed`.

    Returns the structured decision dict.

Auth
----
If the environment variable ``INTERNAL_API_TOKEN`` is set, callers must
present ``Authorization: Bearer <token>``. When the variable is unset
(dev mode) the endpoint is unauthenticated.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from pipelines.cache import DEFAULT_DB_PATH
from pipelines.harvest_trigger import trigger_harvest_collection_if_needed
from pipelines.variety_trigger import trigger_variety_collection_if_needed


router = APIRouter(prefix="/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# Razorpay trader-payment webhook
# ---------------------------------------------------------------------------
# Standalone router for the Razorpay webhook so it isn't shadowed by the
# /internal prefix above. Mounted alongside ``router`` via the same module
# import in main.py (Agent 5's job — this file only declares it).
trader_payment_webhook_router = APIRouter(tags=["razorpay"])


@trader_payment_webhook_router.post("/webhooks/razorpay/trader-payment")
async def razorpay_trader_payment_webhook(request: Request) -> dict[str, Any]:
    """Receive a Razorpay subscription / payment webhook delivery.

    The body is read raw so HMAC verification can run against the
    unaltered bytes. Razorpay sends the signature in
    ``X-Razorpay-Signature`` (case-insensitive). On signature mismatch
    we return HTTP 400 — never 401 — so the gateway retries instead of
    surfacing an auth dialog.
    """
    # Lazy import keeps the route module importable even if Agent 4's
    # payments module is missing during a partial deploy.
    try:
        from api.trader_payments import handle_payment_webhook
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"api.trader_payments not available: {exc}",
        ) from exc

    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}

    result = handle_payment_webhook(
        payload,
        signature=signature,
        raw_body=raw_body,
    )
    if result.get("status") == "invalid_signature":
        raise HTTPException(
            status_code=400,
            detail={"status": "invalid_signature", "event": result.get("event")},
        )
    return result


class TriggerRequest(BaseModel):
    farmer_id: str


class HarvestTriggerRequest(BaseModel):
    farmer_id: str
    plot_id: str | None = None
    season_label: str


def _check_auth(authorization: str | None) -> None:
    expected = os.getenv("INTERNAL_API_TOKEN")
    if not expected:
        # Dev mode — no auth required.
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


def _latest_amed_reading_for_farmer(
    db_path: str, farmer_id: str
) -> dict[str, Any] | None:
    """Return the most recent amed_readings row for any plot belonging to
    ``farmer_id``, shaped to match ``result['amed']`` from the pipeline.

    Returns ``None`` if the farmer has no plot or no amed reading.
    """
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT r.plot_id,
                   r.crop_type_detected,
                   r.crop_type_confidence,
                   r.field_size_acres_amed,
                   r.harvest_date_predicted,
                   r.growth_stage,
                   r.growth_stage_confidence,
                   r.fetch_date
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
            # Provide BOTH key conventions so consumers downstream don't
            # have to translate.
            "crop_type_detected": row[1],
            "crop_type": row[1],
            "crop_type_confidence": row[2],
            "crop_confidence": row[2],
            "field_size_acres": row[3],
            "field_size_acres_amed": row[3],
            "harvest_date_predicted": row[4],
            "growth_stage": row[5],
            "growth_stage_confidence": row[6],
            "fetch_date": row[7],
        }
    finally:
        conn.close()


@router.post("/trigger-variety-collection")
def trigger_variety_collection_endpoint(
    payload: TriggerRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Manually trigger the variety collection flow for one farmer."""
    _check_auth(authorization)

    db_path = os.getenv("SHETMITRA_DB_PATH", DEFAULT_DB_PATH)

    amed_data = _latest_amed_reading_for_farmer(db_path, payload.farmer_id)
    if amed_data is None:
        raise HTTPException(
            status_code=400,
            detail="No AMED data for this farmer yet",
        )

    plot_id = amed_data.get("plot_id")
    decision = trigger_variety_collection_if_needed(
        farmer_id=payload.farmer_id,
        plot_id=plot_id,
        amed_data=amed_data,
        db_path=db_path,
    )
    return decision


@router.post("/trigger-harvest-collection")
def trigger_harvest_collection_endpoint(
    payload: HarvestTriggerRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Manually trigger the harvest-outcome collection for one farmer."""
    _check_auth(authorization)

    db_path = os.getenv("SHETMITRA_DB_PATH", DEFAULT_DB_PATH)

    plot_id = payload.plot_id
    if plot_id is None:
        # Fall back to the farmer's most recent AMED-bearing plot.
        amed = _latest_amed_reading_for_farmer(db_path, payload.farmer_id)
        if amed is None:
            raise HTTPException(
                status_code=400,
                detail="No AMED data for this farmer yet",
            )
        plot_id = amed.get("plot_id")

    decision = trigger_harvest_collection_if_needed(
        farmer_id=payload.farmer_id,
        plot_id=plot_id,
        season_label=payload.season_label,
        db_path=db_path,
    )
    return decision


__all__ = ["router", "trader_payment_webhook_router"]
