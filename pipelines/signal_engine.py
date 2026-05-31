"""pipelines/signal_engine.py

ShetMitra - Trader Intelligence - Signal Generation Engine
SDD section 5.1.

Pure logic with pluggable providers so the engine can be unit-tested with
synthetic data while still defaulting to real DB/CSV/model lookups in
production.

Public API
----------
    generate_signal(commodity, *, variety=None, market=None,
                    price_predictor=None, belt_provider=None,
                    historical_provider=None, weather_provider=None,
                    market_provider=None) -> dict

    calculate_fair_value(historical_avg, quality_adj, supply_adj,
                         seasonal_factor) -> float

Return shape (SDD 5.1):
    {
      'signal': 'BUY'|'SELL'|'HOLD',
      'rationale': str,
      'entry_range': [low, high],
      'target': float,
      'stop': float,
      'horizon_days': int,
      'confidence': float,
      'fair_value': float,
      'discount_pct': float,
    }
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _REPO_ROOT / "data" / "test.db"
_DEFAULT_PRICE_CSV = _REPO_ROOT / "data" / "price_history_training_backfilled.csv"

# Sensible per-commodity priors for when we have no historical data at all.
# Numbers are weekly average modal price (Rs/kg) and seasonal index baselines.
_HISTORICAL_FALLBACKS: dict[str, dict[str, float]] = {
    "Dry_Grapes":   {"week_avg": 125.0, "avg_volume_mt": 1200.0, "seasonal_index": 1.00},
    "Dry Grapes":   {"week_avg": 125.0, "avg_volume_mt": 1200.0, "seasonal_index": 1.00},
    "Pomegranate":  {"week_avg": 110.0, "avg_volume_mt":  900.0, "seasonal_index": 1.00},
    "Alphonso":     {"week_avg": 250.0, "avg_volume_mt":  600.0, "seasonal_index": 1.05},
    "Mango Alphonso": {"week_avg": 250.0, "avg_volume_mt":  600.0, "seasonal_index": 1.05},
    "Kesar":        {"week_avg": 180.0, "avg_volume_mt":  650.0, "seasonal_index": 1.02},
    "Mango Kesar":  {"week_avg": 180.0, "avg_volume_mt":  650.0, "seasonal_index": 1.02},
    "Mango":        {"week_avg": 200.0, "avg_volume_mt":  700.0, "seasonal_index": 1.02},
}


# --------------------------------------------------------------------------- #
# Fair value
# --------------------------------------------------------------------------- #
def calculate_fair_value(
    historical_avg: float,
    quality_adj: float,
    supply_adj: float,
    seasonal_factor: float,
    historical_avg_volume_mt: float = 1000.0,
) -> float:
    """Compute a fair-value reference price.

    Heuristic (documented for the synthetic data set we ship with):
      * base = historical_avg * seasonal_factor
      * quality multiplier:  0.9 + 0.2 * quality_adj      (quality in [0,1])
      * supply correction:   1 - 0.0001 * (supply_adj - historical_avg_volume)
        clipped to [0.85, 1.20].

    The two adjustments are deliberately small-amplitude so the fair value
    stays in the same order of magnitude as the historical average and is
    not whipsawed by noisy belt-volume readings.
    """

    q = max(0.0, min(1.0, float(quality_adj)))
    base = float(historical_avg) * float(seasonal_factor) * (0.9 + 0.2 * q)

    supply_delta = float(supply_adj) - float(historical_avg_volume_mt)
    supply_correction = 1.0 - 0.0001 * supply_delta
    supply_correction = max(0.85, min(1.20, supply_correction))

    return float(base * supply_correction)


# --------------------------------------------------------------------------- #
# Default providers
# --------------------------------------------------------------------------- #
def _commodity_db_crop(commodity: str) -> str:
    """Map signal-engine commodity label -> amed_belt_data.crop_type label."""

    if commodity.lower().replace("_", " ").startswith("dry grapes"):
        return "Grapes"
    return commodity.split()[0]


def _default_belt_provider(commodity: str, *, db_path: Path = _DEFAULT_DB) -> dict:
    """Latest amed_belt_data row for the matching crop, with safe fallbacks."""

    fallback = {"health_pct_good": 70.0, "estimated_volume_mt": 1000.0,
                "fields_harvesting": 0, "total_area_acres": 0.0}
    if not Path(db_path).exists():
        return fallback
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            crop = _commodity_db_crop(commodity)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT health_pct_good, estimated_volume_mt,
                       fields_harvesting, total_area_acres
                FROM amed_belt_data
                WHERE crop_type = ?
                ORDER BY fetch_date DESC
                LIMIT 1
                """,
                (crop,),
            )
            row = cur.fetchone()
            if row is None:
                return fallback
            return {
                "health_pct_good": float(row["health_pct_good"] or 70.0),
                "estimated_volume_mt": float(row["estimated_volume_mt"] or 1000.0),
                "fields_harvesting": int(row["fields_harvesting"] or 0),
                "total_area_acres": float(row["total_area_acres"] or 0.0),
            }
    except sqlite3.Error as exc:
        logger.warning("belt_provider DB lookup failed (%s) - using fallback", exc)
        return fallback


def _default_historical_provider(
    commodity: str, *, csv_path: Path = _DEFAULT_PRICE_CSV
) -> dict:
    """Compute (week_avg, avg_volume_mt, seasonal_index) from the backfill CSV."""

    fb_key = commodity if commodity in _HISTORICAL_FALLBACKS else commodity.replace(" ", "_")
    fallback = _HISTORICAL_FALLBACKS.get(
        fb_key, {"week_avg": 100.0, "avg_volume_mt": 1000.0, "seasonal_index": 1.0}
    )

    if not Path(csv_path).exists():
        return dict(fallback)

    target = commodity.replace(" ", "_")
    prices: list[float] = []
    volumes: list[float] = []
    try:
        with open(csv_path, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if (row.get("commodity") or "").strip() != target:
                    continue
                try:
                    p = float(row.get("price_modal_kg") or "nan")
                    if p > 0:
                        prices.append(p)
                except (TypeError, ValueError):
                    pass
                try:
                    v = float(row.get("arrivals_mt") or "nan")
                    if v > 0:
                        volumes.append(v)
                except (TypeError, ValueError):
                    pass
    except OSError as exc:
        logger.warning("historical CSV read failed (%s) - using fallback", exc)
        return dict(fallback)

    week_avg = (sum(prices) / len(prices)) if prices else fallback["week_avg"]
    avg_volume = (sum(volumes) / len(volumes)) if volumes else fallback["avg_volume_mt"]
    return {
        "week_avg": float(week_avg),
        "avg_volume_mt": float(avg_volume),
        "seasonal_index": float(fallback["seasonal_index"]),
    }


def _default_weather_provider(commodity: str) -> dict:
    return {"summary_7day": "Clear with brief showers possible Thu-Fri"}


def _default_market_provider(
    commodity: str,
    *,
    market: Optional[str] = None,
    csv_path: Path = _DEFAULT_PRICE_CSV,
) -> float:
    """Latest price_modal_kg from the CSV. Falls back to historical_avg."""

    target = commodity.replace(" ", "_")
    last_price: Optional[float] = None
    last_date: Optional[str] = None
    if Path(csv_path).exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if (row.get("commodity") or "").strip() != target:
                        continue
                    if market and (row.get("mandi") or "").strip().lower() != market.lower():
                        continue
                    try:
                        p = float(row.get("price_modal_kg") or "nan")
                    except (TypeError, ValueError):
                        continue
                    d = (row.get("arrival_date") or "").strip()
                    if p > 0 and (last_date is None or d >= last_date):
                        last_price = p
                        last_date = d
        except OSError as exc:
            logger.warning("market_provider CSV read failed (%s)", exc)

    if last_price is None:
        last_price = _HISTORICAL_FALLBACKS.get(
            target, {"week_avg": 100.0}
        )["week_avg"]
    return float(last_price)


def _default_price_predictor(
    commodity: str, *, current_price: float, belt_volume_mt: float,
) -> dict:
    """Default predictor: try utils.price_prediction.PricePredictor, then fall
    back to a deterministic naive forecast that satisfies the SDD shape.
    """

    try:
        from utils.price_prediction import PricePredictor  # local import

        predictor = PricePredictor()
        out = predictor.predict_price(
            commodity if "_" in commodity else commodity.replace(" ", "_"),
            current_features={"amed_belt_volume_mt": belt_volume_mt},
            belt_volume_forecast=[belt_volume_mt, belt_volume_mt],
            horizon_days=3,
        )
        preds = out.get("predictions") or []
        if len(preds) >= 3:
            day1, day2, day3 = preds[0], preds[1], preds[2]
            # Extrapolate day7 from day3 trend
            slope = (day3 - day1) / 2.0
            day7 = float(day3 + slope * 4)
            return {
                "day1": float(day1),
                "day3": float(day3),
                "day7": day7,
                "confidence_day3": float(out.get("confidence", 0.6)),
            }
    except Exception as exc:  # noqa: BLE001
        logger.info("PricePredictor unavailable (%s) - using naive forecast", exc)

    # Naive: assume mild reversion toward current_price.
    return {
        "day1": float(current_price),
        "day3": float(current_price),
        "day7": float(current_price),
        "confidence_day3": 0.55,
    }


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def generate_signal(
    commodity: str,
    *,
    variety: Optional[str] = None,
    market: Optional[str] = None,
    price_predictor: Optional[Callable[..., dict]] = None,
    belt_provider: Optional[Callable[[str], dict]] = None,
    historical_provider: Optional[Callable[[str], dict]] = None,
    weather_provider: Optional[Callable[[str], dict]] = None,
    market_provider: Optional[Callable[..., float]] = None,
) -> dict:
    """Generate a BUY/SELL/HOLD signal for ``commodity``.

    Providers default to DB/CSV/model lookups but each can be injected for
    deterministic tests. See module docstring for return shape.
    """

    bp = belt_provider or _default_belt_provider
    hp = historical_provider or _default_historical_provider
    wp = weather_provider or _default_weather_provider
    mp = market_provider or (lambda c: _default_market_provider(c, market=market))

    belt = dict(bp(commodity))
    historical = dict(hp(commodity))
    weather = dict(wp(commodity))  # noqa: F841 - retained for symmetry / future use

    current_price = float(mp(commodity))

    # Quality_adj in [0,1]: belt health_pct_good is normally 0..100.
    quality_pct = float(belt.get("health_pct_good", 70.0))
    quality_adj = max(0.0, min(1.0, quality_pct / 100.0))

    estimated_volume_mt = float(belt.get("estimated_volume_mt", 1000.0))
    historical_avg_volume = float(historical.get("avg_volume_mt", 1000.0))

    fair_value = calculate_fair_value(
        historical_avg=float(historical.get("week_avg", current_price)),
        quality_adj=quality_adj,
        supply_adj=estimated_volume_mt,
        seasonal_factor=float(historical.get("seasonal_index", 1.0)),
        historical_avg_volume_mt=historical_avg_volume,
    )

    if fair_value > 0:
        discount_pct = (fair_value - current_price) / fair_value * 100.0
    else:
        discount_pct = 0.0

    # Price predictor
    if price_predictor is None:
        pred = _default_price_predictor(
            commodity,
            current_price=current_price,
            belt_volume_mt=estimated_volume_mt,
        )
    else:
        try:
            pred = price_predictor(
                commodity,
                current_price=current_price,
                belt_volume_mt=estimated_volume_mt,
            )
        except TypeError:
            # Allow zero-arg or single-arg predictors for tests.
            pred = price_predictor(commodity)

    # Normalise predictor shape: accept either SDD shape or PricePredictor shape.
    if "day3" not in pred and "predictions" in pred:
        preds = list(pred.get("predictions") or [])
        while len(preds) < 3:
            preds.append(current_price)
        slope = (preds[2] - preds[0]) / 2.0
        pred = {
            "day1": float(preds[0]),
            "day3": float(preds[2]),
            "day7": float(preds[2] + slope * 4),
            "confidence_day3": float(pred.get("confidence", 0.6)),
        }

    day1 = float(pred.get("day1", current_price))
    day3 = float(pred.get("day3", current_price))
    day7 = float(pred.get("day7", day3))
    confidence = float(pred.get("confidence_day3", pred.get("confidence", 0.6)))

    # Signal logic (verbatim SDD 5.1)
    if discount_pct > 10 and day3 > current_price * 1.05:
        signal = "BUY"
        rationale = (
            f"Price {discount_pct:.1f}% below fair value. "
            f"Supply forecast shows {estimated_volume_mt:.0f} MT arriving "
            f"this week - below seasonal average. "
            f"Price recovery of Rs{day3 - current_price:.0f}/kg expected by Day 3."
        )
    elif day3 < current_price * 0.97 and estimated_volume_mt > historical_avg_volume * 1.15:
        surplus_pct = ((estimated_volume_mt / historical_avg_volume) - 1.0) * 100.0
        signal = "SELL"
        rationale = (
            f"Above-average supply of {estimated_volume_mt:.0f} MT forecast "
            f"this week (+{surplus_pct:.0f}% vs average). "
            f"Price pressure expected. Sell before peak arrivals."
        )
    else:
        signal = "HOLD"
        rationale = (
            f"Prices within normal range. "
            f"Supply at {estimated_volume_mt:.0f} MT - near average. "
            f"No strong directional signal this week. "
            f"Monitor for Friday APMC results."
        )

    # If the price predictor flagged the prediction as a disruption (day-1
    # delta vs current price > 15%), the standard signal can't be trusted.
    # Force HOLD, downgrade confidence, surface the trader-facing message.
    unusual = bool(pred.get("unusual_market_conditions", False))
    disruption_message = pred.get("disruption_message")

    if unusual:
        signal = "HOLD"
        rationale = (
            disruption_message
            or "Unusual market conditions detected. Standard forecast suspended."
        )
        confidence = min(confidence, 0.35)

    out = {
        "signal": signal,
        "rationale": rationale,
        "entry_range": [current_price * 0.98, current_price * 1.02],
        "target": float(day7),
        "stop": float(current_price * 0.93),
        "horizon_days": 5,
        "confidence": float(confidence),
        "fair_value": float(fair_value),
        "discount_pct": float(discount_pct),
        "unusual_market_conditions": unusual,
    }
    if unusual:
        out["disruption_message"] = (
            disruption_message
            or "Unusual market conditions detected. Standard forecast suspended. "
               "Monitor daily."
        )
        out["disruption_delta_pct"] = pred.get("disruption_delta_pct")
    return out


__all__ = ["generate_signal", "calculate_fair_value"]
