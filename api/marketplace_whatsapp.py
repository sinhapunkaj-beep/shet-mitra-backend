"""Outbound WhatsApp templates for the Trader-Farmer Connect marketplace.

Implements the message-construction + dispatch helpers described in SDD
§4.5 (trader side), §4.6 (farmer side) and §4.7 (trade confirmation /
completion summary). Every outbound message is sent through
``whatsapp_sender.get_sender()`` so tests can swap in a MockSender.

Region-aware branding:
    * Maharashtra (MH) farmers see "ShetMitra" sender + Marathi message
    * Jharkhand (JH) farmers see "Bagaan Sathi" sender + Hindi message
    * All trader-facing messages remain English only

The sender name lookup goes through :func:`api.whatsapp_sender.get_sender_name`
(Agent 5 owns that function). We tolerate its absence with a local stub
that returns the default brand name based on the farmer's region.

NOTE: This module deliberately constructs the message body itself rather
than relying on AiSensy template params — those will be wired up by
Agent 5 once the per-template name registrations exist. For the swarm
build the body is rendered locally and sent as a free-form text message.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from api import whatsapp_db
from api.whatsapp_sender import get_sender

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sender-name resolution (region routing)
# ---------------------------------------------------------------------------
def _resolve_sender_name(farmer_id: Optional[str]) -> str:
    """Resolve the WhatsApp sender brand for this farmer.

    Tries ``api.whatsapp_sender.get_sender_name`` first (Agent 5 owns it).
    Falls back to a local SQLite lookup against the regions / farmers
    tables. Falls back further to "ShetMitra" if nothing resolves.
    """
    if not farmer_id:
        return "ShetMitra"

    try:
        from api.whatsapp_sender import get_sender_name  # type: ignore
        name = get_sender_name(farmer_id)
        if name:
            return name
    except Exception as exc:  # noqa: BLE001 - stub if Agent 5 unfinished
        LOG.debug("get_sender_name unavailable (%s) — using local lookup", exc)

    # Local fallback: read regions table via farmer.region_code.
    try:
        with sqlite3.connect(str(whatsapp_db.get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            # Resolve region_code from farmer
            cur = conn.execute(
                "SELECT region_code FROM farmers WHERE id = ? LIMIT 1",
                (farmer_id,),
            )
            row = cur.fetchone()
            region_code = row["region_code"] if row else None
            if not region_code:
                return "ShetMitra"
            # Resolve sender name from regions table
            cur = conn.execute(
                "SELECT whatsapp_sender_name FROM regions "
                "WHERE region_code = ? LIMIT 1",
                (region_code,),
            )
            r2 = cur.fetchone()
            if r2 and r2["whatsapp_sender_name"]:
                return r2["whatsapp_sender_name"]
            # Hardcoded brand mapping as final fallback.
            return "Bagaan Sathi" if region_code == "JH" else "ShetMitra"
    except Exception as exc:  # noqa: BLE001
        LOG.debug("region lookup failed (%s)", exc)
        return "ShetMitra"


def _resolve_farmer_region(farmer_id: Optional[str]) -> str:
    """Return 'MH' or 'JH'. Defaults to 'MH' when unknown."""
    if not farmer_id:
        return "MH"
    try:
        with sqlite3.connect(str(whatsapp_db.get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT region_code FROM farmers WHERE id = ? LIMIT 1",
                (farmer_id,),
            )
            row = cur.fetchone()
            if row and row["region_code"]:
                return str(row["region_code"]).upper()
    except Exception:  # noqa: BLE001
        pass
    return "MH"


def _resolve_farmer_mobile(farmer_id: Optional[str]) -> Optional[str]:
    if not farmer_id:
        return None
    farmer = whatsapp_db.get_farmer_by_id(farmer_id)
    if farmer:
        return farmer.get("mobile_number")
    return None


def _footer(sender_name: str) -> str:
    return f"— {sender_name}\n   Sahyadri Krushi Intelligence"


def _send(to: str, body: str) -> dict:
    return get_sender().send(to, body)


# ---------------------------------------------------------------------------
# Section 4.6 Flow 1 — Auto-generated farmer trade offer
# ---------------------------------------------------------------------------
def _format_farmer_offer_marathi(
    *,
    farmer_name: str,
    commodity: str,
    grade: str,
    qty: float,
    price: float,
    mandi_price: float,
    date_from: str,
    date_to: str,
    farm_pickup_yn: str,
    trader_business_name: str,
    acres: float,
    est_kg: float,
    estimated_earning: float,
    mandi_earning: float,
    premium_amount: float,
    counter_price: float,
    gi_badge: str = "",
    sender_name: str = "ShetMitra",
) -> str:
    """Marathi (+ inline English) trade-offer template — SDD §4.6 Flow 1 (MH)."""
    gi_line = f"\n   {gi_badge}\n" if gi_badge else "\n"
    return (
        f"नमस्ते {farmer_name} ji! 🌿\n\n"
        f"एक व्यापारी तुमचे {commodity} विकत घेऊ इच्छितो.\n"
        f"A trader wants to buy your {commodity}:\n\n"
        f"🏢 {trader_business_name}\n"
        f"📦 {commodity} Grade {grade} — {qty:g} kg needed\n"
        f"💰 Offering: ₹{price:g}/kg\n"
        f"   (vs mandi today: ₹{mandi_price:g}/kg)\n"
        f"📅 Collection: {date_from} to {date_to}\n"
        f"🚛 Farm pickup: {farm_pickup_yn}"
        f"{gi_line}\n"
        f"तुमची शेती: {acres:g} acres = ~{est_kg:g} kg\n"
        f"अंदाजे कमाई: ₹{estimated_earning:g}\n"
        f"vs मंडी: ₹{mandi_earning:g}\n"
        f"जादा नफा: ₹{premium_amount:g} 💰\n\n"
        f"स्वीकारायचे का?\n"
        f"Reply: YES — जोडण्यासाठी / to connect\n"
        f"Reply: NO — नाकारण्यासाठी / to reject\n"
        f"Reply: PRICE ₹{counter_price:g} — किंमत बदलण्यासाठी\n\n"
        f"{_footer(sender_name)}"
    )


def _format_farmer_offer_hindi(
    *,
    farmer_name: str,
    commodity: str,
    grade: str,
    qty: float,
    price: float,
    mandi_price: float,
    date_from: str,
    date_to: str,
    farm_pickup_yn: str,
    trader_business_name: str,
    acres: float,
    est_kg: float,
    estimated_earning: float,
    premium_amount: float,
    counter_price: float,
    jardalu_gi_badge: str = "",
    sender_name: str = "Bagaan Sathi",
) -> str:
    """Hindi trade-offer template — SDD §4.6 Flow 1 (JH)."""
    gi_line = f"\n   {jardalu_gi_badge}\n" if jardalu_gi_badge else "\n"
    return (
        f"नमस्ते {farmer_name} ji! 🌿\n\n"
        f"एक व्यापारी आपके {commodity} खरीदना चाहते हैं.\n\n"
        f"🏢 {trader_business_name}\n"
        f"📦 {commodity} Grade {grade} — {qty:g} kg\n"
        f"💰 कीमत: ₹{price:g}/kg\n"
        f"   (आज मंडी: ₹{mandi_price:g}/kg)\n"
        f"📅 तारीख: {date_from} से {date_to}\n"
        f"🚛 खेत से लेंगे: {farm_pickup_yn}"
        f"{gi_line}\n"
        f"आपका बाग: {acres:g} एकड़ = ~{est_kg:g} kg\n"
        f"कुल कमाई: ₹{estimated_earning:g}\n"
        f"मंडी से ज्यादा: ₹{premium_amount:g} 💰\n\n"
        f"जवाब दें:\n"
        f"YES — जोड़ने के लिए\n"
        f"NO — मना करने के लिए\n"
        f"PRICE ₹{counter_price:g} — दाम बदलने के लिए\n\n"
        f"{_footer(sender_name)}"
    )


def build_farmer_trade_offer_body(
    farmer_id: str,
    lot: dict,
    requirement: dict,
    match_id: str,
    *,
    mandi_price: Optional[float] = None,
    trader: Optional[dict] = None,
    gi_verified: bool = False,
) -> str:
    """Public template-builder — returns the rendered message body.

    Splits on region: MH -> Marathi, JH -> Hindi. The caller supplies
    the lot / requirement / match dicts; we tolerate missing keys by
    substituting safe defaults.
    """
    region = _resolve_farmer_region(farmer_id)
    sender_name = _resolve_sender_name(farmer_id)

    farmer = whatsapp_db.get_farmer_by_id(farmer_id) or {}
    farmer_name = farmer.get("farmer_full_name") or "Shetkari"

    commodity = (
        lot.get("commodity")
        or requirement.get("commodity")
        or "Crop"
    )
    grade = lot.get("grade_predicted") or "A"
    qty = float(
        requirement.get("quantity_kg_min")
        or lot.get("quantity_kg_estimated")
        or 0
    )
    price = float(requirement.get("price_per_kg_offered") or 0)
    mp = float(mandi_price if mandi_price is not None else 0)
    date_from = str(
        requirement.get("collection_from") or lot.get("harvest_date_from") or ""
    )
    date_to = str(
        requirement.get("collection_to") or lot.get("harvest_date_to") or ""
    )
    farm_pickup_yn = "YES" if requirement.get("farm_pickup") else "NO"

    trader_dict = trader or {}
    trader_business = (
        trader_dict.get("business_name")
        or trader_dict.get("full_name")
        or "Trader"
    )

    acres = float(lot.get("self_reported_acres") or farmer.get("acres") or 1.0)
    est_kg = float(lot.get("quantity_kg_estimated") or 0)
    estimated_earning = price * est_kg if (price and est_kg) else 0.0
    mandi_earning = mp * est_kg if (mp and est_kg) else 0.0
    premium_amount = max(estimated_earning - mandi_earning, 0.0)
    counter_price = max(price + 2.0, price) if price else 0.0

    gi_badge = ""
    if gi_verified and (str(lot.get("variety") or "").lower() == "jardalu"):
        if region == "JH":
            gi_badge = "🏅 Jardalu GI Verified — 1.60x premium eligible"
        else:
            gi_badge = "🏅 GI Verified Lot"

    if region == "JH":
        return _format_farmer_offer_hindi(
            farmer_name=farmer_name,
            commodity=commodity,
            grade=grade,
            qty=qty,
            price=price,
            mandi_price=mp,
            date_from=date_from,
            date_to=date_to,
            farm_pickup_yn=farm_pickup_yn,
            trader_business_name=trader_business,
            acres=acres,
            est_kg=est_kg,
            estimated_earning=estimated_earning,
            premium_amount=premium_amount,
            counter_price=counter_price,
            jardalu_gi_badge=gi_badge,
            sender_name=sender_name,
        )
    return _format_farmer_offer_marathi(
        farmer_name=farmer_name,
        commodity=commodity,
        grade=grade,
        qty=qty,
        price=price,
        mandi_price=mp,
        date_from=date_from,
        date_to=date_to,
        farm_pickup_yn=farm_pickup_yn,
        trader_business_name=trader_business,
        acres=acres,
        est_kg=est_kg,
        estimated_earning=estimated_earning,
        mandi_earning=mandi_earning,
        premium_amount=premium_amount,
        counter_price=counter_price,
        gi_badge=gi_badge,
        sender_name=sender_name,
    )


def send_farmer_trade_offer(
    farmer_id: str,
    lot: dict,
    requirement: dict,
    match_id: str,
    *,
    mandi_price: Optional[float] = None,
    trader: Optional[dict] = None,
    gi_verified: bool = False,
) -> dict:
    """Render + send the Marathi/Hindi trade offer to the farmer.

    Returns a dict with status + the rendered body so callers can inspect.
    """
    body = build_farmer_trade_offer_body(
        farmer_id=farmer_id,
        lot=lot,
        requirement=requirement,
        match_id=match_id,
        mandi_price=mandi_price,
        trader=trader,
        gi_verified=gi_verified,
    )
    mobile = _resolve_farmer_mobile(farmer_id)
    if not mobile:
        LOG.warning(
            "send_farmer_trade_offer: no mobile on farmer %s", farmer_id
        )
        return {
            "status": "skipped",
            "reason": "missing mobile",
            "body": body,
        }
    send_result = _send(mobile, body)
    return {
        "status": "sent",
        "match_id": match_id,
        "mobile": mobile,
        "body": body,
        "send_result": send_result,
    }


# ---------------------------------------------------------------------------
# Section 4.5 Flow 2 — Trader aggregated lot alert (English)
# ---------------------------------------------------------------------------
def _format_trader_lot_alert(aggregation: dict) -> str:
    region = aggregation.get("region_name") or aggregation.get("region_code") or ""
    commodity = aggregation.get("commodity") or ""
    grade = aggregation.get("grade_predicted") or aggregation.get("grade") or "A"
    variety = aggregation.get("variety") or ""
    total_kg = aggregation.get("total_quantity_kg") or aggregation.get("total_kg") or 0
    farm_count = aggregation.get("farm_count") or 0
    week_start = aggregation.get("harvest_week_start") or ""
    week_end = aggregation.get("harvest_week_end") or ""
    district = aggregation.get("district") or ""
    state = aggregation.get("state") or ""
    min_price = aggregation.get("min_price_per_kg") or 0
    bid_suggest = aggregation.get("bid_suggested") or min_price
    return (
        f"🟡 LOT AVAILABLE — {region} {commodity}\n\n"
        f"Grade {grade} — {variety}\n"
        f"Quantity: {float(total_kg):g} kg\n"
        f"{int(farm_count)} farms — satellite verified\n"
        f"Harvest: {week_start} to {week_end}\n"
        f"Location: {district}, {state}\n\n"
        f"Min price: ₹{float(min_price):g}/kg\n\n"
        f"Reply BID ₹{float(bid_suggest):g} to offer\n"
        f"Reply INFO for farm details\n"
        f"Reply SKIP to ignore"
    )


def build_trader_lot_alert_body(aggregation: dict) -> str:
    return _format_trader_lot_alert(aggregation)


def send_trader_lot_alert(trader_id: str, aggregation: dict) -> dict:
    """Broadcast an aggregated-lot alert to one trader (English-only)."""
    try:
        from api import trader_db  # local import to avoid cycle at import time
        trader = trader_db.get_trader_by_id(trader_id) or {}
    except Exception as exc:  # noqa: BLE001
        LOG.warning("trader_db unavailable: %s", exc)
        trader = {}
    mobile = trader.get("mobile")
    body = _format_trader_lot_alert(aggregation)
    if not mobile:
        return {
            "status": "skipped",
            "reason": "missing trader mobile",
            "body": body,
        }
    send_result = _send(mobile, body)
    return {
        "status": "sent",
        "trader_id": trader_id,
        "mobile": mobile,
        "body": body,
        "send_result": send_result,
    }


# ---------------------------------------------------------------------------
# Section 4.5 Flow 3 — Trader direct-match offer (English)
# ---------------------------------------------------------------------------
def build_trader_direct_match_body(
    *,
    commodity: str,
    grade: str,
    first_name: str,
    village: str,
    qty: float,
    harvest_date: str,
    distance_km: float,
    brix_min: float,
    brix_max: float,
    your_price: float,
    farmer_min: float,
    gi_badge: str = "",
) -> str:
    gi_line = f"\n   {gi_badge}" if gi_badge else ""
    return (
        f"✅ FARM MATCH FOUND\n\n"
        f"{commodity} — Grade {grade}\n"
        f"Farmer: {first_name}, {village}\n"
        f"Quantity: {qty:g} kg\n"
        f"Harvest: {harvest_date}\n"
        f"Distance from you: ~{distance_km:g} km\n\n"
        f"Satellite grade: {grade}\n"
        f"Brix estimate: {brix_min:g}-{brix_max:g}"
        f"{gi_line}\n\n"
        f"Offering: ₹{your_price:g}/kg\n"
        f"Farmer minimum: ₹{farmer_min:g}/kg\n\n"
        f"Reply ACCEPT to connect with farmer\n"
        f"Reply COUNTER ₹{your_price:g} to negotiate\n"
        f"Reply SKIP to pass"
    )


# ---------------------------------------------------------------------------
# Section 4.6 Flow 2 — Tasgaon Plant priority offer (Marathi)
# ---------------------------------------------------------------------------
def _format_plant_priority_marathi(
    *,
    farmer_name: str,
    est_kg: float,
    plant_price: float,
    processing_fee: float,
    net_price: float,
    mandi_price: float,
    premium: float,
    total_premium: float,
    sender_name: str = "ShetMitra",
) -> str:
    return (
        f"नमस्ते {farmer_name} ji! 🌿\n\n"
        f"Tasgaon Plant — या हंगामात प्रक्रिया करण्यासाठी\n"
        f"जागा आहे.\n"
        f"Tasgaon Plant has capacity this week:\n\n"
        f"🏭 Activity: Washing + Sorting + Cleaning\n"
        f"🌾 Your estimated lot: {est_kg:g} kg\n"
        f"💰 Plant rate: ₹{plant_price:g}/kg\n"
        f"⚙️ Processing fee: ₹{processing_fee:g}/kg\n"
        f"💵 Net to you: ₹{net_price:g}/kg\n\n"
        f"vs direct mandi: ₹{mandi_price:g}/kg\n"
        f"Extra per kg: ₹{premium:g}/kg\n"
        f"Total extra: ₹{total_premium:g} 💰\n\n"
        f"Slot confirm करायचे का?\n"
        f"Reply: YES — confirm\n"
        f"Reply: NO — skip\n\n"
        f"{_footer(sender_name)}"
    )


def build_plant_priority_offer_body(
    farmer_id: str,
    *,
    est_kg: float,
    plant_price: float,
    processing_fee: float,
    net_price: float,
    mandi_price: float,
    premium: float,
) -> str:
    sender_name = _resolve_sender_name(farmer_id)
    farmer = whatsapp_db.get_farmer_by_id(farmer_id) or {}
    farmer_name = farmer.get("farmer_full_name") or "Shetkari"
    total_premium = premium * est_kg
    return _format_plant_priority_marathi(
        farmer_name=farmer_name,
        est_kg=est_kg,
        plant_price=plant_price,
        processing_fee=processing_fee,
        net_price=net_price,
        mandi_price=mandi_price,
        premium=premium,
        total_premium=total_premium,
        sender_name=sender_name,
    )


def send_plant_priority_offer(
    farmer_id: str,
    *,
    est_kg: float,
    plant_price: float,
    processing_fee: float,
    net_price: float,
    mandi_price: float,
    premium: float,
) -> dict:
    body = build_plant_priority_offer_body(
        farmer_id,
        est_kg=est_kg,
        plant_price=plant_price,
        processing_fee=processing_fee,
        net_price=net_price,
        mandi_price=mandi_price,
        premium=premium,
    )
    mobile = _resolve_farmer_mobile(farmer_id)
    if not mobile:
        return {
            "status": "skipped",
            "reason": "missing mobile",
            "body": body,
        }
    send_result = _send(mobile, body)
    return {
        "status": "sent",
        "farmer_id": farmer_id,
        "mobile": mobile,
        "body": body,
        "send_result": send_result,
    }


# ---------------------------------------------------------------------------
# Section 4.6 Flow 4 — Bid notification (reverse auction)
# ---------------------------------------------------------------------------
def build_bid_notification_body(
    farmer_id: str,
    *,
    commodity: str,
    bid_price: float,
    trader_location: str,
    highest_bid: float,
    close_time: str,
) -> str:
    sender_name = _resolve_sender_name(farmer_id)
    return (
        f"⚡ नवीन बोली / New Bid Received!\n\n"
        f"तुमच्या {commodity} साठी:\n"
        f"For your {commodity} lot:\n\n"
        f"Bid: ₹{bid_price:g}/kg\n"
        f"Bidder: {trader_location}\n\n"
        f"Current highest: ₹{highest_bid:g}/kg\n"
        f"Auction closes: {close_time}\n\n"
        f"अजून बोली येऊ शकतात.\n"
        f"More bids may arrive.\n"
        f"Reply ACCEPT to sell now\n"
        f"Reply WAIT to continue auction\n\n"
        f"{_footer(sender_name)}"
    )


def send_bid_notification(
    farmer_id: str,
    *,
    commodity: str,
    bid_price: float,
    trader_location: str,
    highest_bid: float,
    close_time: str,
) -> dict:
    body = build_bid_notification_body(
        farmer_id,
        commodity=commodity,
        bid_price=bid_price,
        trader_location=trader_location,
        highest_bid=highest_bid,
        close_time=close_time,
    )
    mobile = _resolve_farmer_mobile(farmer_id)
    if not mobile:
        return {"status": "skipped", "reason": "missing mobile", "body": body}
    send_result = _send(mobile, body)
    return {
        "status": "sent",
        "farmer_id": farmer_id,
        "mobile": mobile,
        "body": body,
        "send_result": send_result,
    }


# ---------------------------------------------------------------------------
# Section 4.7 — Trade confirmation requests (7 days after connection)
# ---------------------------------------------------------------------------
def build_trade_confirmation_request_farmer(
    farmer_id: str,
    *,
    farmer_name: str,
    trader_name: str,
    suggested_price: float,
) -> str:
    sender_name = _resolve_sender_name(farmer_id)
    return (
        f"नमस्ते {farmer_name} ji,\n"
        f"{trader_name} सोबत व्यापार झाला का?\n"
        f"Did the trade happen with {trader_name}?\n\n"
        f"Reply: YES ₹{suggested_price:g} — व्यापार झाला\n"
        f"Reply: NO — व्यापार झाला नाही\n"
        f"Reply: PENDING — अजून चालू आहे\n\n"
        f"{_footer(sender_name)}"
    )


def build_trade_confirmation_request_trader(
    *,
    farmer_name: str,
    commodity: str,
    suggested_price: float,
    suggested_qty: float,
) -> str:
    return (
        f"Did you complete the trade with\n"
        f"{farmer_name} for {commodity}?\n\n"
        f"Reply: YES ₹{suggested_price:g} kg{suggested_qty:g}\n"
        f"Reply: NO\n"
        f"Reply: PENDING"
    )


def send_trade_confirmation_request(match_id: str) -> dict:
    """Send the 7-day-after confirmation request to BOTH parties.

    The match_id is looked up against ``lot_matches`` to find the farmer
    and trader. Tolerant of missing rows / tables.
    """
    farmer_id: Optional[str] = None
    trader_id: Optional[str] = None
    commodity: str = ""
    suggested_price: float = 0.0
    suggested_qty: float = 0.0

    try:
        with sqlite3.connect(str(whatsapp_db.get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT m.*, l.commodity, l.quantity_kg_estimated, "
                "r.price_per_kg_offered "
                "FROM lot_matches m "
                "LEFT JOIN farmer_lots l ON m.lot_id = l.id "
                "LEFT JOIN trader_requirements r ON m.requirement_id = r.id "
                "WHERE m.id = ? LIMIT 1",
                (match_id,),
            )
            row = cur.fetchone()
            if row:
                farmer_id = row["farmer_id"]
                trader_id = row["trader_id"]
                commodity = row["commodity"] or ""
                suggested_price = float(row["price_per_kg_offered"] or 0)
                suggested_qty = float(row["quantity_kg_estimated"] or 0)
    except sqlite3.OperationalError as exc:
        LOG.warning("lot_matches not available: %s", exc)

    results: dict[str, Any] = {"match_id": match_id, "sent": []}

    # Farmer leg
    if farmer_id:
        farmer = whatsapp_db.get_farmer_by_id(farmer_id) or {}
        farmer_mobile = farmer.get("mobile_number")
        farmer_name = farmer.get("farmer_full_name") or "Shetkari"
        trader_name = "Trader"
        try:
            from api import trader_db
            trader = trader_db.get_trader_by_id(trader_id) if trader_id else None
            if trader:
                trader_name = (
                    trader.get("business_name")
                    or trader.get("full_name")
                    or "Trader"
                )
        except Exception:  # noqa: BLE001
            pass
        body = build_trade_confirmation_request_farmer(
            farmer_id,
            farmer_name=farmer_name,
            trader_name=trader_name,
            suggested_price=suggested_price,
        )
        if farmer_mobile:
            send_result = _send(farmer_mobile, body)
            results["sent"].append({
                "to": "farmer",
                "mobile": farmer_mobile,
                "body": body,
                "send_result": send_result,
            })

    # Trader leg
    if trader_id:
        try:
            from api import trader_db
            trader = trader_db.get_trader_by_id(trader_id)
        except Exception:  # noqa: BLE001
            trader = None
        if trader:
            farmer = whatsapp_db.get_farmer_by_id(farmer_id) if farmer_id else None
            farmer_name = (
                (farmer or {}).get("farmer_full_name") or "Farmer"
            )
            body = build_trade_confirmation_request_trader(
                farmer_name=farmer_name,
                commodity=commodity,
                suggested_price=suggested_price,
                suggested_qty=suggested_qty,
            )
            trader_mobile = trader.get("mobile")
            if trader_mobile:
                send_result = _send(trader_mobile, body)
                results["sent"].append({
                    "to": "trader",
                    "mobile": trader_mobile,
                    "body": body,
                    "send_result": send_result,
                })

    results["status"] = "sent" if results["sent"] else "skipped"
    return results


# ---------------------------------------------------------------------------
# Section 4.7 — Trade completion summary (achievement message)
# ---------------------------------------------------------------------------
def build_trade_completion_summary_body(
    farmer_id: str,
    *,
    grade: str,
    commodity: str,
    price: float,
    mandi_price: float,
    premium_total: float,
) -> str:
    sender_name = _resolve_sender_name(farmer_id)
    return (
        f"✅ व्यापार नोंदवला!\n"
        f"Grade {grade} {commodity}: ₹{price:g}/kg\n"
        f"vs mandi: ₹{mandi_price:g}/kg\n"
        f"तुम्ही ₹{premium_total:g} जास्त मिळवले! 🎉\n\n"
        f"{_footer(sender_name)}"
    )


def send_trade_completion_summary(trade_id: str) -> dict:
    """Look up the farmer_trades row and send the achievement summary."""
    farmer_id: Optional[str] = None
    grade: str = "A"
    commodity: str = ""
    price: float = 0.0
    mandi_price: float = 0.0
    premium_total: float = 0.0

    try:
        with sqlite3.connect(str(whatsapp_db.get_db_path())) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT t.*, l.grade_predicted "
                "FROM farmer_trades t "
                "LEFT JOIN farmer_lots l ON t.lot_id = l.id "
                "WHERE t.id = ? LIMIT 1",
                (trade_id,),
            )
            row = cur.fetchone()
            if row:
                farmer_id = row["farmer_id"]
                commodity = row["commodity"] or ""
                grade = row["grade_predicted"] or "A"
                price = float(row["price_per_kg_actual"] or 0)
                mandi_price = float(row["mandi_price_same_day"] or 0)
                qty = float(row["quantity_kg_actual"] or 0)
                premium_total = max((price - mandi_price) * qty, 0.0)
    except sqlite3.OperationalError as exc:
        LOG.warning("farmer_trades unavailable: %s", exc)

    if not farmer_id:
        return {"status": "skipped", "reason": "no trade row"}

    body = build_trade_completion_summary_body(
        farmer_id,
        grade=grade,
        commodity=commodity,
        price=price,
        mandi_price=mandi_price,
        premium_total=premium_total,
    )
    mobile = _resolve_farmer_mobile(farmer_id)
    if not mobile:
        return {"status": "skipped", "reason": "missing mobile", "body": body}
    send_result = _send(mobile, body)
    return {
        "status": "sent",
        "trade_id": trade_id,
        "mobile": mobile,
        "body": body,
        "send_result": send_result,
    }


__all__ = [
    "build_farmer_trade_offer_body",
    "send_farmer_trade_offer",
    "build_trader_lot_alert_body",
    "send_trader_lot_alert",
    "build_trader_direct_match_body",
    "build_plant_priority_offer_body",
    "send_plant_priority_offer",
    "build_bid_notification_body",
    "send_bid_notification",
    "build_trade_confirmation_request_farmer",
    "build_trade_confirmation_request_trader",
    "send_trade_confirmation_request",
    "build_trade_completion_summary_body",
    "send_trade_completion_summary",
]
