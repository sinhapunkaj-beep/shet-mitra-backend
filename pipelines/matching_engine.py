"""pipelines/matching_engine.py

ShetMitra / Bagaan Sathi -- Trader-Farmer Connect Matching Engine.

Implements SDD Section 4.2: nightly job that scores every active
``trader_requirements`` row against the pool of ``farmer_lots`` and creates
``lot_matches`` records (plus farmer WhatsApp offers) for any pair that
scores >= 0.70.

The module is written in the same provider-injection style as
``pipelines/signal_engine.py``: all DB / WhatsApp side effects are routed
through helper functions that the tests can monkeypatch, so the core
scoring logic is fully unit-testable without a live Supabase or AiSensy
connection.

Public API
----------
    calculate_match_score(lot, req)             -> float in [0.0, 1.0]
    date_range_overlap(a_from, a_to, b_from, b_to) -> float in [0.0, 1.0]
    run_daily_matching()                         -> dict (summary)

Helpers (overridable for tests)
-------------------------------
    get_active_requirements()
    get_lots_matching(...)
    create_match(lot, req, score, reasons)
    notify_farmer_of_offer(farmer_id, lot, requirement, match_id)
    log_match_created(match)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Threshold and weights (SDD 4.2)
# --------------------------------------------------------------------------- #
MATCH_THRESHOLD = 0.70

MATCH_WEIGHTS: dict[str, float] = {
    "commodity": 0.30,
    "variety":   0.20,
    "grade":     0.15,
    "quantity":  0.15,
    "timing":    0.10,
    "location":  0.10,
}


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #
def _coerce_date(value: Any) -> Optional[date]:
    """Best-effort conversion of strings / datetimes / dates to ``date``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Take the date portion of an ISO-8601 string (works for both
        # ``2026-06-01`` and ``2026-06-01T14:30:00+05:30``).
        head = s.split("T")[0].split(" ")[0]
        try:
            return datetime.strptime(head, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def date_range_overlap(
    a_from: Any, a_to: Any, b_from: Any, b_to: Any
) -> float:
    """Return the fraction of range A that overlaps with range B.

    The result is in ``[0.0, 1.0]``:
      * ``1.0`` when range A is fully contained inside range B
      * ``0.0`` when the ranges are disjoint, missing, or inverted
      * Otherwise the proportion of A covered by the intersection

    We deliberately normalise by ``len(A)`` (the lot's harvest window) rather
    than ``min(len(A), len(B))`` so that "long requirement window, short
    harvest window" still scores 1.0 -- the trader can absolutely take the
    lot. The reverse case ("short requirement, long harvest") yields a
    fractional score, which is what the SDD describes.
    """
    a_f = _coerce_date(a_from)
    a_t = _coerce_date(a_to)
    b_f = _coerce_date(b_from)
    b_t = _coerce_date(b_to)

    if not (a_f and a_t and b_f and b_t):
        return 0.0
    if a_t < a_f or b_t < b_f:
        # Inverted or zero-length ranges: caller error, no credit.
        return 0.0

    latest_start = max(a_f, b_f)
    earliest_end = min(a_t, b_t)
    if earliest_end < latest_start:
        return 0.0

    overlap_days = (earliest_end - latest_start).days + 1
    a_days = (a_t - a_f).days + 1
    if a_days <= 0:
        return 0.0
    return max(0.0, min(1.0, overlap_days / a_days))


# --------------------------------------------------------------------------- #
# Attribute access shim
# --------------------------------------------------------------------------- #
def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off either an attribute-style object or a dict row."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# --------------------------------------------------------------------------- #
# Score calculation (SDD 4.2 verbatim, with safe attribute access)
# --------------------------------------------------------------------------- #
def calculate_match_score(lot: Any, req: Any) -> float:
    """Score a (farmer lot, trader requirement) pair in ``[0.0, 1.0]``.

    Implements SDD 4.2 with the exact 6 weighted factors:
      commodity (0.30 -- mandatory),
      variety   (0.20),
      grade     (0.15),
      quantity  (0.15),
      timing    (0.10),
      location  (0.10).

    Returns ``0.0`` immediately on commodity mismatch. Result rounded to 3 dp.
    """
    score = 0.0
    w = MATCH_WEIGHTS

    lot_commodity = _get(lot, "commodity")
    req_commodity = _get(req, "commodity")

    # ----- Commodity (mandatory) ---------------------------------------- #
    if not lot_commodity or not req_commodity:
        return 0.0
    if str(lot_commodity).strip().lower() != str(req_commodity).strip().lower():
        return 0.0
    score += w["commodity"]

    # ----- Variety ------------------------------------------------------- #
    lot_variety = _get(lot, "variety")
    req_variety = _get(req, "variety")
    if req_variety is None or req_variety == "":
        score += w["variety"]
    elif lot_variety and str(lot_variety).strip().lower() == str(req_variety).strip().lower():
        score += w["variety"]
    elif lot_variety and req_variety and (
        str(lot_variety).strip().lower() in str(req_variety).strip().lower()
        or str(req_variety).strip().lower() in str(lot_variety).strip().lower()
    ):
        score += w["variety"] * 0.5

    # ----- Grade --------------------------------------------------------- #
    lot_grade = _get(lot, "grade_predicted")
    req_grade = _get(req, "grade") or []
    # ``req_grade`` may be a list/tuple (Postgres ARRAY) or a single string.
    if isinstance(req_grade, str):
        req_grade_list = [g.strip() for g in req_grade.split(",") if g.strip()]
    else:
        req_grade_list = [str(g).strip() for g in (req_grade or [])]
    if lot_grade and lot_grade in req_grade_list:
        score += w["grade"]

    # ----- Quantity ------------------------------------------------------ #
    try:
        lot_qty = float(_get(lot, "quantity_kg_estimated") or 0.0)
    except (TypeError, ValueError):
        lot_qty = 0.0
    try:
        req_min = float(_get(req, "quantity_kg_min") or 0.0)
    except (TypeError, ValueError):
        req_min = 0.0
    if req_min > 0:
        if lot_qty >= req_min:
            score += w["quantity"]
        elif lot_qty >= req_min * 0.7:
            score += w["quantity"] * 0.6
    else:
        # No minimum specified -- credit lot as long as it has any quantity.
        if lot_qty > 0:
            score += w["quantity"]

    # ----- Timing -------------------------------------------------------- #
    overlap = date_range_overlap(
        _get(lot, "harvest_date_from"),
        _get(lot, "harvest_date_to"),
        _get(req, "collection_from"),
        _get(req, "collection_to"),
    )
    score += w["timing"] * overlap

    # ----- Location ------------------------------------------------------ #
    lot_district = _get(lot, "farm_district")
    req_district = _get(req, "location_district")
    lot_state = _get(lot, "farm_state")
    req_state = _get(req, "location_state")
    if lot_district and req_district and str(lot_district).strip().lower() == str(req_district).strip().lower():
        score += w["location"]
    elif lot_state and req_state and str(lot_state).strip().lower() == str(req_state).strip().lower():
        score += w["location"] * 0.5

    return round(score, 3)


def build_match_reasons(lot: Any, req: Any) -> list[str]:
    """Compose a short human-readable list explaining why we matched."""
    reasons: list[str] = []
    if _get(lot, "commodity") == _get(req, "commodity"):
        reasons.append(f"commodity:{_get(lot, 'commodity')}")
    if _get(lot, "variety") and _get(req, "variety") and _get(lot, "variety") == _get(req, "variety"):
        reasons.append(f"variety:{_get(lot, 'variety')}")
    if _get(lot, "grade_predicted"):
        reasons.append(f"grade:{_get(lot, 'grade_predicted')}")
    try:
        if float(_get(lot, "quantity_kg_estimated") or 0) >= float(_get(req, "quantity_kg_min") or 0):
            reasons.append("quantity_full")
    except (TypeError, ValueError):
        pass
    if _get(lot, "farm_district") and _get(lot, "farm_district") == _get(req, "location_district"):
        reasons.append("district_match")
    return reasons


# --------------------------------------------------------------------------- #
# Default DB helpers -- Supabase via psycopg2. All wrapped so tests can patch.
# --------------------------------------------------------------------------- #
def _get_supabase_conn():
    """Open a psycopg2 connection to Supabase. Lazy import, may raise.

    Reads credentials from environment, mirroring scripts/apply_migrations_test.py.
    """
    import os
    import psycopg2

    host = os.environ.get("SUPABASE_DB_HOST")
    port = int(os.environ.get("SUPABASE_DB_PORT", "5432"))
    user = os.environ.get("SUPABASE_DB_USER", "postgres")
    password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    dbname = os.environ.get("SUPABASE_DB_NAME", "postgres")
    if not host or not password:
        raise RuntimeError(
            "SUPABASE_DB_HOST / SUPABASE_DB_PASSWORD not configured"
        )
    return psycopg2.connect(
        host=host, port=port, user=user, password=password, dbname=dbname,
        connect_timeout=10,
    )


@dataclass
class _Row:
    """Lightweight attribute-style wrapper around a dict row."""
    _data: dict

    def __getattr__(self, item: str) -> Any:
        try:
            return self._data[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def __getitem__(self, item: str) -> Any:
        return self._data[item]

    def get(self, item: str, default: Any = None) -> Any:
        return self._data.get(item, default)


def get_active_requirements() -> list[Any]:
    """Return all ACTIVE, non-expired trader requirements.

    Tests should monkeypatch this; the default implementation pulls from
    Supabase via psycopg2.
    """
    try:
        conn = _get_supabase_conn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("matching: cannot reach Supabase (%s)", exc)
        return []

    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, trader_id, region_code, commodity, variety,
                       quantity_kg_min, quantity_kg_max, grade,
                       price_per_kg_offered, collection_from, collection_to,
                       location_district, location_state, farm_pickup,
                       gi_required, status, matched_count, created_at,
                       expires_at
                FROM trader_requirements
                WHERE status = 'ACTIVE'
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY created_at ASC
                """
            )
            cols = [d[0] for d in cur.description]
            return [_Row(dict(zip(cols, r))) for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def get_lots_matching(
    *,
    commodity: str,
    variety: Optional[str],
    grade: Optional[Iterable[str]],
    harvest_from: Any,
    harvest_to: Any,
    region_code: Optional[str] = None,
) -> list[Any]:
    """Return candidate lots intersecting (commodity, harvest window, region).

    The match score still further filters via ``calculate_match_score``. We
    keep this query loose (commodity + window + region) so we don't miss
    near-misses on variety / grade that still score above 0.70.
    """
    try:
        conn = _get_supabase_conn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("matching: cannot reach Supabase (%s)", exc)
        return []

    h_from = _coerce_date(harvest_from)
    h_to = _coerce_date(harvest_to)
    try:
        with conn, conn.cursor() as cur:
            sql = [
                "SELECT id, lot_ref, farmer_id, plot_id, region_code,",
                "       commodity, variety, quantity_kg_estimated,",
                "       quantity_kg_min_acceptable, grade_predicted,",
                "       brix_estimated_min, brix_estimated_max,",
                "       harvest_date_from, harvest_date_to,",
                "       farm_district, farm_state, centroid_lat, centroid_lng,",
                "       satellite_verified, amed_verified, gi_verified,",
                "       gi_certificate_ref, min_price_per_kg, status,",
                "       created_at, expires_at",
                "FROM farmer_lots",
                "WHERE status IN ('AVAILABLE','PARTIALLY_MATCHED')",
                "  AND commodity = %s",
            ]
            params: list[Any] = [commodity]
            if region_code:
                sql.append("  AND region_code = %s")
                params.append(region_code)
            if h_from and h_to:
                # Lot's harvest window must intersect requirement's collection
                # window at all -- the score function takes it from there.
                sql.append("  AND harvest_date_to >= %s")
                sql.append("  AND harvest_date_from <= %s")
                params.extend([h_from, h_to])
            sql.append("ORDER BY created_at ASC")
            cur.execute("\n".join(sql), params)
            cols = [d[0] for d in cur.description]
            return [_Row(dict(zip(cols, r))) for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def create_match(lot: Any, req: Any, score: float, reasons: Optional[list[str]] = None) -> Any:
    """Insert one row into ``lot_matches`` and return the new match record."""
    reasons = reasons or build_match_reasons(lot, req)
    try:
        conn = _get_supabase_conn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("matching: cannot reach Supabase (%s)", exc)
        return _Row({
            "id": None, "lot_id": _get(lot, "id"),
            "requirement_id": _get(req, "id"),
            "farmer_id": _get(lot, "farmer_id"),
            "trader_id": _get(req, "trader_id"),
            "match_score": score, "match_reasons": reasons,
        })

    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lot_matches
                    (lot_id, requirement_id, farmer_id, trader_id,
                     match_score, match_reasons, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                RETURNING id, lot_id, requirement_id, farmer_id, trader_id,
                          match_score, match_reasons, created_at
                """,
                (
                    _get(lot, "id"), _get(req, "id"),
                    _get(lot, "farmer_id"), _get(req, "trader_id"),
                    score, reasons,
                ),
            )
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            # Bump matched_count on the requirement.
            cur.execute(
                "UPDATE trader_requirements "
                "   SET matched_count = COALESCE(matched_count,0) + 1 "
                " WHERE id = %s",
                (_get(req, "id"),),
            )
            # Mark the lot as PARTIALLY_MATCHED (a later trade confirms SOLD).
            cur.execute(
                "UPDATE farmer_lots SET status = 'PARTIALLY_MATCHED' "
                " WHERE id = %s AND status = 'AVAILABLE'",
                (_get(lot, "id"),),
            )
            return _Row(dict(zip(cols, row)))
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def notify_farmer_of_offer(
    *,
    farmer_id: Any,
    lot: Any,
    requirement: Any,
    match_id: Any,
) -> dict:
    """Send the Marathi/Hindi WhatsApp offer to the farmer.

    Real implementation lives in Agent 4's ``api/marketplace_whatsapp.py``.
    Stubbed here so this module is import-safe even if Agent 4 hasn't shipped
    yet; the function is the seam tests monkeypatch.
    """
    try:
        from api import marketplace_whatsapp  # type: ignore
        send_fn = getattr(marketplace_whatsapp, "send_farmer_trade_offer", None)
        if callable(send_fn):
            return send_fn(
                farmer_id=farmer_id, lot=lot,
                requirement=requirement, match_id=match_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("notify_farmer_of_offer: WhatsApp not wired (%s)", exc)
    # Stub return shape -- mirrors what Agent 4 will eventually return.
    return {
        "sent": False, "reason": "whatsapp_not_wired",
        "farmer_id": farmer_id, "match_id": match_id,
    }


def log_match_created(match: Any) -> None:
    """Audit log for matches created in this run."""
    logger.info(
        "MATCH created id=%s score=%s lot=%s req=%s",
        _get(match, "id"), _get(match, "match_score"),
        _get(match, "lot_id"), _get(match, "requirement_id"),
    )


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def run_daily_matching(
    *,
    get_active_requirements_fn: Optional[Callable[[], Iterable[Any]]] = None,
    get_lots_matching_fn: Optional[Callable[..., Iterable[Any]]] = None,
    create_match_fn: Optional[Callable[..., Any]] = None,
    notify_farmer_fn: Optional[Callable[..., Any]] = None,
    log_match_fn: Optional[Callable[[Any], None]] = None,
    threshold: float = MATCH_THRESHOLD,
) -> dict:
    """Match all active trader requirements with available farmer lots.

    Returns a small summary dict for logging / scheduler observability:
        {"requirements_seen": N, "candidates_seen": N,
         "matches_created": N, "notifications_attempted": N}
    """
    req_fn = get_active_requirements_fn or get_active_requirements
    lots_fn = get_lots_matching_fn or get_lots_matching
    match_fn = create_match_fn or create_match
    notify_fn = notify_farmer_fn or notify_farmer_of_offer
    log_fn = log_match_fn or log_match_created

    summary = {
        "requirements_seen": 0,
        "candidates_seen": 0,
        "matches_created": 0,
        "notifications_attempted": 0,
    }

    requirements = list(req_fn() or [])
    summary["requirements_seen"] = len(requirements)

    for req in requirements:
        try:
            grade_list = _get(req, "grade") or []
            candidates = list(lots_fn(
                commodity=_get(req, "commodity"),
                variety=_get(req, "variety"),
                grade=grade_list,
                harvest_from=_get(req, "collection_from"),
                harvest_to=_get(req, "collection_to"),
                region_code=_get(req, "region_code"),
            ) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("matching: get_lots_matching failed (%s)", exc)
            continue
        summary["candidates_seen"] += len(candidates)

        for lot in candidates:
            score = calculate_match_score(lot, req)
            if score < threshold:
                continue
            try:
                reasons = build_match_reasons(lot, req)
                match = match_fn(lot, req, score, reasons)
            except TypeError:
                # Older signature without reasons kwarg.
                match = match_fn(lot, req, score)
            except Exception as exc:  # noqa: BLE001
                logger.warning("matching: create_match failed (%s)", exc)
                continue
            summary["matches_created"] += 1
            try:
                log_fn(match)
            except Exception:  # noqa: BLE001
                pass
            try:
                notify_fn(
                    farmer_id=_get(lot, "farmer_id"),
                    lot=lot, requirement=req,
                    match_id=_get(match, "id"),
                )
                summary["notifications_attempted"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("matching: notify failed (%s)", exc)

    logger.info("run_daily_matching summary=%s", summary)
    return summary


__all__ = [
    "MATCH_THRESHOLD",
    "MATCH_WEIGHTS",
    "calculate_match_score",
    "build_match_reasons",
    "date_range_overlap",
    "get_active_requirements",
    "get_lots_matching",
    "create_match",
    "notify_farmer_of_offer",
    "log_match_created",
    "run_daily_matching",
]
