"""WhatsApp variety-collection state machine.

Implements the 8-state conversation from the spec:

    VARIETY_COLLECTION_TRIGGER  (kicks off the flow, sends initial msg)
    AWAITING_VARIETY            -> AWAITING_NAME
    AWAITING_NAME               -> AWAITING_PHONE
    AWAITING_PHONE              -> AWAITING_VILLAGE
    AWAITING_VILLAGE            -> AWAITING_ACRES
    AWAITING_ACRES              -> AWAITING_MISMATCH_RESOLUTION
                                   or COLLECTION_COMPLETE
    AWAITING_MISMATCH_RESOLUTION -> COLLECTION_COMPLETE

Public entry points:
    * ``start_variety_collection(...)`` — called by Agent 3's pipeline
      trigger once AMED has detected a crop.
    * ``handle_incoming_message(mobile, body)`` — dispatched by the
      AISensy webhook for every farmer reply.

All outbound messages go through ``whatsapp_sender.get_sender()`` so
tests can swap in a ``MockSender`` and assert on the outbox.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import whatsapp_db
from api.whatsapp_sender import get_region_code, get_sender
from pipelines import i18n

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/variety", tags=["variety"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VARIETY_EXAMPLES = {
    "Grapes": "Thompson Seedless, Sharad Seedless, Bangalore Blue, Flame Seedless, Sonaka",
    "Pomegranate": "Bhagwa, Ganesh, Mridula, Arakta",
    "Mango": "Alphonso, Kesar, Dasheri, Langra, Totapuri",
}


class Step:
    TRIGGER = "VARIETY_COLLECTION_TRIGGER"
    VARIETY = "AWAITING_VARIETY"
    NAME = "AWAITING_NAME"
    PHONE = "AWAITING_PHONE"
    VILLAGE = "AWAITING_VILLAGE"
    ACRES = "AWAITING_ACRES"
    MISMATCH = "AWAITING_MISMATCH_RESOLUTION"
    COMPLETE = "COLLECTION_COMPLETE"


REPO_ROOT = Path(__file__).resolve().parent.parent
FIELD_CHANGE_LOG = REPO_ROOT / "data" / "field_changes.jsonl"
PANKAJ_DEFAULT_MOBILE = "9999999999"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_field_change(
    farmer_id: str, field: str, old: Any, new: Any
) -> None:
    """Append a JSON-line entry to ``data/field_changes.jsonl``."""
    FIELD_CHANGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _now_iso(),
        "farmer_id": farmer_id,
        "field": field,
        "old": old,
        "new": new,
    }
    line = json.dumps(record, ensure_ascii=False)
    with FIELD_CHANGE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _send(to: str, body: str) -> dict:
    return get_sender().send(to, body)


def _normalise(text: str) -> str:
    return (text or "").strip()


def _is_purely_numeric(text: str) -> bool:
    stripped = _normalise(text)
    if not stripped:
        return False
    try:
        float(stripped)
        return True
    except ValueError:
        return False


def _resolve_language(farmer_id: Optional[str]) -> str:
    """Return the farmer-facing message language (e.g. 'Hindi' for JH,
    'Marathi' for MH). Falls back to Marathi when the region cannot be
    resolved so existing MH behaviour is preserved.
    """
    try:
        region_code = get_region_code(farmer_id) if farmer_id else None
    except Exception:  # noqa: BLE001 - DB/network errors must not crash.
        region_code = None
    if not region_code:
        return i18n.DEFAULT_LANGUAGE
    return i18n.language_for_region(region_code)


def _format_initial_message(
    farmer_name: str, amed_crop: str, language: str = i18n.DEFAULT_LANGUAGE
) -> str:
    examples = VARIETY_EXAMPLES.get(
        amed_crop, "(कृपया जात पाठवा / please share the variety)"
    )
    if (language or "").strip().lower() == "hindi":
        # JH farmers get the Hindi prompts from data/translations/hindi.json
        # (Devanagari only; English equivalents live in english.json).
        return (
            f"{i18n.get_message('greeting', 'Hindi', name=farmer_name)}\n\n"
            f"{i18n.get_message('satellite_detected', 'Hindi', crop=amed_crop)}\n"
            f"(Satellite has detected {amed_crop} on your bagaan.)\n\n"
            f"{i18n.get_message('please_share_details', 'Hindi')}\n"
            f"(Please share more details:)\n\n"
            f"1️⃣ {amed_crop} की किस्म / Variety\n"
            f"   उदा. / e.g.:\n"
            f"   {examples}\n\n"
            f"{i18n.get_message('send_variety_first', 'Hindi')} / Send variety first."
        )
    return (
        f"नमस्ते {farmer_name} ji! \U0001F33F\n\n"
        f"आमच्या उपग्रहाने तुमच्या शेतात\n"
        f"{amed_crop} ओळखली आहेत.\n"
        f"(Satellite has detected {amed_crop} on your farm.)\n\n"
        f"तुमच्या शेताबद्दल अधिक माहिती द्या:\n"
        f"(Please share more details:)\n\n"
        f"1️⃣ {amed_crop} ची जात / Variety\n"
        f"   उदा. / e.g.:\n"
        f"   {examples}\n\n"
        f"पहिले जात पाठवा. / Send variety first."
    )


def _is_hindi(language: str) -> bool:
    return (language or "").strip().lower() == "hindi"


def _format_variety_saved(variety: str, language: str = i18n.DEFAULT_LANGUAGE) -> str:
    if _is_hindi(language):
        return (
            f"{i18n.get_message('variety_saved', 'Hindi', variety=variety)} / Saved!\n\n"
            "Now send your full name:"
        )
    return (
        f"✅ {variety} नोंदवले! / Saved!\n\n"
        "आता तुमचे पूर्ण नाव पाठवा:\n"
        "Now send your full name:"
    )


def _format_name_saved(language: str = i18n.DEFAULT_LANGUAGE) -> str:
    if _is_hindi(language):
        return (
            f"{i18n.get_message('name_saved', 'Hindi')} / Name saved!\n\n"
            f"{i18n.get_message('confirm_phone', 'Hindi')}\n"
            "Confirm your phone number:\n"
            "(केवल 10 अंक / 10 digits only)"
        )
    return (
        "✅ नाव नोंदवले! / Name saved!\n\n"
        "तुमचा फोन नंबर confirm करा:\n"
        "Confirm your phone number:\n"
        "(फक्त 10 अंक / 10 digits only)"
    )


def _format_phone_saved(language: str = i18n.DEFAULT_LANGUAGE) -> str:
    if _is_hindi(language):
        return (
            f"{i18n.get_message('phone_confirmed', 'Hindi')} / Confirmed!\n\n"
            "Send your village name:"
        )
    return (
        "✅ नंबर confirm झाला! / Confirmed!\n\n"
        "तुमच्या गावाचे नाव पाठवा:\n"
        "Send your village name:"
    )


def _format_village_saved(language: str = i18n.DEFAULT_LANGUAGE) -> str:
    if _is_hindi(language):
        return (
            f"{i18n.get_message('village_saved', 'Hindi')} / Village saved!\n\n"
            f"{i18n.get_message('send_farm_area_acres', 'Hindi')}\n"
            "Send your farm area in acres:\n"
            "(केवल संख्या / numbers only e.g. 3.5)"
        )
    return (
        "✅ गाव नोंदवले! / Village saved!\n\n"
        "तुमच्या शेताचे एकर मध्ये क्षेत्रफळ पाठवा:\n"
        "Send your farm area in acres:\n"
        "(फक्त नंबर / numbers only e.g. 3.5)"
    )


def _format_mismatch_prompt(
    farmer_name: str,
    reported_acres: float,
    amed_acres: float,
    language: str = i18n.DEFAULT_LANGUAGE,
) -> str:
    if _is_hindi(language):
        question = i18n.get_message(
            "mismatch_acres_question",
            "Hindi",
            name=farmer_name,
            reported_acres=reported_acres,
            amed_acres=amed_acres,
        )
        opt1 = i18n.get_message(
            "mismatch_option_my_acres", "Hindi", reported_acres=reported_acres
        )
        opt2 = i18n.get_message(
            "mismatch_option_amed_acres", "Hindi", amed_acres=amed_acres
        )
        opt3 = i18n.get_message("mismatch_option_unsure", "Hindi")
        return (
            f"{question}\n"
            f"(You mentioned {reported_acres} acres but\n"
            f" satellite shows {amed_acres} acres.)\n\n"
            f"कृपया confirm करें / Please confirm:\n"
            f"{opt1}\n"
            f"   (My bagaan is {reported_acres} acres)\n"
            f"{opt2}\n"
            f"   (My bagaan is {amed_acres} acres)\n"
            f"{opt3} / I am not sure"
        )
    return (
        f"{farmer_name} ji,\n"
        f"तुम्ही {reported_acres} एकर सांगितले परंतु\n"
        f"उपग्रहाने {amed_acres} एकर दाखवले आहे.\n"
        f"(You mentioned {reported_acres} acres but\n"
        f" satellite shows {amed_acres} acres.)\n\n"
        f"कृपया confirm करा / Please confirm:\n"
        f"1️⃣ माझे शेत {reported_acres} एकर आहे\n"
        f"   (My farm is {reported_acres} acres)\n"
        f"2️⃣ माझे शेत {amed_acres} एकर आहे\n"
        f"   (My farm is {amed_acres} acres)\n"
        f"3️⃣ मला खात्री नाही / I am not sure"
    )


def _format_confirmation(
    name: str,
    crop: str,
    variety: str,
    village: str,
    taluka: str,
    acres: float,
    phone: str,
    language: str = i18n.DEFAULT_LANGUAGE,
) -> str:
    if _is_hindi(language):
        return (
            f"{i18n.get_message('all_data_saved', 'Hindi')}\n"
            "All details saved!\n\n"
            "आपकी जानकारी / Your details:\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"\U0001F464 नाम: {name}\n"
            f"\U0001F33F फसल: {crop} — {variety}\n"
            f"\U0001F4CD गाँव: {village}, {taluka}\n"
            f"\U0001F33E क्षेत्र: {acres} एकड़\n"
            f"\U0001F4DE फोन: {phone}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{i18n.get_message('you_will_receive_daily_report', 'Hindi')}\n"
            "From tomorrow you will receive your\n"
            "daily bagaan report at 6 AM!"
        )
    return (
        "✅ सर्व माहिती नोंदवली!\n"
        "All details saved!\n\n"
        "तुमची माहिती / Your details:\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"\U0001F464 नाव: {name}\n"
        f"\U0001F33F पीक: {crop} — {variety}\n"
        f"\U0001F4CD गाव: {village}, {taluka}\n"
        f"\U0001F33E क्षेत्र: {acres} एकर\n"
        f"\U0001F4DE फोन: {phone}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "उद्यापासून तुम्हाला रोज सकाळी 6 वाजता\n"
        "शेत अहवाल मिळेल! \U0001F331\n"
        "From tomorrow you will receive your\n"
        "daily farm report at 6 AM!"
    )


def _format_pankaj_alert(
    farmer_name: str,
    village: str,
    reported: float,
    amed: float,
) -> str:
    return (
        "Agent verification needed:\n"
        f"{farmer_name} — {village}\n"
        f"Area mismatch: {reported} vs {amed} acres\n"
        "Booking ref: —"
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_variety(body: str) -> Optional[str]:
    text = _normalise(body)
    if not text:
        return None
    if _is_purely_numeric(text):
        return None
    return text


def _validate_name(body: str) -> Optional[str]:
    text = _normalise(body)
    if len(text) < 3:
        return None
    if _is_purely_numeric(text):
        return None
    return text


def _validate_phone(body: str) -> Optional[str]:
    text = _normalise(body)
    if not re.fullmatch(r"\d{10}", text):
        return None
    return text


def _validate_village(body: str) -> Optional[str]:
    text = _normalise(body)
    if len(text) < 2:
        return None
    return text


def _validate_acres(body: str) -> Optional[float]:
    text = _normalise(body).replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0.1 or value > 100:
        return None
    return value


# ---------------------------------------------------------------------------
# Public API — trigger
# ---------------------------------------------------------------------------
def start_variety_collection(
    farmer_id: str,
    plot_id: str,
    amed_crop: str,
    amed_confidence: Optional[float],
    amed_acres: Optional[float],
) -> dict:
    """Send the kickoff message and create the session row.

    Returned by ``api.webhooks_variety.start_variety_collection`` and
    called from Agent 3's pipeline trigger. Side effects:
        * variety_responses row INSERT (status=IN_PROGRESS)
        * whatsapp_sessions UPSERT (step=AWAITING_VARIETY)
        * one outbound WhatsApp message via the active sender.
    """
    farmer = whatsapp_db.get_farmer_by_id(farmer_id)
    if not farmer:
        raise ValueError(f"Unknown farmer_id: {farmer_id}")
    mobile = farmer.get("mobile_number")
    if not mobile:
        raise ValueError(f"Farmer {farmer_id} has no mobile_number")

    farmer_name = farmer.get("farmer_full_name") or "Shetkari"
    language = _resolve_language(farmer_id)
    body = _format_initial_message(farmer_name, amed_crop, language=language)
    send_result = _send(mobile, body)

    response_id = whatsapp_db.create_variety_response(
        farmer_id=farmer_id,
        plot_id=plot_id,
        amed_crop_detected=amed_crop,
        amed_confidence=amed_confidence,
    )

    session_data = {
        "amed_crop": amed_crop,
        "amed_confidence": amed_confidence,
        "amed_acres": amed_acres,
        "plot_id": str(plot_id),
        "variety_response_id": response_id,
        "farmer_name": farmer_name,
        "language": language,
    }
    session = whatsapp_db.upsert_session(
        mobile,
        farmer_id=farmer_id,
        current_step=Step.VARIETY,
        collection_flow="variety_collection",
        session_data=session_data,
    )

    return {
        "sent": [send_result],
        "session": session,
        "variety_response_id": response_id,
        "mobile": mobile,
    }


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------
def _advance(
    mobile: str,
    farmer_id: str,
    next_step: str,
    session_data: dict,
) -> Optional[dict]:
    return whatsapp_db.upsert_session(
        mobile,
        farmer_id=farmer_id,
        current_step=next_step,
        collection_flow="variety_collection",
        session_data=session_data,
    )


def _session_language(session_data: dict, farmer_id: Optional[str]) -> str:
    """Read the language from session_data; fall back to a live region lookup
    for sessions started before language routing landed."""
    lang = (session_data or {}).get("language")
    if isinstance(lang, str) and lang.strip():
        return lang
    resolved = _resolve_language(farmer_id)
    if isinstance(session_data, dict):
        session_data["language"] = resolved
    return resolved


def _handle_variety(
    mobile: str, body: str, session: dict
) -> dict:
    variety = _validate_variety(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}
    amed_crop = session_data.get("amed_crop", "")
    language = _session_language(session_data, farmer_id)

    if variety is None:
        examples = VARIETY_EXAMPLES.get(amed_crop, "")
        if _is_hindi(language):
            reask = (
                f"{i18n.get_message('send_variety_first', 'Hindi')} / Please send the variety.\n"
                f"उदा. / e.g.: {examples}"
            )
        else:
            reask = (
                "कृपया जात पाठवा / Please send the variety.\n"
                f"उदा. / e.g.: {examples}"
            )
        result = _send(mobile, reask)
        return {
            "sent": [result],
            "next_step": Step.VARIETY,
            "complete": False,
        }

    response_id = session_data.get("variety_response_id")
    plot_id = session_data.get("plot_id")
    if response_id:
        whatsapp_db.update_variety_response(
            response_id, variety_reported=variety
        )
    if plot_id:
        whatsapp_db.update_plot(
            plot_id,
            current_crop_variety=variety,
            variety_source="farmer_reported",
        )

    session_data["variety"] = variety
    _advance(mobile, farmer_id, Step.NAME, session_data)
    result = _send(mobile, _format_variety_saved(variety, language=language))
    return {
        "sent": [result],
        "next_step": Step.NAME,
        "complete": False,
    }


def _handle_name(mobile: str, body: str, session: dict) -> dict:
    name = _validate_name(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}
    language = _session_language(session_data, farmer_id)

    if name is None:
        if _is_hindi(language):
            reask = (
                f"{i18n.get_message('send_full_name', 'Hindi')}\n"
                "Please send your full name (min 3 letters)."
            )
        else:
            reask = (
                "कृपया तुमचे पूर्ण नाव पाठवा (किमान 3 अक्षरे).\n"
                "Please send your full name (min 3 letters)."
            )
        result = _send(mobile, reask)
        return {
            "sent": [result],
            "next_step": Step.NAME,
            "complete": False,
        }

    response_id = session_data.get("variety_response_id")
    if response_id:
        whatsapp_db.update_variety_response(
            response_id, name_confirmed=name
        )

    farmer = whatsapp_db.get_farmer_by_id(farmer_id) or {}
    existing_name = farmer.get("farmer_full_name")
    if existing_name != name:
        whatsapp_db.update_farmer(farmer_id, farmer_full_name=name)
        _log_field_change(farmer_id, "farmer_full_name", existing_name, name)

    session_data["name"] = name
    _advance(mobile, farmer_id, Step.PHONE, session_data)
    result = _send(mobile, _format_name_saved(language=language))
    return {
        "sent": [result],
        "next_step": Step.PHONE,
        "complete": False,
    }


def _handle_phone(mobile: str, body: str, session: dict) -> dict:
    phone = _validate_phone(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}
    language = _session_language(session_data, farmer_id)

    if phone is None:
        if _is_hindi(language):
            reask = (
                f"{i18n.get_message('confirm_phone_only_10_digits', 'Hindi')}\n"
                "Please send a 10-digit phone number."
            )
        else:
            reask = (
                "कृपया 10 अंकी फोन नंबर पाठवा.\n"
                "Please send a 10-digit phone number."
            )
        result = _send(mobile, reask)
        return {
            "sent": [result],
            "next_step": Step.PHONE,
            "complete": False,
        }

    response_id = session_data.get("variety_response_id")
    if response_id:
        whatsapp_db.update_variety_response(
            response_id, phone_confirmed=phone
        )

    farmer = whatsapp_db.get_farmer_by_id(farmer_id) or {}
    registered = farmer.get("mobile_number")
    if phone != registered:
        whatsapp_db.update_farmer(farmer_id, alternate_mobile=phone)
        _log_field_change(
            farmer_id,
            "alternate_mobile",
            farmer.get("alternate_mobile"),
            phone,
        )

    session_data["phone"] = phone
    _advance(mobile, farmer_id, Step.VILLAGE, session_data)
    result = _send(mobile, _format_phone_saved(language=language))
    return {
        "sent": [result],
        "next_step": Step.VILLAGE,
        "complete": False,
    }


def _handle_village(mobile: str, body: str, session: dict) -> dict:
    village = _validate_village(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}
    language = _session_language(session_data, farmer_id)

    if village is None:
        if _is_hindi(language):
            reask = (
                f"{i18n.get_message('send_village_name', 'Hindi')}\n"
                "Please send your village name."
            )
        else:
            reask = (
                "कृपया तुमच्या गावाचे नाव पाठवा.\n"
                "Please send your village name."
            )
        result = _send(mobile, reask)
        return {
            "sent": [result],
            "next_step": Step.VILLAGE,
            "complete": False,
        }

    response_id = session_data.get("variety_response_id")
    if response_id:
        whatsapp_db.update_variety_response(
            response_id, village_confirmed=village
        )

    farmer = whatsapp_db.get_farmer_by_id(farmer_id) or {}
    existing_village = farmer.get("village")
    if existing_village != village:
        whatsapp_db.update_farmer(farmer_id, village=village)
        _log_field_change(farmer_id, "village", existing_village, village)

    session_data["village"] = village
    _advance(mobile, farmer_id, Step.ACRES, session_data)
    result = _send(mobile, _format_village_saved(language=language))
    return {
        "sent": [result],
        "next_step": Step.ACRES,
        "complete": False,
    }


def _handle_acres(mobile: str, body: str, session: dict) -> dict:
    acres = _validate_acres(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}
    plot_id = session_data.get("plot_id")
    language = _session_language(session_data, farmer_id)

    if acres is None:
        if _is_hindi(language):
            reask = (
                f"{i18n.get_message('area_acres_invalid', 'Hindi')}\n"
                "Please send acres as a number (e.g. 3.5)."
            )
        else:
            reask = (
                "कृपया एकर मध्ये नंबर पाठवा (उदा. 3.5).\n"
                "Please send acres as a number (e.g. 3.5)."
            )
        result = _send(mobile, reask)
        return {
            "sent": [result],
            "next_step": Step.ACRES,
            "complete": False,
        }

    response_id = session_data.get("variety_response_id")
    if response_id:
        whatsapp_db.update_variety_response(
            response_id, acres_reported=acres
        )
    if plot_id:
        whatsapp_db.update_plot(plot_id, self_reported_acres=acres)

    # Compute mismatch vs AMED.
    amed_acres: Optional[float] = None
    if plot_id:
        amed_row = whatsapp_db.latest_amed_reading_for_plot(plot_id)
        if amed_row and amed_row.get("field_size_acres_amed") is not None:
            amed_acres = float(amed_row["field_size_acres_amed"])

    if amed_acres is None:
        # Skip mismatch — wrap straight to COMPLETE.
        session_data["acres"] = acres
        return _finalise_collection(
            mobile, farmer_id, session_data, agent_required=False
        )

    diff_pct = abs(acres - amed_acres) / amed_acres * 100.0
    if diff_pct > 20:
        if plot_id:
            whatsapp_db.update_plot(plot_id, area_mismatch_pct=diff_pct)
        if response_id:
            whatsapp_db.update_variety_response(
                response_id, acres_mismatch_pct=diff_pct
            )
        session_data["acres"] = acres
        session_data["reported_acres"] = acres
        session_data["amed_acres"] = amed_acres
        _advance(mobile, farmer_id, Step.MISMATCH, session_data)

        farmer = whatsapp_db.get_farmer_by_id(farmer_id) or {}
        farmer_name = (
            session_data.get("name")
            or farmer.get("farmer_full_name")
            or "Shetkari"
        )
        result = _send(
            mobile,
            _format_mismatch_prompt(
                farmer_name, acres, amed_acres, language=language
            ),
        )
        return {
            "sent": [result],
            "next_step": Step.MISMATCH,
            "complete": False,
        }

    # Within tolerance.
    session_data["acres"] = acres
    return _finalise_collection(
        mobile, farmer_id, session_data, agent_required=False
    )


_MISMATCH_OPTION_PATTERNS = {
    "1": "1",
    "2": "2",
    "3": "3",
    "१": "1",
    "२": "2",
    "३": "3",
    "one": "1",
    "two": "2",
    "three": "3",
}


def _parse_mismatch_choice(body: str) -> Optional[str]:
    text = _normalise(body).lower()
    if not text:
        return None
    # First try direct mapping.
    if text in _MISMATCH_OPTION_PATTERNS:
        return _MISMATCH_OPTION_PATTERNS[text]
    # Check leading char.
    first = text[0]
    if first in _MISMATCH_OPTION_PATTERNS:
        return _MISMATCH_OPTION_PATTERNS[first]
    return None


def _handle_mismatch(mobile: str, body: str, session: dict) -> dict:
    choice = _parse_mismatch_choice(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}
    language = _session_language(session_data, farmer_id)

    if choice is None:
        if _is_hindi(language):
            reask = (
                "कृपया 1, 2 या 3 भेजें।\n"
                "Please reply with 1, 2 or 3."
            )
        else:
            reask = (
                "कृपया 1, 2 किंवा 3 पाठवा.\n"
                "Please reply with 1, 2 or 3."
            )
        result = _send(mobile, reask)
        return {
            "sent": [result],
            "next_step": Step.MISMATCH,
            "complete": False,
        }

    plot_id = session_data.get("plot_id")
    response_id = session_data.get("variety_response_id")
    reported_acres = session_data.get("reported_acres")
    amed_acres = session_data.get("amed_acres")

    if choice == "1":
        # Farmer sticks with own number; flag for agent.
        if response_id:
            whatsapp_db.update_variety_response(
                response_id,
                mismatch_resolution="farmer_confirmed_own",
            )
        return _finalise_collection(
            mobile, farmer_id, session_data, agent_required=True
        )

    if choice == "2":
        # Farmer accepts AMED.
        if plot_id and amed_acres is not None:
            whatsapp_db.update_plot(
                plot_id,
                self_reported_acres=amed_acres,
                area_mismatch_pct=0,
            )
        if response_id:
            whatsapp_db.update_variety_response(
                response_id,
                mismatch_resolution="farmer_accepted_amed",
                acres_reported=amed_acres,
                acres_mismatch_pct=0,
            )
        if amed_acres is not None:
            session_data["acres"] = amed_acres
        return _finalise_collection(
            mobile, farmer_id, session_data, agent_required=False
        )

    # choice == "3"
    if response_id:
        whatsapp_db.update_variety_response(
            response_id, mismatch_resolution="farmer_unsure"
        )
    return _finalise_collection(
        mobile, farmer_id, session_data, agent_required=True
    )


def _finalise_collection(
    mobile: str,
    farmer_id: str,
    session_data: dict,
    agent_required: bool,
) -> dict:
    plot_id = session_data.get("plot_id")
    response_id = session_data.get("variety_response_id")
    farmer = whatsapp_db.get_farmer_by_id(farmer_id) or {}

    # Resolve final values for the confirmation card.
    name = (
        session_data.get("name")
        or farmer.get("farmer_full_name")
        or ""
    )
    village = (
        session_data.get("village")
        or farmer.get("village")
        or ""
    )
    phone = session_data.get("phone") or farmer.get("mobile_number") or ""
    acres = session_data.get("acres")
    variety = session_data.get("variety") or ""
    crop = session_data.get("amed_crop", "")
    taluka = farmer.get("taluka") or "Tasgaon"

    # Persist final state across tables.
    status = "AGENT_REQUIRED" if agent_required else "COMPLETE"
    whatsapp_db.update_farmer(
        farmer_id,
        amed_variety_collected=1,
        amed_variety_collected_at=_now_iso(),
        variety_collection_status=status,
    )
    if plot_id:
        whatsapp_db.update_plot(
            plot_id,
            amed_crop_verified=1,
            amed_verification_date=date.today().isoformat(),
        )
    if response_id:
        whatsapp_db.update_variety_response(
            response_id,
            status=status,
            collection_completed_at=_now_iso(),
        )

    # Mark session as COMPLETE so the farmer is not re-prompted.
    whatsapp_db.upsert_session(
        mobile,
        farmer_id=farmer_id,
        current_step=Step.COMPLETE,
        collection_flow="variety_collection",
        session_data=session_data,
    )

    sent: list[dict] = []
    language = _session_language(session_data, farmer_id)
    body = _format_confirmation(
        name=name,
        crop=crop,
        variety=variety,
        village=village,
        taluka=taluka,
        acres=acres if acres is not None else "—",
        phone=phone,
        language=language,
    )
    sent.append(_send(mobile, body))

    if agent_required:
        pankaj_mobile = os.getenv("PANKAJ_ALERT_MOBILE", PANKAJ_DEFAULT_MOBILE)
        reported = session_data.get("reported_acres", acres)
        amed_acres = session_data.get("amed_acres", "—")
        alert = _format_pankaj_alert(
            farmer_name=name,
            village=village,
            reported=reported,
            amed=amed_acres,
        )
        sent.append(_send(pankaj_mobile, alert))

    return {
        "sent": sent,
        "next_step": Step.COMPLETE,
        "complete": True,
        "agent_required": agent_required,
    }


# ---------------------------------------------------------------------------
# Public API — routing
# ---------------------------------------------------------------------------
_HANDLERS = {
    Step.VARIETY: _handle_variety,
    Step.NAME: _handle_name,
    Step.PHONE: _handle_phone,
    Step.VILLAGE: _handle_village,
    Step.ACRES: _handle_acres,
    Step.MISMATCH: _handle_mismatch,
}


def handle_incoming_message(mobile: str, body: str) -> dict:
    """Route a farmer reply through the active conversation state."""
    session = whatsapp_db.get_active_session(mobile)
    if not session:
        return {
            "sent": [],
            "next_step": None,
            "complete": False,
            "status": "no_active_session",
        }

    step = session.get("current_step")
    handler = _HANDLERS.get(step)
    if handler is None:
        # Either COLLECTION_COMPLETE or an unrecognised step. Nothing to do.
        return {
            "sent": [],
            "next_step": step,
            "complete": step == Step.COMPLETE,
            "status": "no_handler_for_step",
        }
    return handler(mobile, body, session)


# ---------------------------------------------------------------------------
# FastAPI endpoint — direct testing hook
# ---------------------------------------------------------------------------
class IncomingMessage(BaseModel):
    mobile: str
    body: str


@router.post("/incoming")
def incoming_endpoint(payload: IncomingMessage) -> dict:
    if not payload.mobile or not payload.body:
        raise HTTPException(
            status_code=400, detail="mobile and body are required"
        )
    return handle_incoming_message(payload.mobile, payload.body)
