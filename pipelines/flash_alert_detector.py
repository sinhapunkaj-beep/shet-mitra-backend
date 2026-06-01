"""pipelines/flash_alert_detector.py

ShetMitra - Trader Intelligence - Flash Alert Detector
SDD section 5.2.

The 4 trigger conditions are pure: each takes already-resolved values and
returns either an alert dict or None. ``check_flash_triggers`` orchestrates
the providers and returns the list of detected alerts WITHOUT performing
any side effects. Persistence and weekly-limit enforcement are split out
so tests can compose them independently.

Public API
----------
    check_flash_triggers(commodities=("Dry Grapes","Pomegranate","Mango"),
                         *, db_path=..., clock=None,
                         price_provider=None, arrivals_provider=None,
                         weather_provider=None) -> list[dict]

    count_flash_alerts_this_week(db_path) -> int
    persist_flash_alerts(triggered, db_path) -> list[str]
    enforce_weekly_limit(triggered, db_path, limit=3) -> list[dict]
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _REPO_ROOT / "data" / "test.db"


# --------------------------------------------------------------------------- #
# Mandi watchlists (SDD §5.2 + JH/Bihar/Bengal/Delhi extension)
# --------------------------------------------------------------------------- #
#: Maharashtra mango mandis the flash detector watches by default.
MH_MANGO_MANDIS: tuple[str, ...] = (
    "Vashi APMC",
    "Ratnagiri APMC",
    "Pune APMC",
    "Mumbai Vashi",
)

#: Jharkhand / Bihar / West Bengal / Delhi mango mandis added for
#: Bagaan Sathi farmers and traders. Detection rule mirrors the existing
#: Maharashtra logic — only the mandi list grows.
JH_MANGO_MANDIS: tuple[str, ...] = (
    "Bhagalpur APMC",
    "Ranchi APMC",
    "Deoghar APMC",
    "Dumka Mandi",
    "Godda Mandi",
    "Sahebganj Mandi",
    "Patna APMC",
    "Munger Mandi",
    "Malda APMC",
    "Murshidabad Mandi",
    "Delhi Azadpur APMC",
)

#: Union of MH + JH mandi watchlists used when commodity == "Mango".
MANGO_MANDI_WATCHLIST: tuple[str, ...] = MH_MANGO_MANDIS + JH_MANGO_MANDIS


# --------------------------------------------------------------------------- #
# Schema helper
# --------------------------------------------------------------------------- #
def _ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotently create a lightweight flash_alert_triggers table.

    The production schema (SDD 4.6) uses uuid + timestamptz; the local SQLite
    mirror uses text columns. Trader Agent 1 owns the canonical migration -
    this helper is a no-op when the table already exists with that schema.
    """

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS flash_alert_triggers (
            id TEXT PRIMARY KEY,
            commodity TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            trigger_description TEXT,
            price_before REAL,
            price_after REAL,
            arrivals_forecast_mt REAL,
            arrivals_actual_mt REAL,
            alert_sent INTEGER DEFAULT 0,
            report_id TEXT,
            detected_at TEXT NOT NULL
        )
        """
    )


# --------------------------------------------------------------------------- #
# Default providers
# --------------------------------------------------------------------------- #
def _commodity_db_crop(commodity: str) -> str:
    if commodity.lower().replace("_", " ").startswith("dry grapes"):
        return "Grapes"
    return commodity.split()[0]


def _default_price_provider(commodity: str, *, db_path: Path = _DEFAULT_DB) -> dict:
    """Returns {'latest': float, 'yesterday': float}.

    Reads the two most recent rows for ``commodity`` from price_history_training
    if present; otherwise returns synthetic baseline (100, 100) so tests that
    don't inject a provider still get a defined shape.
    """

    fallback = {"latest": 100.0, "yesterday": 100.0}
    if not Path(db_path).exists():
        return fallback

    target = commodity.replace(" ", "_")
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT price_modal_kg
                    FROM price_history_training
                    WHERE commodity = ?
                    ORDER BY arrival_date DESC
                    LIMIT 2
                    """,
                    (target,),
                )
                rows = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
            except sqlite3.OperationalError:
                rows = []
        if len(rows) >= 2:
            return {"latest": rows[0], "yesterday": rows[1]}
        if len(rows) == 1:
            return {"latest": rows[0], "yesterday": rows[0]}
    except sqlite3.Error as exc:
        logger.warning("price_provider DB lookup failed (%s)", exc)
    return fallback


def _default_arrivals_provider(commodity: str, *, db_path: Path = _DEFAULT_DB) -> dict:
    """Returns {'forecast_mt': float, 'actual_mt': float}.

    Forecast = latest estimated_volume_mt from amed_belt_data for the crop.
    Actual = synthetic equal-to-forecast unless something better is available
    (real arrivals will be wired in by another agent).
    """

    fallback = {"forecast_mt": 1000.0, "actual_mt": 1000.0}
    if not Path(db_path).exists():
        return fallback
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT estimated_volume_mt
                FROM amed_belt_data
                WHERE crop_type = ?
                ORDER BY fetch_date DESC
                LIMIT 1
                """,
                (_commodity_db_crop(commodity),),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                forecast = float(row[0])
                return {"forecast_mt": forecast, "actual_mt": forecast}
    except sqlite3.Error as exc:
        logger.warning("arrivals_provider DB lookup failed (%s)", exc)
    return fallback


def _default_weather_provider(commodity: str) -> dict:
    return {"rain_tomorrow_mm": 0.0, "was_clear_yesterday": True}


# --------------------------------------------------------------------------- #
# Individual trigger evaluators
# --------------------------------------------------------------------------- #
def _trigger_price_drop(commodity: str, prices: dict) -> Optional[dict]:
    latest = float(prices.get("latest", 0.0))
    yesterday = float(prices.get("yesterday", 0.0))
    if yesterday <= 0 or latest <= 0:
        return None
    if latest < yesterday * 0.92:
        drop_pct = (1.0 - latest / yesterday) * 100.0
        return {
            "commodity": commodity,
            "trigger_type": "PRICE_DROP",
            "signal": "IMMEDIATE_BUY",
            "price_before": yesterday,
            "price_after": latest,
            "arrivals_forecast_mt": None,
            "arrivals_actual_mt": None,
            "description": (
                f"Modal price fell {drop_pct:.1f}% vs yesterday "
                f"(Rs{yesterday:.0f} -> Rs{latest:.0f}/kg). "
                f"Sudden oversupply - buying opportunity."
            ),
        }
    return None


def _trigger_arrival_shortage(commodity: str, arrivals: dict) -> Optional[dict]:
    forecast = float(arrivals.get("forecast_mt", 0.0))
    actual = float(arrivals.get("actual_mt", 0.0))
    if forecast <= 0:
        return None
    if actual < forecast * 0.60:
        return {
            "commodity": commodity,
            "trigger_type": "ARRIVAL_SHORTAGE",
            "signal": "IMMEDIATE_BUY",
            "price_before": None,
            "price_after": None,
            "arrivals_forecast_mt": forecast,
            "arrivals_actual_mt": actual,
            "description": (
                f"Arrivals only {actual:.0f} MT vs forecast {forecast:.0f} MT "
                f"(-{(1.0 - actual / forecast) * 100:.0f}%). Price spike expected."
            ),
        }
    return None


def _trigger_arrival_surplus(commodity: str, arrivals: dict) -> Optional[dict]:
    forecast = float(arrivals.get("forecast_mt", 0.0))
    actual = float(arrivals.get("actual_mt", 0.0))
    if forecast <= 0:
        return None
    if actual > forecast * 1.40:
        return {
            "commodity": commodity,
            "trigger_type": "ARRIVAL_SURPLUS",
            "signal": "SELL_NOW",
            "price_before": None,
            "price_after": None,
            "arrivals_forecast_mt": forecast,
            "arrivals_actual_mt": actual,
            "description": (
                f"Arrivals surge: {actual:.0f} MT vs forecast {forecast:.0f} MT "
                f"(+{(actual / forecast - 1.0) * 100:.0f}%). Supply pressure - sell now."
            ),
        }
    return None


def _trigger_weather_change(commodity: str, weather: dict) -> Optional[dict]:
    rain_mm = float(weather.get("rain_tomorrow_mm", 0.0))
    was_clear = bool(weather.get("was_clear_yesterday", False))
    if rain_mm > 20 and was_clear:
        return {
            "commodity": commodity,
            "trigger_type": "WEATHER_CHANGE",
            "signal": "HOLD",
            "price_before": None,
            "price_after": None,
            "arrivals_forecast_mt": None,
            "arrivals_actual_mt": None,
            "description": (
                f"Weather window closing: {rain_mm:.0f} mm rain expected tomorrow "
                f"after clear conditions yesterday. Hold and re-assess Friday."
            ),
        }
    return None


# --------------------------------------------------------------------------- #
# Orchestration (detection only - no persistence here)
# --------------------------------------------------------------------------- #
def mandi_watchlist_for(commodity: str) -> tuple[str, ...]:
    """Return the mandi watchlist for a commodity.

    Mango fans out across the union of MH + JH mandi lists; every other
    commodity returns an empty tuple, which means the existing
    commodity-level provider call (without mandi context) is used.
    """
    if (commodity or "").strip().lower() == "mango":
        return MANGO_MANDI_WATCHLIST
    return ()


def check_flash_triggers(
    commodities: Iterable[str] = ("Dry Grapes", "Pomegranate", "Mango"),
    *,
    db_path: Path | str = _DEFAULT_DB,
    clock: Optional[Callable[[], datetime]] = None,  # noqa: ARG001
    price_provider: Optional[Callable[[str], dict]] = None,
    arrivals_provider: Optional[Callable[[str], dict]] = None,
    weather_provider: Optional[Callable[[str], dict]] = None,
) -> list[dict]:
    """Check all flash trigger conditions for every commodity in ``commodities``.

    For Mango we additionally fan out across :data:`MANGO_MANDI_WATCHLIST`
    so a Jharkhand price spike at e.g. Bhagalpur triggers an alert even
    when the all-India aggregate is quiet. Each provider receives the
    bare commodity name; if a caller wants per-mandi pricing they should
    inject a provider that inspects ``commodity`` and returns the right
    payload.

    Returns the list of triggered alert dicts. Does NOT persist them and
    does NOT enforce the weekly cap - callers do that explicitly with
    ``persist_flash_alerts`` and ``enforce_weekly_limit``.
    """

    pp = price_provider or (lambda c: _default_price_provider(c, db_path=Path(db_path)))
    ap = arrivals_provider or (lambda c: _default_arrivals_provider(c, db_path=Path(db_path)))
    wp = weather_provider or _default_weather_provider

    triggered: list[dict] = []
    for commodity in commodities:
        try:
            prices = pp(commodity)
            arrivals = ap(commodity)
            weather = wp(commodity)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provider error for %s: %s", commodity, exc)
            continue

        for fn, payload in (
            (_trigger_price_drop, prices),
            (_trigger_arrival_shortage, arrivals),
            (_trigger_arrival_surplus, arrivals),
            (_trigger_weather_change, weather),
        ):
            alert = fn(commodity, payload)
            if alert is not None:
                triggered.append(alert)

        # Mango-only fan-out across the JH/MH mandi watchlist. Each mandi
        # is hit with the provider trio so a single-mandi spike is caught
        # even when the all-India aggregate is muted. The orchestrator
        # tolerates provider raises by skipping that mandi.
        for mandi in mandi_watchlist_for(commodity):
            try:
                m_prices = pp(mandi)
                m_arrivals = ap(mandi)
                m_weather = wp(mandi)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "provider error for mandi %s under %s: %s",
                    mandi, commodity, exc,
                )
                continue
            for fn, payload in (
                (_trigger_price_drop, m_prices),
                (_trigger_arrival_shortage, m_arrivals),
                (_trigger_arrival_surplus, m_arrivals),
                (_trigger_weather_change, m_weather),
            ):
                alert = fn(commodity, payload)
                if alert is not None:
                    alert = dict(alert)
                    alert["mandi"] = mandi
                    triggered.append(alert)

    return triggered


# --------------------------------------------------------------------------- #
# Weekly limit + persistence
# --------------------------------------------------------------------------- #
def _week_start(now: datetime) -> datetime:
    """Monday 00:00 of the week containing ``now`` (UTC)."""

    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def count_flash_alerts_this_week(
    db_path: Path | str,
    *,
    clock: Optional[Callable[[], datetime]] = None,
) -> int:
    """Number of flash_alert_triggers rows detected since this week's Monday."""

    path = Path(db_path)
    if not path.exists():
        return 0

    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    start_iso = _week_start(now).isoformat()

    try:
        with sqlite3.connect(str(path)) as conn:
            _ensure_table(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM flash_alert_triggers WHERE detected_at >= ?",
                (start_iso,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        logger.warning("count_flash_alerts_this_week failed (%s)", exc)
        return 0


def persist_flash_alerts(
    triggered: list[dict],
    db_path: Path | str,
    *,
    clock: Optional[Callable[[], datetime]] = None,
) -> list[str]:
    """Insert each alert as a flash_alert_triggers row. Returns inserted IDs."""

    if not triggered:
        return []

    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_iso = now.isoformat()

    inserted: list[str] = []
    with sqlite3.connect(str(db_path)) as conn:
        _ensure_table(conn)
        cur = conn.cursor()
        for alert in triggered:
            row_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO flash_alert_triggers
                  (id, commodity, trigger_type, trigger_description,
                   price_before, price_after,
                   arrivals_forecast_mt, arrivals_actual_mt,
                   alert_sent, detected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row_id,
                    alert.get("commodity"),
                    alert.get("trigger_type"),
                    alert.get("description"),
                    alert.get("price_before"),
                    alert.get("price_after"),
                    alert.get("arrivals_forecast_mt"),
                    alert.get("arrivals_actual_mt"),
                    0,
                    now_iso,
                ),
            )
            inserted.append(row_id)
        conn.commit()
    return inserted


def enforce_weekly_limit(
    triggered: list[dict],
    db_path: Path | str,
    limit: int = 3,
    *,
    clock: Optional[Callable[[], datetime]] = None,
) -> list[dict]:
    """Trim ``triggered`` to fit within the weekly cap.

    Returns at most ``limit - already_sent_this_week`` items. If the cap is
    already exhausted, returns an empty list.
    """

    already = count_flash_alerts_this_week(db_path, clock=clock)
    remaining = max(0, int(limit) - int(already))
    return list(triggered[:remaining])


__all__ = [
    "check_flash_triggers",
    "count_flash_alerts_this_week",
    "persist_flash_alerts",
    "enforce_weekly_limit",
    "mandi_watchlist_for",
    "MH_MANGO_MANDIS",
    "JH_MANGO_MANDIS",
    "MANGO_MANDI_WATCHLIST",
]
