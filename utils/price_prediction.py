"""
utils/price_prediction.py
ShetMitra — Agent 5 — Production Price Predictor

Loads the v3 (outlier-flag + tuned RF + commodity-specific seasonal features)
price model by default. Falls back to v2 (AMED-enhanced) if v3 is missing,
and to the v1 baseline if both are missing. Used by the advisory engine and
the daily price prediction pipeline.

Mango branch (SDD Section 5.4): when commodity='Mango' is requested the
predictor routes to a per-variety pickle (arima_mango_<variety>.pkl). The
optional `bearing_year` argument (ON/OFF/UNKNOWN) feeds an advisory-only
`bearing_price_adjust` field on the response - it does NOT mutate the model's
predicted price, since bearing affects supply volume rather than the per-kg
price target (per SDD Section 2.2).

Public API
----------
    PricePredictor()                  -> instance
    .predict_price(
        commodity: str,
        current_features: dict,
        belt_volume_forecast: list[float] | None = None,
        correspondent_arrivals: float | None = None,
        horizon_days: int = 3,
        variety: str | None = None,
        bearing_year: str | None = None,
    ) -> {
        'predictions':    [day1, day2, day3, ...],
        'confidence':     float in [0.3, 0.95],
        'model_version':  'v3' | 'v2' | 'v1',
        'supply_pressure':'high' | 'medium' | 'low',
        'model_kind':     'arimax_v1'|'arimax_v2'|'arimax_common'|'arimax_full'
                          |'random_forest'|'log_naive',
        # Mango-only optional fields:
        'variety':              str,
        'bearing_year':         'ON'|'OFF'|'UNKNOWN',
        'bearing_price_adjust': float (yield multiplier hint, advisory only),
    }
"""

from __future__ import annotations

import logging
import sqlite3
import warnings
from pathlib import Path
from typing import Any, Iterable, Optional

import joblib
import numpy as np

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

ROOT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT_DIR / "data" / "models"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "test.db"

# Filename convention used by scripts/train_price_model.py.
COMMODITY_SLUG = {
    "Dry_Grapes": "dry_grapes",
    "Pomegranate": "pomegranate",
    "Mango": "mango",
}

# Per SDD Section 2.2: bearing-year yield multipliers (advisory only - they
# describe expected supply volume, not per-kg price).
BEARING_YIELD_MULTIPLIER = {
    "ON": 1.0,
    "OFF": 0.45,
    "UNKNOWN": 0.75,
}

MANGO_VARIETIES = (
    "alphonso",
    "kesar",
    "dasheri",
    "totapuri",
    "banganapalli",
)


def _commodity_slug(commodity: str) -> str:
    return COMMODITY_SLUG.get(commodity, commodity.lower().replace(" ", "_"))


def _normalise_variety(variety: Optional[str]) -> Optional[str]:
    if variety is None:
        return None
    return str(variety).strip().lower().replace(" ", "_")


def _normalise_bearing(bearing_year: Optional[str]) -> Optional[str]:
    if bearing_year is None:
        return None
    s = str(bearing_year).strip().upper()
    if s in BEARING_YIELD_MULTIPLIER:
        return s
    return "UNKNOWN"


# --------------------------------------------------------------------------- #
# Model bundle
# --------------------------------------------------------------------------- #
class _Bundle:
    """Wraps a loaded pickle payload + the on-disk path."""

    __slots__ = ("payload", "path", "version")

    def __init__(self, payload: dict, path: Path, version: str) -> None:
        self.payload = payload
        self.path = path
        self.version = version

    # Convenience -------------------------------------------------------------
    @property
    def features(self) -> list[str]:
        return list(self.payload.get("features", []))

    @property
    def model_kind(self) -> str:
        return str(self.payload.get("model_kind", "unknown"))

    @property
    def model(self):  # noqa: ANN201
        return self.payload.get("model")

    @property
    def residual_std(self) -> float:
        return float(self.payload.get("residual_std", 0.0))

    @property
    def mean_price(self) -> float:
        return float(self.payload.get("mean_price", 0.0))

    @property
    def feature_medians(self) -> dict[str, float]:
        return dict(self.payload.get("feature_medians", {}))

    @property
    def last_train_log_price(self) -> float:
        return float(self.payload.get("last_train_log_price", 0.0))


# --------------------------------------------------------------------------- #
# Predictor
# --------------------------------------------------------------------------- #
class PricePredictor:
    """Loads model bundles and produces multi-day price forecasts.

    The predictor is intentionally tolerant: if the v2 pickle is missing it
    silently falls back to v1; if both are missing for a commodity, predict()
    raises FileNotFoundError with a clear message.
    """

    def __init__(
        self,
        models_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.models_dir = Path(models_dir) if models_dir else MODELS_DIR
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._cache: dict[str, _Bundle] = {}

    # ---------------------------------------------------------------------- #
    # Bundle loading
    # ---------------------------------------------------------------------- #
    def _bundle_for(
        self, commodity: str, variety: Optional[str] = None
    ) -> _Bundle:
        # Mango is variety-routed: one pickle per variety, no v1/v2 versioning.
        if commodity == "Mango":
            return self._mango_bundle(variety)

        if commodity in self._cache:
            return self._cache[commodity]

        slug = _commodity_slug(commodity)
        v3_path = self.models_dir / f"arima_{slug}_v3.pkl"
        v2_path = self.models_dir / f"arima_{slug}_v2.pkl"
        v1_path = self.models_dir / f"arima_{slug}_v1.pkl"

        # Prefer v3, then v2, then v1. v3 was introduced with the
        # outlier-flag + tuned-RF + commodity-specific feature work; when
        # missing we silently fall back so older deploys keep functioning.
        for path, version in ((v3_path, "v3"), (v2_path, "v2"), (v1_path, "v1")):
            if not path.exists():
                if version == "v3":
                    logger.info(
                        "v3 pickle missing for %s at %s - will try v2 fallback",
                        commodity,
                        path,
                    )
                elif version == "v2":
                    logger.warning(
                        "v2 pickle missing for %s at %s - will try v1 fallback",
                        commodity,
                        path,
                    )
                continue
            try:
                payload = joblib.load(path)
                bundle = _Bundle(payload, path, version)
                self._cache[commodity] = bundle
                if version == "v1":
                    logger.warning(
                        "Loaded v1 fallback for %s from %s (v2/v3 unavailable)",
                        commodity,
                        path,
                    )
                elif version == "v2":
                    logger.warning(
                        "Loaded v2 fallback for %s from %s (v3 unavailable, "
                        "kind=%s, mape=%.2f)",
                        commodity,
                        path,
                        payload.get("model_kind", "?"),
                        float(payload.get("mape", float("nan"))),
                    )
                else:
                    logger.info(
                        "Loaded v3 model for %s from %s (kind=%s, mape=%.2f)",
                        commodity,
                        path,
                        payload.get("model_kind", "?"),
                        float(payload.get("mape", float("nan"))),
                    )
                return bundle
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to load %s for %s: %s - trying next fallback",
                    path,
                    commodity,
                    exc,
                )
                continue

        raise FileNotFoundError(
            f"No price model pickle found for commodity={commodity} "
            f"in {self.models_dir}. Run scripts/train_price_model.py."
        )

    def _mango_bundle(self, variety: Optional[str]) -> _Bundle:
        """Resolve the per-variety mango pickle (or its default fallback).

        File naming convention (produced by scripts/train_mango_models.py):
            arima_mango_<variety_slug>.pkl

        Falls back to arima_mango_default.pkl if the requested variety pickle
        is missing. Raises FileNotFoundError with a clear message if neither
        exists.
        """

        slug = _normalise_variety(variety) or "default"
        cache_key = f"Mango::{slug}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        primary_path = self.models_dir / f"arima_mango_{slug}.pkl"
        default_path = self.models_dir / "arima_mango_default.pkl"

        attempts: list[tuple[Path, str]] = [(primary_path, slug)]
        if slug != "default":
            attempts.append((default_path, "default"))

        last_error: Optional[Exception] = None
        for path, kind_label in attempts:
            if not path.exists():
                if kind_label == slug and slug != "default":
                    logger.warning(
                        "Mango variety pickle missing (%s) - will try default fallback",
                        path,
                    )
                continue
            try:
                payload = joblib.load(path)
                # All mango pickles are tagged version='v2'.
                version = str(payload.get("version", "v2"))
                bundle = _Bundle(payload, path, version)
                self._cache[cache_key] = bundle
                if kind_label != slug:
                    logger.warning(
                        "Loaded default mango model for variety=%s (no %s pickle)",
                        variety,
                        slug,
                    )
                else:
                    logger.info(
                        "Loaded mango model %s from %s (kind=%s, mape=%.2f)",
                        slug,
                        path,
                        payload.get("model_kind", "?"),
                        float(payload.get("mape", float("nan"))),
                    )
                return bundle
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "Failed to load mango pickle %s: %s - trying next fallback",
                    path,
                    exc,
                )

        raise FileNotFoundError(
            f"No mango price model pickle found for variety={variety!r} "
            f"(tried {primary_path.name}"
            + (f" and {default_path.name}" if slug != "default" else "")
            + f") in {self.models_dir}. Run scripts/train_mango_models.py."
        )

    def reset_cache(self) -> None:
        """Drop cached bundles (useful for tests that rename pickles)."""

        self._cache.clear()

    # ---------------------------------------------------------------------- #
    # Belt-volume auto-forecast
    # ---------------------------------------------------------------------- #
    def _auto_belt_volume_forecast(
        self, commodity: str, n_steps: int = 2
    ) -> list[float]:
        """Pull the latest two belt-volume rows from amed_belt_data; otherwise
        fall back to the bundle's seasonal median.
        """

        crop_filter = "Grapes" if commodity == "Dry_Grapes" else commodity
        if self.db_path and self.db_path.exists():
            try:
                with sqlite3.connect(str(self.db_path)) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        SELECT estimated_volume_mt
                        FROM amed_belt_data
                        WHERE crop_type = ?
                        ORDER BY fetch_date DESC
                        LIMIT ?
                        """,
                        (crop_filter, n_steps),
                    )
                    rows = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
                if len(rows) >= 1:
                    if len(rows) < n_steps:
                        rows = (rows + rows)[:n_steps]
                    return rows[:n_steps]
            except sqlite3.Error as exc:
                logger.warning(
                    "amed_belt_data lookup failed (%s) - falling back to seasonal mean",
                    exc,
                )

        # Seasonal-mean fallback.
        bundle = self._bundle_for(commodity)
        seasonal_mean = float(
            bundle.feature_medians.get("amed_belt_volume_mt", bundle.mean_price or 200.0)
        )
        return [seasonal_mean] * n_steps

    # ---------------------------------------------------------------------- #
    # Supply pressure
    # ---------------------------------------------------------------------- #
    @staticmethod
    def _supply_pressure(belt_volume: float, historical_median: float) -> str:
        if historical_median <= 0 or not np.isfinite(historical_median):
            return "medium"
        ratio = belt_volume / historical_median
        if ratio > 1.3:
            return "high"
        if ratio < 0.7:
            return "low"
        return "medium"

    # ---------------------------------------------------------------------- #
    # Feature vector construction
    # ---------------------------------------------------------------------- #
    def _build_feature_row(
        self,
        bundle: _Bundle,
        current_features: dict,
        belt_volume_forecast: list[float],
        correspondent_arrivals: Optional[float],
        step_idx: int,
    ) -> np.ndarray:
        """Build a single-step exogenous feature row aligned with bundle.features."""

        feats = bundle.features
        medians = bundle.feature_medians
        row: list[float] = []
        for f in feats:
            if f == "amed_belt_volume_mt":
                val = belt_volume_forecast[0] if belt_volume_forecast else current_features.get(f)
            elif f == "amed_belt_volume_mt_lag1":
                val = (
                    belt_volume_forecast[0]
                    if belt_volume_forecast
                    else current_features.get(f, current_features.get("amed_belt_volume_mt"))
                )
            elif f == "amed_belt_volume_mt_lag2":
                if belt_volume_forecast and len(belt_volume_forecast) >= 2:
                    val = belt_volume_forecast[1]
                else:
                    val = current_features.get(
                        f, current_features.get("amed_belt_volume_mt")
                    )
            elif f == "arrivals_lag_1" and correspondent_arrivals is not None:
                val = correspondent_arrivals
            elif f == "arrivals_7day_avg" and correspondent_arrivals is not None:
                val = correspondent_arrivals
            else:
                val = current_features.get(f, np.nan)

            if val is None or (isinstance(val, float) and not np.isfinite(val)):
                val = medians.get(f, 0.0)
            try:
                row.append(float(val))
            except (TypeError, ValueError):
                row.append(float(medians.get(f, 0.0)))
        return np.asarray(row, dtype=float).reshape(1, -1)

    # ---------------------------------------------------------------------- #
    # Forecasting backends
    # ---------------------------------------------------------------------- #
    def _forecast_arimax(
        self, bundle: _Bundle, exog_matrix: np.ndarray, n_steps: int
    ) -> np.ndarray:
        model = bundle.model
        if model is None:
            return self._forecast_log_naive(bundle, n_steps)
        try:
            forecast_log = model.forecast(steps=n_steps, exog=exog_matrix)
            forecast_log = np.asarray(forecast_log, dtype=float)
            preds = np.exp(forecast_log)
            if not np.all(np.isfinite(preds)) or np.any(preds <= 0):
                raise ValueError("ARIMAX produced non-finite predictions")
            return preds
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ARIMAX forecast failed (%s) - using log-naive fallback", exc
            )
            return self._forecast_log_naive(bundle, n_steps)

    @staticmethod
    def _forecast_log_naive(bundle: _Bundle, n_steps: int) -> np.ndarray:
        base = float(np.exp(bundle.last_train_log_price)) if bundle.last_train_log_price else (
            bundle.mean_price or 100.0
        )
        return np.full(n_steps, base, dtype=float)

    def _forecast_random_forest(
        self, bundle: _Bundle, exog_matrix: np.ndarray
    ) -> np.ndarray:
        model = bundle.model
        if model is None:
            return self._forecast_log_naive(bundle, exog_matrix.shape[0])
        try:
            preds = np.asarray(model.predict(exog_matrix), dtype=float)
            if not np.all(np.isfinite(preds)) or np.any(preds <= 0):
                raise ValueError("RF produced non-finite predictions")
            return preds
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RandomForest forecast failed (%s) - using log-naive fallback", exc
            )
            return self._forecast_log_naive(bundle, exog_matrix.shape[0])

    # ---------------------------------------------------------------------- #
    # Confidence
    # ---------------------------------------------------------------------- #
    @staticmethod
    def _confidence(residual_std: float, mean_price: float) -> float:
        if mean_price <= 0 or not np.isfinite(mean_price):
            mean_price = 100.0
        conf = 1.0 / (1.0 + max(residual_std, 0.0) / mean_price)
        return float(np.clip(conf, 0.30, 0.95))

    # ---------------------------------------------------------------------- #
    # Public entry point
    # ---------------------------------------------------------------------- #
    def predict_price(
        self,
        commodity: str,
        current_features: dict,
        belt_volume_forecast: Optional[Iterable[float]] = None,
        correspondent_arrivals: Optional[float] = None,
        horizon_days: int = 3,
        variety: Optional[str] = None,
        bearing_year: Optional[str] = None,
    ) -> dict:
        is_mango = commodity == "Mango"
        bundle = self._bundle_for(commodity, variety=variety) if is_mango else self._bundle_for(commodity)

        if is_mango:
            # Mango models don't take belt-volume exog; skip the AMED lookup
            # entirely so we don't pollute the response with grape-only fields.
            belt_volume_forecast = []
        elif belt_volume_forecast is None:
            belt_volume_forecast = self._auto_belt_volume_forecast(
                commodity, n_steps=max(2, horizon_days)
            )
        else:
            belt_volume_forecast = [float(x) for x in belt_volume_forecast]

        # Ensure forecast length matches horizon (grapes/pom only).
        if not is_mango:
            while len(belt_volume_forecast) < horizon_days:
                belt_volume_forecast.append(
                    belt_volume_forecast[-1] if belt_volume_forecast else 0.0
                )

        exog_rows = []
        for step in range(horizon_days):
            row = self._build_feature_row(
                bundle,
                current_features=current_features,
                belt_volume_forecast=belt_volume_forecast[step:] if belt_volume_forecast else [],
                correspondent_arrivals=correspondent_arrivals,
                step_idx=step,
            )
            exog_rows.append(row)
        exog_matrix = np.vstack(exog_rows)

        arimax_kinds = {"arimax_v1", "arimax_v2", "arimax_common", "arimax_full"}
        if bundle.model_kind == "random_forest":
            preds = self._forecast_random_forest(bundle, exog_matrix)
        elif bundle.model_kind in arimax_kinds:
            preds = self._forecast_arimax(bundle, exog_matrix, horizon_days)
        else:
            preds = self._forecast_log_naive(bundle, horizon_days)

        confidence = self._confidence(bundle.residual_std, bundle.mean_price)

        response: dict = {
            "predictions": [round(float(p), 2) for p in preds.tolist()],
            "confidence": round(confidence, 4),
            "model_version": bundle.version,
            "model_kind": bundle.model_kind,
            "mape_train": round(float(bundle.payload.get("mape", 0.0)), 4),
        }

        if is_mango:
            # Bearing year is advisory only - it shifts expected supply
            # volume, not the predicted per-kg price (SDD Section 2.2).
            norm_bearing = _normalise_bearing(bearing_year) or "UNKNOWN"
            response["variety"] = bundle.payload.get(
                "variety", variety or "default"
            )
            response["bearing_year"] = norm_bearing
            response["bearing_price_adjust"] = float(
                BEARING_YIELD_MULTIPLIER[norm_bearing]
            )
            # Mango supply pressure: derived from the bearing flag - OFF year
            # means tight supply (high pressure), ON year means relaxed supply.
            response["supply_pressure"] = (
                "high" if norm_bearing == "OFF"
                else ("low" if norm_bearing == "ON" else "medium")
            )
        else:
            # Supply pressure - compare current (lag-1) belt vol with the
            # historical median baked into the bundle.
            median_belt = float(
                bundle.feature_medians.get("amed_belt_volume_mt", 0.0)
            )
            current_belt = (
                belt_volume_forecast[0]
                if belt_volume_forecast
                else float(current_features.get("amed_belt_volume_mt", 0.0))
            )
            response["supply_pressure"] = self._supply_pressure(
                current_belt, median_belt
            )
            response["belt_volume_used"] = [
                round(float(b), 2) for b in belt_volume_forecast[:horizon_days]
            ]

        # ------------------------------------------------------------------
        # Disruption detection: if day-1 prediction diverges from the most
        # recent known price by more than DISRUPTION_THRESHOLD_PCT (15%) we
        # suspend the standard signal, downgrade confidence, and surface a
        # trader-facing message asking them to monitor daily.
        # ------------------------------------------------------------------
        current_price = current_features.get("price_lag_1")
        try:
            current_price = float(current_price) if current_price is not None else float(preds[0])
        except (TypeError, ValueError):
            current_price = float(preds[0])
        predicted_day1 = float(preds[0])
        if current_price > 1e-6:
            delta_pct = (predicted_day1 - current_price) / current_price
        else:
            delta_pct = 0.0

        if abs(delta_pct) > DISRUPTION_THRESHOLD_PCT:
            response["unusual_market_conditions"] = True
            response["disruption_delta_pct"] = round(delta_pct * 100, 2)
            response["confidence_band"] = "LOW"
            # Downgrade the numeric confidence so any downstream caller that
            # only looks at the float still sees the degradation.
            response["confidence"] = round(min(confidence, 0.35), 4)
            response["suggested_signal"] = "HOLD"
            response["disruption_message"] = DISRUPTION_MESSAGE
        else:
            response["unusual_market_conditions"] = False
            response["disruption_delta_pct"] = round(delta_pct * 100, 2)
            response["confidence_band"] = (
                "HIGH" if confidence >= 0.75 else
                "MEDIUM" if confidence >= 0.5 else
                "LOW"
            )

        return response


DISRUPTION_THRESHOLD_PCT = 0.15
DISRUPTION_MESSAGE = (
    "Unusual market conditions detected. Standard forecast suspended. "
    "Monitor daily."
)


__all__ = [
    "PricePredictor",
    "DISRUPTION_THRESHOLD_PCT",
    "DISRUPTION_MESSAGE",
]
