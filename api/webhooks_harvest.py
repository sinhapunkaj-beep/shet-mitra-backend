"""WhatsApp harvest-outcome state machine.

Mirrors the variety-collection webhook but targets the END of the season:
once a farmer's AMED-predicted harvest date has passed (plus a small
grace window) we ask them four questions over WhatsApp and persist the
answers to ``farm_harvest_actuals`` for Year-2 ML retraining.

States (5):

    HARVEST_TRIGGER
    AWAITING_YIELD     -> AWAITING_PRICE
    AWAITING_PRICE     -> AWAITING_GRADE
    AWAITING_GRADE     -> AWAITING_SOLD_DATE
    AWAITING_SOLD_DATE -> HARVEST_COMPLETE

Public entry points:
    * ``start_harvest_collection(...)`` — called by the harvest trigger /
      cron once the season has matured. Sends the initial template + the
      first follow-up question.
    * ``handle_incoming_message(mobile, body)`` — dispatched by the
      AISensy webhook for every farmer reply on this flow.

All outbound messages go through ``whatsapp_sender.get_sender()`` so
tests can swap in a ``MockSender`` and assert on the outbox.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import whatsapp_db
from api.whatsapp_sender import get_sender

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/harvest", tags=["harvest"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
class Step:
    TRIGGER = "HARVEST_TRIGGER"
    YIELD = "AWAITING_YIELD"
    PRICE = "AWAITING_PRICE"
    GRADE = "AWAITING_GRADE"
    SOLD_DATE = "AWAITING_SOLD_DATE"
    COMPLETE = "HARVEST_COMPLETE"


COLLECTION_FLOW = "harvest_actuals"

# Validation bands. Yield_per_acre below this is implausible (kg/acre); a
# raw value above the per-acre ceiling is treated as a total-yield report.
YIELD_PER_ACRE_MIN_KG = 50.0
YIELD_PER_ACRE_MAX_KG = 50000.0
PRICE_MIN_INR = 1.0
PRICE_MAX_INR = 10000.0

# Marathi month names map to month index 1..12. Both Devanagari script and
# the English form ("january"..."december") are accepted via _parse_date.
_MARATHI_MONTHS = {
    "जानेवारी": 1, "फेब्रुवारी": 2, "मार्च": 3, "एप्रिल": 4,
    "मे": 5, "जून": 6, "जुलै": 7, "ऑगस्ट": 8,
    "सप्टेंबर": 9, "ऑक्टोबर": 10, "नोव्हेंबर": 11, "डिसेंबर": 12,
}
_ENGLISH_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _send(to: str, body: str) -> dict:
    return get_sender().send(to, body)


def _normalise(text: str) -> str:
    return (text or "").strip()


def _today_iso() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# farm_harvest_actuals: bespoke SQL helpers. We keep these here (rather than
# in whatsapp_db) because the harvest table is private to this flow.
# ---------------------------------------------------------------------------
_HARVEST_UPDATABLE = {
    "variety",
    "total_yield_kg",
    "yield_per_acre_kg",
    "selling_price_inr_per_kg",
    "grade",
    "sold_date",
    "buyer_type",
    "amed_predicted_yield_kg",
    "amed_predicted_grade",
    "yield_accuracy_pct",
    "raw_response",
    "collection_completed_at",
    "status",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(whatsapp_db.get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def _create_harvest_actual(
    *,
    farmer_id: str,
    plot_id: Optional[str],
    season_label: str,
    crop_type: str,
    variety: Optional[str],
    amed_predicted_yield_kg: Optional[float],
    amed_predicted_grade: Optional[str],
) -> Optional[str]:
    """Insert (or fetch existing) IN_PROGRESS row, returning its id."""
    actual_id = str(uuid.uuid4())
    with _connect() as conn:
        if not _table_exists(conn, "farm_harvest_actuals"):
            LOG.warning("farm_harvest_actuals missing; cannot create row")
            return None
        # Re-use any pre-existing IN_PROGRESS row for the same season so the
        # uniqueness constraint never fires.
        cur = conn.execute(
            """
            SELECT id FROM farm_harvest_actuals
             WHERE farmer_id = ? AND COALESCE(plot_id,'') = COALESCE(?,'')
               AND season_label = ? AND crop_type = ?
             LIMIT 1
            """,
            (farmer_id, plot_id, season_label, crop_type),
        )
        existing = cur.fetchone()
        if existing:
            return existing["id"]
        conn.execute(
            """
            INSERT INTO farm_harvest_actuals (
                id, farmer_id, plot_id, season_label, crop_type, variety,
                amed_predicted_yield_kg, amed_predicted_grade,
                reported_via, status,
                collection_started_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'whatsapp', 'IN_PROGRESS', ?, ?)
            """,
            (
                actual_id,
                farmer_id,
                plot_id,
                season_label,
                crop_type,
                variety,
                amed_predicted_yield_kg,
                amed_predicted_grade,
                _now_iso(),
                _now_iso(),
            ),
        )
        conn.commit()
        return actual_id


def _update_harvest_actual(actual_id: str, **fields: Any) -> bool:
    if not fields:
        return False
    bad = [k for k in fields if k not in _HARVEST_UPDATABLE]
    if bad:
        raise ValueError(
            f"Disallowed update fields for farm_harvest_actuals: {bad}. "
            f"Allowed: {sorted(_HARVEST_UPDATABLE)}"
        )
    with _connect() as conn:
        if not _table_exists(conn, "farm_harvest_actuals"):
            return False
        cur = conn.execute("PRAGMA table_info(farm_harvest_actuals)")
        local = {row["name"] for row in cur.fetchall()}
        applicable = {k: v for k, v in fields.items() if k in local}
        if not applicable:
            return False
        set_clause = ", ".join(f"{col} = ?" for col in applicable)
        values = list(applicable.values()) + [actual_id]
        conn.execute(
            f"UPDATE farm_harvest_actuals SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()
        return True


def _get_harvest_actual(actual_id: str) -> Optional[dict]:
    with _connect() as conn:
        if not _table_exists(conn, "farm_harvest_actuals"):
            return None
        cur = conn.execute(
            "SELECT * FROM farm_harvest_actuals WHERE id = ? LIMIT 1",
            (actual_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}


def _update_farmer_raw(farmer_id: str, **fields: Any) -> bool:
    """Direct UPDATE for harvest-specific farmer columns (not on the
    whatsapp_db allowlist). Idempotent and silently degrades when columns
    are missing on the local mirror."""
    if not fields:
        return False
    with _connect() as conn:
        if not _table_exists(conn, "farmers"):
            return False
        cur = conn.execute("PRAGMA table_info(farmers)")
        local = {row["name"] for row in cur.fetchall()}
        applicable = {k: v for k, v in fields.items() if k in local}
        if not applicable:
            return False
        set_clause = ", ".join(f"{col} = ?" for col in applicable)
        values = list(applicable.values()) + [farmer_id]
        conn.execute(
            f"UPDATE farmers SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()
        return True


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _to_western_digits(text: str) -> str:
    return text.translate(_DEVANAGARI_DIGITS)


def _validate_yield(body: str) -> Optional[tuple[str, float]]:
    """Return (field_name, value_kg) for the yield reply.

    Accepts both per-acre and total kg in a single field. Heuristic:
    a number larger than ``YIELD_PER_ACRE_MAX_KG`` is stored as
    ``total_yield_kg``, anything in the per-acre band is stored as
    ``yield_per_acre_kg``. Sub-floor values re-ask.
    """
    text = _to_western_digits(_normalise(body))
    if not text:
        return None
    # Strip common suffixes ("kg", "kg/acre", etc.) before parsing.
    digits = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not digits:
        return None
    try:
        value = float(digits.group(0).replace(",", "."))
    except ValueError:
        return None
    if value <= 0:
        return None
    if value > YIELD_PER_ACRE_MAX_KG:
        return ("total_yield_kg", value)
    if value < YIELD_PER_ACRE_MIN_KG:
        return None
    return ("yield_per_acre_kg", value)


def _validate_price(body: str) -> Optional[float]:
    text = _to_western_digits(_normalise(body))
    if not text:
        return None
    digits = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not digits:
        return None
    try:
        value = float(digits.group(0).replace(",", "."))
    except ValueError:
        return None
    if not (PRICE_MIN_INR <= value <= PRICE_MAX_INR):
        return None
    return value


_GRADE_TOKENS = {
    "a": "A", "b": "B", "c": "C",
    "1": "A", "2": "B", "3": "C",
    "ए": "A", "बी": "B", "सी": "C",
    "१": "A", "२": "B", "३": "C",
    "good": "A", "best": "A", "premium": "A", "high": "A",
    "medium": "B", "mid": "B", "average": "B",
    "poor": "C", "low": "C",
    "mixed": "MIXED", "मिश्र": "MIXED",
}


def _validate_grade(body: str) -> Optional[str]:
    text = _normalise(body).lower()
    if not text:
        return None
    if text in _GRADE_TOKENS:
        return _GRADE_TOKENS[text]
    first_token = text.split()[0] if text.split() else ""
    if first_token in _GRADE_TOKENS:
        return _GRADE_TOKENS[first_token]
    # Devanagari single-char attempt.
    if text[0] in _GRADE_TOKENS:
        return _GRADE_TOKENS[text[0]]
    return None


def _parse_date(body: str) -> Optional[date]:
    """Parse a date in a permissive set of formats.

    Supported:
        * ISO YYYY-MM-DD
        * DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY
        * 'today', 'yesterday' (case-insensitive)
        * '<day> <month-name>' (English or Marathi); year defaults to today
        * '<day> <month-name> <year>'
    """
    raw = _normalise(body)
    if not raw:
        return None
    text = _to_western_digits(raw).lower()
    today = date.today()
    if text in ("today", "आज"):
        return today
    if text in ("yesterday", "काल"):
        return today - timedelta(days=1)

    # ISO YYYY-MM-DD.
    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return None

    # DD[-/.]MM[-/.]YYYY  or  DD[-/.]MM[-/.]YY.
    dmy_match = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", text)
    if dmy_match:
        try:
            day, month, year = (int(x) for x in dmy_match.groups())
            if year < 100:
                year += 2000
            return date(year, month, day)
        except ValueError:
            return None

    # "<day> <month-name> [<year>]" — accept Marathi or English month words.
    # Use the ORIGINAL raw text so Devanagari month words are preserved.
    tokens = raw.split()
    if 2 <= len(tokens) <= 3:
        # Day token must be numeric.
        try:
            day = int(_to_western_digits(tokens[0]))
        except ValueError:
            day = None
        month_token = tokens[1]
        month_num: Optional[int] = None
        if month_token in _MARATHI_MONTHS:
            month_num = _MARATHI_MONTHS[month_token]
        else:
            month_num = _ENGLISH_MONTHS.get(month_token.lower())
        if day is not None and month_num is not None:
            year = today.year
            if len(tokens) == 3:
                try:
                    year = int(_to_western_digits(tokens[2]))
                    if year < 100:
                        year += 2000
                except ValueError:
                    year = today.year
            try:
                return date(year, month_num, day)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Message templates (user-facing; emoji-bearing strings are intentional).
# ---------------------------------------------------------------------------
def _format_initial_message(farmer_name: str) -> str:
    return (
        f"{farmer_name} ji,\n"
        "या हंगामात तुम्हाला किती उत्पन्न मिळाले?\n"
        "What was your harvest this season?\n\n"
        "1. एकूण उत्पादन (Total yield kg/acre): ?\n"
        "2. विक्री किंमत (Selling price ₹/kg): ?\n"
        "3. श्रेणी (Grade): A / B / C\n"
        "4. विकल्याची तारीख (Date sold): ?\n\n"
        "हे डेटा पुढील वर्षी अधिक अचूक सल्ला देण्यासाठी\n"
        "वापरला जाईल.\n"
        "This data will improve next year's advice."
    )


def _format_q_yield() -> str:
    return (
        "कृपया एकूण उत्पादन kg/acre मध्ये पाठवा:\n"
        "Please send total yield in kg/acre:"
    )


def _format_q_price() -> str:
    return (
        "✅ नोंदवले! / Saved!\n\n"
        "आता विक्री किंमत ₹/kg मध्ये पाठवा:\n"
        "Now send selling price in ₹/kg:"
    )


def _format_q_grade() -> str:
    return (
        "✅ नोंदवले! / Saved!\n\n"
        "श्रेणी पाठवा / Send grade: A / B / C"
    )


def _format_q_sold_date() -> str:
    return (
        "✅ नोंदवले! / Saved!\n\n"
        "विकल्याची तारीख पाठवा (DD-MM-YYYY किंवा today / yesterday):\n"
        "Send date sold (DD-MM-YYYY or today / yesterday):"
    )


def _format_invalid_yield() -> str:
    return (
        "कृपया उत्पादन kg मध्ये पाठवा (उदा. 1200).\n"
        "Please send yield in kg (e.g. 1200)."
    )


def _format_invalid_price() -> str:
    return (
        "कृपया विक्री किंमत ₹/kg पाठवा (उदा. 85.5).\n"
        "Please send selling price in ₹/kg (e.g. 85.5)."
    )


def _format_invalid_grade() -> str:
    return (
        "कृपया श्रेणी पाठवा: A, B किंवा C.\n"
        "Please send grade: A, B or C."
    )


def _format_invalid_date() -> str:
    return (
        "कृपया तारीख DD-MM-YYYY स्वरूपात पाठवा.\n"
        "Please send the date in DD-MM-YYYY format\n"
        "(or 'today' / 'yesterday')."
    )


def _format_complete(
    crop: str,
    variety: Optional[str],
    yield_total_kg: Optional[float],
    yield_per_acre_kg: Optional[float],
    price_inr_per_kg: float,
    grade: str,
    sold_on: date,
) -> str:
    variety_label = variety or "—"
    if yield_total_kg is not None:
        yield_line = f"\U0001F33E उत्पादन: {yield_total_kg:.0f} kg एकूण"
    elif yield_per_acre_kg is not None:
        yield_line = f"\U0001F33E उत्पादन: {yield_per_acre_kg:.0f} kg/एकर"
    else:
        yield_line = "\U0001F33E उत्पादन: —"
    return (
        "✅ हंगामाची माहिती नोंदवली!\n"
        "Season data saved!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"\U0001F33F पीक: {crop} — {variety_label}\n"
        f"{yield_line}\n"
        f"\U0001F4B0 किंमत: ₹{price_inr_per_kg:g}/kg\n"
        f"\U0001F4CB श्रेणी: {grade}\n"
        f"\U0001F4C5 विकले: {sold_on.isoformat()}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "धन्यवाद! / Thank you —\n"
        "your data improves next season's advice."
    )


# ---------------------------------------------------------------------------
# Public API — start
# ---------------------------------------------------------------------------
def start_harvest_collection(
    farmer_id: str,
    plot_id: Optional[str],
    crop: str,
    variety: Optional[str],
    season_label: str,
    amed_predicted_yield_kg: Optional[float] = None,
    amed_predicted_grade: Optional[str] = None,
) -> dict:
    """Open a harvest-actuals conversation with this farmer.

    Side effects:
        * farm_harvest_actuals row INSERT with status='IN_PROGRESS'
        * whatsapp_sessions UPSERT (flow='harvest_actuals',
          step='AWAITING_YIELD')
        * two outbound WhatsApp messages: the kickoff template + Q1.
    """
    farmer = whatsapp_db.get_farmer_by_id(farmer_id)
    if not farmer:
        raise ValueError(f"Unknown farmer_id: {farmer_id}")
    mobile = farmer.get("mobile_number")
    if not mobile:
        raise ValueError(f"Farmer {farmer_id} has no mobile_number")

    farmer_name = farmer.get("farmer_full_name") or "Shetkari"

    actual_id = _create_harvest_actual(
        farmer_id=farmer_id,
        plot_id=plot_id,
        season_label=season_label,
        crop_type=crop,
        variety=variety,
        amed_predicted_yield_kg=amed_predicted_yield_kg,
        amed_predicted_grade=amed_predicted_grade,
    )

    sent: list[dict] = []
    sent.append(_send(mobile, _format_initial_message(farmer_name)))
    sent.append(_send(mobile, _format_q_yield()))

    session_data = {
        "actual_id": actual_id,
        "plot_id": plot_id,
        "crop_type": crop,
        "variety": variety,
        "season_label": season_label,
        "amed_predicted_yield_kg": amed_predicted_yield_kg,
        "amed_predicted_grade": amed_predicted_grade,
        "farmer_name": farmer_name,
    }
    session = whatsapp_db.upsert_session(
        mobile,
        farmer_id=farmer_id,
        current_step=Step.YIELD,
        collection_flow=COLLECTION_FLOW,
        session_data=session_data,
    )

    return {
        "sent": sent,
        "session": session,
        "actual_id": actual_id,
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
        collection_flow=COLLECTION_FLOW,
        session_data=session_data,
    )


def _handle_yield(mobile: str, body: str, session: dict) -> dict:
    parsed = _validate_yield(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}

    if parsed is None:
        result = _send(mobile, _format_invalid_yield())
        return {
            "sent": [result],
            "next_step": Step.YIELD,
            "complete": False,
        }

    field_name, value_kg = parsed
    actual_id = session_data.get("actual_id")

    # If we know plot acres, mirror the value across both columns so the
    # ML pipeline can use whichever is convenient.
    other_value: Optional[float] = None
    plot_id = session_data.get("plot_id")
    area_acres: Optional[float] = None
    if plot_id:
        plot = whatsapp_db.get_plot_by_id(plot_id) or {}
        raw = plot.get("self_reported_acres") or plot.get("area_acres")
        try:
            area_acres = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            area_acres = None
    if area_acres and area_acres > 0:
        if field_name == "yield_per_acre_kg":
            other_value = value_kg * area_acres
        else:
            other_value = value_kg / area_acres

    update_fields: dict[str, Any] = {field_name: value_kg}
    if other_value is not None:
        if field_name == "yield_per_acre_kg":
            update_fields["total_yield_kg"] = other_value
        else:
            update_fields["yield_per_acre_kg"] = other_value
    if actual_id:
        _update_harvest_actual(actual_id, **update_fields)

    session_data["yield_field"] = field_name
    session_data["yield_value_kg"] = value_kg
    if other_value is not None:
        session_data["yield_other_kg"] = other_value
    _advance(mobile, farmer_id, Step.PRICE, session_data)
    result = _send(mobile, _format_q_price())
    return {
        "sent": [result],
        "next_step": Step.PRICE,
        "complete": False,
    }


def _handle_price(mobile: str, body: str, session: dict) -> dict:
    value = _validate_price(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}

    if value is None:
        result = _send(mobile, _format_invalid_price())
        return {
            "sent": [result],
            "next_step": Step.PRICE,
            "complete": False,
        }

    actual_id = session_data.get("actual_id")
    if actual_id:
        _update_harvest_actual(actual_id, selling_price_inr_per_kg=value)

    session_data["selling_price_inr_per_kg"] = value
    _advance(mobile, farmer_id, Step.GRADE, session_data)
    result = _send(mobile, _format_q_grade())
    return {
        "sent": [result],
        "next_step": Step.GRADE,
        "complete": False,
    }


def _handle_grade(mobile: str, body: str, session: dict) -> dict:
    grade = _validate_grade(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}

    if grade is None:
        result = _send(mobile, _format_invalid_grade())
        return {
            "sent": [result],
            "next_step": Step.GRADE,
            "complete": False,
        }

    actual_id = session_data.get("actual_id")
    if actual_id:
        _update_harvest_actual(actual_id, grade=grade)

    session_data["grade"] = grade
    _advance(mobile, farmer_id, Step.SOLD_DATE, session_data)
    result = _send(mobile, _format_q_sold_date())
    return {
        "sent": [result],
        "next_step": Step.SOLD_DATE,
        "complete": False,
    }


def _handle_sold_date(mobile: str, body: str, session: dict) -> dict:
    sold = _parse_date(body)
    farmer_id = session["farmer_id"]
    session_data = session.get("session_data") or {}

    if sold is None:
        result = _send(mobile, _format_invalid_date())
        return {
            "sent": [result],
            "next_step": Step.SOLD_DATE,
            "complete": False,
        }

    session_data["sold_date"] = sold.isoformat()
    return _finalise_collection(mobile, farmer_id, session_data, sold)


def _finalise_collection(
    mobile: str,
    farmer_id: str,
    session_data: dict,
    sold_on: date,
) -> dict:
    actual_id = session_data.get("actual_id")
    crop = session_data.get("crop_type") or ""
    variety = session_data.get("variety")
    price = session_data.get("selling_price_inr_per_kg")
    grade = session_data.get("grade") or "UNKNOWN"

    yield_field = session_data.get("yield_field")
    yield_value = session_data.get("yield_value_kg")
    other_value = session_data.get("yield_other_kg")
    yield_total = yield_per_acre = None
    if yield_field == "total_yield_kg":
        yield_total = yield_value
        yield_per_acre = other_value
    elif yield_field == "yield_per_acre_kg":
        yield_per_acre = yield_value
        yield_total = other_value

    # Compute yield_accuracy_pct against the AMED prediction when we have one.
    # We compare AMED's prediction against the value the farmer actually
    # entered (not the derived total/per-acre pair) so the comparison is
    # apples-to-apples — AMED produces a per-acre or total figure, and the
    # caller is expected to stash a prediction in the same unit the farmer
    # will reply with.
    amed_pred = session_data.get("amed_predicted_yield_kg")
    actual_for_compare: Optional[float] = yield_value
    accuracy_pct: Optional[float] = None
    if amed_pred and actual_for_compare is not None:
        try:
            amed_pred_f = float(amed_pred)
            if amed_pred_f > 0:
                accuracy_pct = (1.0 - abs(actual_for_compare - amed_pred_f) / amed_pred_f) * 100.0
        except (TypeError, ValueError):
            accuracy_pct = None

    if actual_id:
        update_fields: dict[str, Any] = {
            "sold_date": sold_on.isoformat(),
            "status": "COMPLETE",
            "collection_completed_at": _now_iso(),
        }
        if grade:
            update_fields["grade"] = grade
        if accuracy_pct is not None:
            update_fields["yield_accuracy_pct"] = accuracy_pct
        _update_harvest_actual(actual_id, **update_fields)

    # Flag the farmer as done so cron skips them next time.
    _update_farmer_raw(
        farmer_id,
        harvest_actuals_collected=1,
        harvest_actuals_collected_at=_now_iso(),
        harvest_collection_status="COMPLETE",
    )

    whatsapp_db.upsert_session(
        mobile,
        farmer_id=farmer_id,
        current_step=Step.COMPLETE,
        collection_flow=COLLECTION_FLOW,
        session_data=session_data,
    )

    sent = [
        _send(
            mobile,
            _format_complete(
                crop=crop,
                variety=variety,
                yield_total_kg=yield_total,
                yield_per_acre_kg=yield_per_acre,
                price_inr_per_kg=float(price) if price is not None else 0.0,
                grade=grade,
                sold_on=sold_on,
            ),
        )
    ]

    # Trigger 3 of continuous training: when today's harvest count crosses
    # HARVEST_TRIGGER_MIN_COUNT, kick off a retrain. The trigger itself is
    # idempotent (dedupes via cron_run_log) so calling it on every
    # completion is safe.
    try:
        from pipelines.model_retraining import retrain_on_harvest_actuals
        retrain_on_harvest_actuals()
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "retrain_on_harvest_actuals failed: %s", exc,
        )

    return {
        "sent": sent,
        "next_step": Step.COMPLETE,
        "complete": True,
        "yield_accuracy_pct": accuracy_pct,
        "actual_id": actual_id,
    }


# ---------------------------------------------------------------------------
# Public API — routing
# ---------------------------------------------------------------------------
_HANDLERS = {
    Step.YIELD: _handle_yield,
    Step.PRICE: _handle_price,
    Step.GRADE: _handle_grade,
    Step.SOLD_DATE: _handle_sold_date,
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


__all__ = [
    "router",
    "Step",
    "start_harvest_collection",
    "handle_incoming_message",
]
