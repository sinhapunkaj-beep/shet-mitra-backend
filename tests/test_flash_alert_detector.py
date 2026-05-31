"""Tests for pipelines/flash_alert_detector.py - SDD 5.2."""

from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipelines.flash_alert_detector import (  # noqa: E402
    _ensure_table,
    check_flash_triggers,
    count_flash_alerts_this_week,
    enforce_weekly_limit,
    persist_flash_alerts,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _mk_db(tmp_path) -> Path:
    db = tmp_path / "trader.db"
    with sqlite3.connect(str(db)) as conn:
        _ensure_table(conn)
    return db


def _provider(*, prices=None, arrivals=None, weather=None):
    """Build provider trio that ignores commodity."""

    p = prices or {"latest": 100.0, "yesterday": 100.0}
    a = arrivals or {"forecast_mt": 1000.0, "actual_mt": 1000.0}
    w = weather or {"rain_tomorrow_mm": 0.0, "was_clear_yesterday": True}
    return (
        (lambda c: p),
        (lambda c: a),
        (lambda c: w),
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_price_drop_8pct_triggers(tmp_path):
    db = _mk_db(tmp_path)
    pp, ap, wp = _provider(
        prices={"latest": 90.0, "yesterday": 100.0},  # 10% drop
    )
    alerts = check_flash_triggers(
        ["Dry Grapes"], db_path=db,
        price_provider=pp, arrivals_provider=ap, weather_provider=wp,
    )
    drops = [a for a in alerts if a["trigger_type"] == "PRICE_DROP"]
    assert len(drops) == 1
    assert drops[0]["signal"] == "IMMEDIATE_BUY"
    assert drops[0]["price_after"] == 90.0
    assert drops[0]["price_before"] == 100.0


def test_arrival_shortage_triggers(tmp_path):
    db = _mk_db(tmp_path)
    pp, ap, wp = _provider(
        arrivals={"forecast_mt": 1000.0, "actual_mt": 400.0},  # -60%
    )
    alerts = check_flash_triggers(
        ["Dry Grapes"], db_path=db,
        price_provider=pp, arrivals_provider=ap, weather_provider=wp,
    )
    short = [a for a in alerts if a["trigger_type"] == "ARRIVAL_SHORTAGE"]
    assert len(short) == 1
    assert short[0]["signal"] == "IMMEDIATE_BUY"
    assert short[0]["arrivals_actual_mt"] == 400.0
    assert short[0]["arrivals_forecast_mt"] == 1000.0


def test_arrival_surplus_triggers(tmp_path):
    db = _mk_db(tmp_path)
    pp, ap, wp = _provider(
        arrivals={"forecast_mt": 1000.0, "actual_mt": 1500.0},  # +50%
    )
    alerts = check_flash_triggers(
        ["Dry Grapes"], db_path=db,
        price_provider=pp, arrivals_provider=ap, weather_provider=wp,
    )
    surp = [a for a in alerts if a["trigger_type"] == "ARRIVAL_SURPLUS"]
    assert len(surp) == 1
    assert surp[0]["signal"] == "SELL_NOW"


def test_weather_change_triggers(tmp_path):
    db = _mk_db(tmp_path)
    pp, ap, wp = _provider(
        weather={"rain_tomorrow_mm": 25.0, "was_clear_yesterday": True},
    )
    alerts = check_flash_triggers(
        ["Dry Grapes"], db_path=db,
        price_provider=pp, arrivals_provider=ap, weather_provider=wp,
    )
    wx = [a for a in alerts if a["trigger_type"] == "WEATHER_CHANGE"]
    assert len(wx) == 1
    assert wx[0]["signal"] == "HOLD"


def test_weekly_limit_enforced(tmp_path):
    db = _mk_db(tmp_path)
    # Pre-insert 3 rows dated "now" so they fall in the current week
    now = datetime.now(timezone.utc)
    with sqlite3.connect(str(db)) as conn:
        for _ in range(3):
            conn.execute(
                """INSERT INTO flash_alert_triggers
                   (id, commodity, trigger_type, trigger_description,
                    detected_at, alert_sent)
                   VALUES (?,?,?,?,?,?)""",
                (str(uuid.uuid4()), "Dry Grapes", "PRICE_DROP",
                 "pre-existing", now.isoformat(), 0),
            )
        conn.commit()
    candidates = [
        {"commodity": "Dry Grapes", "trigger_type": "PRICE_DROP",
         "signal": "IMMEDIATE_BUY", "description": "x"},
    ]
    allowed = enforce_weekly_limit(candidates, db, limit=3)
    assert allowed == []


def test_persist_flash_alerts(tmp_path):
    db = _mk_db(tmp_path)
    alerts = [
        {"commodity": "Dry Grapes", "trigger_type": "PRICE_DROP",
         "signal": "IMMEDIATE_BUY", "description": "drop",
         "price_before": 100.0, "price_after": 90.0,
         "arrivals_forecast_mt": None, "arrivals_actual_mt": None},
        {"commodity": "Pomegranate", "trigger_type": "ARRIVAL_SURPLUS",
         "signal": "SELL_NOW", "description": "surge",
         "price_before": None, "price_after": None,
         "arrivals_forecast_mt": 1000.0, "arrivals_actual_mt": 1500.0},
    ]
    ids = persist_flash_alerts(alerts, db)
    assert len(ids) == 2
    with sqlite3.connect(str(db)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM flash_alert_triggers")
        assert cur.fetchone()[0] == 2
        cur.execute(
            "SELECT commodity, trigger_type, alert_sent "
            "FROM flash_alert_triggers ORDER BY commodity"
        )
        rows = cur.fetchall()
        # Both alert_sent values should be 0 since we only persist.
        assert all(r[2] == 0 for r in rows)
        commodities = sorted(r[0] for r in rows)
        assert commodities == ["Dry Grapes", "Pomegranate"]
