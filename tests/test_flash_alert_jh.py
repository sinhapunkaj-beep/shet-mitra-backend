"""tests/test_flash_alert_jh.py — Jharkhand mandi watchlist (SDD §5.2 extension).

Verifies that the flash alert detector includes the Bhagalpur/Ranchi/
Deoghar/Dumka/Godda/Sahebganj/Patna/Munger/Malda/Murshidabad/Delhi
Azadpur mango mandis on the watchlist and fires alerts when one of
those mandis spikes — even when the all-India mango aggregate is
flat. All DB / provider side effects are mocked.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines import flash_alert_detector  # noqa: E402
from pipelines.flash_alert_detector import (  # noqa: E402
    JH_MANGO_MANDIS,
    MANGO_MANDI_WATCHLIST,
    MH_MANGO_MANDIS,
    _ensure_table,
    check_flash_triggers,
    mandi_watchlist_for,
)


def _mk_db(tmp_path) -> Path:
    db = tmp_path / "trader.db"
    with sqlite3.connect(str(db)) as conn:
        _ensure_table(conn)
    return db


def test_jh_mandis_present_in_watchlist():
    """SDD §5.2 — the JH mango watchlist must include Bhagalpur, Ranchi,
    Deoghar, Dumka, Godda, Sahebganj, Patna, Munger, Malda, Murshidabad
    and Delhi Azadpur."""
    expected = {
        "Bhagalpur APMC", "Ranchi APMC", "Deoghar APMC", "Dumka Mandi",
        "Godda Mandi", "Sahebganj Mandi", "Patna APMC", "Munger Mandi",
        "Malda APMC", "Murshidabad Mandi", "Delhi Azadpur APMC",
    }
    assert set(JH_MANGO_MANDIS) >= expected, (
        f"missing JH mandis: {expected - set(JH_MANGO_MANDIS)}"
    )
    # And those mandis must all flow through the union watchlist.
    assert set(MANGO_MANDI_WATCHLIST) >= set(JH_MANGO_MANDIS)
    assert set(MANGO_MANDI_WATCHLIST) >= set(MH_MANGO_MANDIS)


def test_mandi_watchlist_for_mango_returns_full_list():
    """The accessor returns the union of MH + JH watchlists for Mango and
    an empty tuple for non-mango commodities (so existing behaviour for
    Dry Grapes and Pomegranate stays unchanged)."""
    assert mandi_watchlist_for("Mango") == MANGO_MANDI_WATCHLIST
    assert mandi_watchlist_for("Dry Grapes") == ()
    assert mandi_watchlist_for("Pomegranate") == ()
    # Case-insensitive defensive check.
    assert mandi_watchlist_for("mango") == MANGO_MANDI_WATCHLIST


def test_bhagalpur_only_price_drop_triggers_alert(tmp_path):
    """A price drop that only hits Bhagalpur (not the all-India aggregate)
    must still fire because the JH watchlist is fanned out per-mandi."""
    db = _mk_db(tmp_path)

    def _prices(c):
        # Aggregate "Mango" looks flat; Bhagalpur APMC shows a 12% drop.
        if c == "Bhagalpur APMC":
            return {"latest": 60.0, "yesterday": 70.0}  # -14.3% drop
        return {"latest": 100.0, "yesterday": 100.0}

    def _arr(c):
        return {"forecast_mt": 1000.0, "actual_mt": 1000.0}

    def _wx(c):
        return {"rain_tomorrow_mm": 0.0, "was_clear_yesterday": True}

    alerts = check_flash_triggers(
        ["Mango"], db_path=db,
        price_provider=_prices, arrivals_provider=_arr, weather_provider=_wx,
    )
    bhagalpur = [a for a in alerts if a.get("mandi") == "Bhagalpur APMC"]
    assert bhagalpur, "expected a flash alert tagged with mandi=Bhagalpur APMC"
    assert bhagalpur[0]["trigger_type"] == "PRICE_DROP"
    assert bhagalpur[0]["commodity"] == "Mango"
    assert bhagalpur[0]["signal"] == "IMMEDIATE_BUY"


def test_delhi_azadpur_arrival_surplus_triggers_sell_now(tmp_path):
    """A Delhi Azadpur surplus must fire — Azadpur is the national price
    signal anchor and arrivals there matter for JH traders too."""
    db = _mk_db(tmp_path)

    def _prices(c):
        return {"latest": 100.0, "yesterday": 100.0}

    def _arr(c):
        if c == "Delhi Azadpur APMC":
            return {"forecast_mt": 1000.0, "actual_mt": 1800.0}  # +80%
        return {"forecast_mt": 1000.0, "actual_mt": 1000.0}

    def _wx(c):
        return {"rain_tomorrow_mm": 0.0, "was_clear_yesterday": True}

    alerts = check_flash_triggers(
        ["Mango"], db_path=db,
        price_provider=_prices, arrivals_provider=_arr, weather_provider=_wx,
    )
    delhi = [a for a in alerts if a.get("mandi") == "Delhi Azadpur APMC"]
    assert delhi, "expected a flash alert tagged with mandi=Delhi Azadpur APMC"
    assert delhi[0]["signal"] == "SELL_NOW"
    assert delhi[0]["trigger_type"] == "ARRIVAL_SURPLUS"


def test_non_mango_commodities_do_not_fan_out(tmp_path):
    """Dry Grapes / Pomegranate must NOT fan out across mango mandis —
    we don't want JH mandi calls polluting MH grape alerts."""
    db = _mk_db(tmp_path)
    seen_commodities = []

    def _prices(c):
        seen_commodities.append(c)
        return {"latest": 100.0, "yesterday": 100.0}

    def _arr(c):
        return {"forecast_mt": 1000.0, "actual_mt": 1000.0}

    def _wx(c):
        return {"rain_tomorrow_mm": 0.0, "was_clear_yesterday": True}

    check_flash_triggers(
        ["Dry Grapes"], db_path=db,
        price_provider=_prices, arrivals_provider=_arr, weather_provider=_wx,
    )
    # Should only call the price provider for "Dry Grapes" — no mandi fan-out.
    assert seen_commodities == ["Dry Grapes"], (
        f"unexpected commodities visited: {seen_commodities}"
    )
