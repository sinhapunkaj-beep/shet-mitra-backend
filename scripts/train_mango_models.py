"""
scripts/train_mango_models.py
ShetMitra - Mango Agent 3 - Per-Variety Price Model Training

Per SDD Section 5: trains 5 separate log-ARIMAX models, one per Mango variety
(Alphonso, Kesar, Dasheri, Totapuri, Banganapalli). For each variety three
candidate models are fit and compared on MAPE:

  Model A: log-ARIMAX(1,1,1) with common features only.
  Model B: log-ARIMAX(1,1,1) with common features + variety-specific extras
           (Alphonso adds usd_inr_rate / export_season_flag / ratnagiri_only_flag).
  Model C: RandomForestRegressor(n_estimators=300, random_state=42).

Selection rule (per SDD Section 5.4):
  - Default to the best ARIMAX (B if it improves over A by >= 0.5 MAPE points,
    otherwise A).
  - RandomForest only wins if it beats the best ARIMAX by >= 3 MAPE points.
  - If every ARIMAX fit fails, fall back to log-naive.

Outputs (under data/models/):
  - arima_mango_alphonso.pkl
  - arima_mango_kesar.pkl
  - arima_mango_dasheri.pkl
  - arima_mango_totapuri.pkl
  - arima_mango_banganapalli.pkl
  - mango_training_report.json

Target MAPE per SDD Section 5.3:
  Alphonso < 22, Kesar < 20, Dasheri < 18, Totapuri < 15, Banganapalli < 18.

Data source preference:
  data/price_history_mango_synthetic.csv (joined with forex_rates_synthetic.csv
  for Alphonso) - produced upstream by Mango Agent 2.

If the upstream synthetic CSVs are absent at training time, a smaller inline
synthetic dataset is generated so the script still produces pickles.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

warnings.filterwarnings("ignore")
os.environ.setdefault("PYTHONWARNINGS", "ignore")

from statsmodels.tsa.arima.model import ARIMA  # noqa: E402

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = DATA_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

PRICE_CSV = DATA_DIR / "price_history_mango_synthetic.csv"
FOREX_CSV = DATA_DIR / "forex_rates_synthetic.csv"
REPORT_PATH = MODELS_DIR / "mango_training_report.json"

# Jharkhand belt (SDD §3.4)
PRICE_CSV_JHARKHAND = DATA_DIR / "price_history_jharkhand_mango.csv"
REPORT_PATH_JHARKHAND = MODELS_DIR / "mango_training_report_jharkhand.json"

JHARKHAND_VARIETIES = ("Mallika", "Jardalu", "Amrapali")

TARGET_MAPE_JHARKHAND = {
    "Mallika": 18.0,
    "Jardalu": 20.0,     # lumpy GI premium
    "Amrapali": 16.0,
}

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("train_mango_models")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
RANDOM_STATE = 42

VARIETIES = ("Alphonso", "Kesar", "Dasheri", "Totapuri", "Banganapalli")

TARGET_MAPE = {
    "Alphonso": 22.0,
    "Kesar": 20.0,
    "Dasheri": 18.0,
    "Totapuri": 15.0,
    "Banganapalli": 18.0,
}

COMMON_FEATURES = [
    "price_lag_1",
    "price_lag_7",
    "price_lag_14",
    "arrivals_lag_1",
    "arrivals_7day_avg",
    "season_week",
    "month",
    "year",
    "price_yoy",
    "arrivals_yoy",
    "bearing_year_flag",
    "flowering_weather_score",
]

ALPHONSO_EXTRA_FEATURES = [
    "usd_inr_rate",
    "export_season_flag",
    "ratnagiri_only_flag",
]

# Profiles used by the inline synthetic fallback. Captures the relative
# price-level and seasonal peak per variety so the pickles are still useful
# when upstream CSVs are missing.
VARIETY_PROFILES: dict[str, dict] = {
    "Alphonso": {
        "base_price": 320.0,
        "season_amp": 110.0,
        "noise": 22.0,
        "peak_week": 18,  # mid-May (Konkan window)
        "arrivals_base": 35.0,
        "arrivals_amp": 28.0,
        "off_year_factor": 0.55,
        "bearing_cycle": {2015: "OFF", 2016: "ON", 2017: "OFF", 2018: "ON",
                          2019: "OFF", 2020: "ON", 2021: "OFF", 2022: "ON",
                          2023: "OFF", 2024: "ON", 2025: "OFF", 2026: "ON"},
        "is_alphonso": True,
    },
    "Kesar": {
        "base_price": 145.0,
        "season_amp": 55.0,
        "noise": 12.0,
        "peak_week": 22,  # late May - early June
        "arrivals_base": 90.0,
        "arrivals_amp": 60.0,
        "off_year_factor": 0.70,
        "bearing_cycle": {y: "ON" if y % 2 == 0 else "OFF" for y in range(2015, 2027)},
        "is_alphonso": False,
    },
    "Dasheri": {
        "base_price": 75.0,
        "season_amp": 30.0,
        "noise": 7.0,
        "peak_week": 25,  # mid-June (North India window)
        "arrivals_base": 150.0,
        "arrivals_amp": 95.0,
        "off_year_factor": 0.80,
        "bearing_cycle": {y: "ON" if y % 2 == 1 else "OFF" for y in range(2015, 2027)},
        "is_alphonso": False,
    },
    "Totapuri": {
        "base_price": 35.0,
        "season_amp": 12.0,
        "noise": 4.0,
        "peak_week": 24,
        "arrivals_base": 320.0,
        "arrivals_amp": 180.0,
        "off_year_factor": 0.88,  # mostly even-yielding pulp variety
        "bearing_cycle": {y: "ON" for y in range(2015, 2027)},
        "is_alphonso": False,
    },
    "Banganapalli": {
        "base_price": 85.0,
        "season_amp": 32.0,
        "noise": 8.0,
        "peak_week": 21,  # mid-May
        "arrivals_base": 180.0,
        "arrivals_amp": 110.0,
        "off_year_factor": 0.75,
        "bearing_cycle": {y: "ON" if y % 2 == 0 else "OFF" for y in range(2015, 2027)},
        "is_alphonso": False,
    },
}


# --------------------------------------------------------------------------- #
# Inline synthetic data builders
# --------------------------------------------------------------------------- #
def _build_inline_forex(weeks: pd.DatetimeIndex) -> pd.DataFrame:
    """Generate a weekly USD/INR proxy series (2015 -> 2026)."""

    rng = np.random.default_rng(RANDOM_STATE + 7)
    base = 65.0
    rates = []
    val = base
    for w in weeks:
        # Slow drift upward (rupee depreciation) + AR(1) noise.
        drift = (w.year - 2015) * 0.55
        shock = rng.normal(0, 0.35)
        val = 0.92 * val + 0.08 * (base + drift) + shock
        rates.append({"date": w.normalize(), "usd_inr_rate": round(val, 3)})
    return pd.DataFrame(rates)


def _build_inline_mango(weeks: pd.DatetimeIndex) -> pd.DataFrame:
    """Generate a synthetic weekly mango price/arrival series for all varieties.

    Each variety follows its profile (base price, seasonal amplitude,
    bearing-year cycle). Alphonso additionally couples to USD/INR through
    higher in-season prices.
    """

    rng = np.random.default_rng(RANDOM_STATE)
    rows = []
    for variety, p in VARIETY_PROFILES.items():
        for w in weeks:
            iso_week = int(w.isocalendar().week)
            year = w.year
            month = w.month
            phase = 2 * np.pi * ((iso_week - p["peak_week"]) / 52.0)
            season = p["season_amp"] * np.cos(phase)
            bearing = p["bearing_cycle"].get(year, "UNKNOWN")
            bearing_factor = (
                1.0
                if bearing == "OFF"  # OFF year -> low supply -> high price
                else (p["off_year_factor"] if bearing == "ON" else 0.85)
            )
            arrivals_supply_factor = (
                p["off_year_factor"] if bearing == "OFF" else 1.0
            )
            flowering_weather_score = float(
                np.clip(70 + rng.normal(0, 12), 20, 100)
            )
            # Flowering weather influences arrivals + price softness.
            weather_pressure = -0.25 * (flowering_weather_score - 70)
            noise = rng.normal(0, p["noise"])
            price = max(
                10.0,
                (p["base_price"] + season) * bearing_factor + weather_pressure + noise,
            )
            arrivals = max(
                2.0,
                (p["arrivals_base"] + p["arrivals_amp"] * np.cos(phase + 0.3))
                * arrivals_supply_factor
                + rng.normal(0, 12),
            )
            # In-season indicator helps the export_season flag.
            in_season = abs(iso_week - p["peak_week"]) <= 6
            mandi_name = "Ratnagiri APMC" if (
                variety == "Alphonso" and in_season and rng.random() < 0.55
            ) else "Mumbai APMC"

            rows.append(
                {
                    "date": w.normalize(),
                    "variety": variety,
                    "mandi_name": mandi_name,
                    "price_modal_kg": round(price, 2),
                    "arrivals_qty": round(arrivals, 2),
                    "bearing_year": bearing,
                    "flowering_weather_score": round(flowering_weather_score, 2),
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_dataset() -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Load mango price history and forex rates, building synthetic ones if needed."""

    if PRICE_CSV.exists():
        price = pd.read_csv(PRICE_CSV)
        source = "upstream_csv"
        logger.info("Loaded upstream mango CSV: %s rows=%d", PRICE_CSV, len(price))
    else:
        logger.warning(
            "Upstream mango CSV missing (%s) - building inline synthetic dataset.",
            PRICE_CSV,
        )
        weeks = pd.date_range("2015-01-04", "2026-04-26", freq="W-SUN")
        price = _build_inline_mango(weeks)
        source = "inline_synthetic"

    if FOREX_CSV.exists():
        forex = pd.read_csv(FOREX_CSV)
        logger.info("Loaded forex CSV: %s rows=%d", FOREX_CSV, len(forex))
    else:
        weeks = pd.date_range("2015-01-04", "2026-04-26", freq="W-SUN")
        forex = _build_inline_forex(weeks)
        logger.warning("Forex CSV missing - generated inline (rows=%d).", len(forex))

    # Normalise date columns.
    for df in (price, forex):
        date_col = None
        for cand in ("date", "rate_date", "week_start", "arrival_date"):
            if cand in df.columns:
                date_col = cand
                break
        if date_col is None:
            raise ValueError("price/forex frame missing a recognisable date column")
        df["date"] = pd.to_datetime(df[date_col], errors="coerce")

    # Variety field is required on price data.
    if "variety" not in price.columns:
        raise ValueError("price_history_mango_synthetic.csv must contain a 'variety' column")

    # Optional fields safely defaulted.
    if "mandi_name" not in price.columns:
        price["mandi_name"] = "Unknown APMC"
    if "bearing_year" not in price.columns:
        price["bearing_year"] = "UNKNOWN"
    if "flowering_weather_score" not in price.columns:
        price["flowering_weather_score"] = 70.0

    # Rename FIRST so we don't double-create arrivals_qty (was a bug:
    # the NaN seed was added before the rename, leaving the DataFrame
    # with two columns named arrivals_qty and triggering a
    # "Columns must be same length as key" downstream).
    rename_map = {
        "modal_price_per_kg": "price_modal_kg",
        "modal_price_kg": "price_modal_kg",
        "arrivals_quantity": "arrivals_qty",
        "arrivals_tonnes": "arrivals_qty",
        "arrivals_mt": "arrivals_qty",
    }
    price = price.rename(
        columns={k: v for k, v in rename_map.items() if k in price.columns}
    )

    if "arrivals_qty" not in price.columns:
        price["arrivals_qty"] = np.nan

    return price, forex, source


# --------------------------------------------------------------------------- #
# Jharkhand belt dataset loader (SDD §3.4)
# --------------------------------------------------------------------------- #
def load_jharkhand_dataset() -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Load the Jharkhand-belt mango CSV produced by Mango Agent 2.

    If the upstream CSV is missing, build it inline by delegating to the
    importer's synthetic generator so the trainer always has data.
    """
    if PRICE_CSV_JHARKHAND.exists():
        price = pd.read_csv(PRICE_CSV_JHARKHAND)
        source = "upstream_csv_jharkhand"
        logger.info(
            "Loaded Jharkhand mango CSV: %s rows=%d",
            PRICE_CSV_JHARKHAND, len(price),
        )
    else:
        logger.warning(
            "Jharkhand mango CSV missing (%s) - building inline synthetic dataset.",
            PRICE_CSV_JHARKHAND,
        )
        # Import lazily to avoid circular import cost at module-load time.
        from scripts.import_mango_market_data import (
            build_jharkhand_synthetic_dataframe,
        )
        price = build_jharkhand_synthetic_dataframe(2015, 2026, seed=RANDOM_STATE)
        source = "inline_synthetic_jharkhand"

    # Forex frame - reuse Maharashtra path's helper if the file already exists,
    # else build inline. (Jharkhand training doesn't actually use forex but
    # `engineer_features` expects a frame with a date column.)
    if FOREX_CSV.exists():
        forex = pd.read_csv(FOREX_CSV)
    else:
        weeks = pd.date_range("2015-01-04", "2026-12-27", freq="W-SUN")
        forex = _build_inline_forex(weeks)

    # Date column normalisation.
    for df in (price, forex):
        date_col = None
        for cand in ("date", "rate_date", "week_start", "arrival_date"):
            if cand in df.columns:
                date_col = cand
                break
        if date_col is None:
            raise ValueError(
                "Jharkhand price/forex frame missing a recognisable date column"
            )
        df["date"] = pd.to_datetime(df[date_col], errors="coerce")

    if "variety" not in price.columns:
        raise ValueError(
            "price_history_jharkhand_mango.csv must contain a 'variety' column"
        )

    # Rename arrivals to the trainer's canonical name.
    rename_map = {
        "arrivals_mt": "arrivals_qty",
        "arrivals_tonnes": "arrivals_qty",
        "modal_price_per_kg": "price_modal_kg",
        "modal_price_kg": "price_modal_kg",
    }
    price = price.rename(
        columns={k: v for k, v in rename_map.items() if k in price.columns}
    )

    if "arrivals_qty" not in price.columns:
        price["arrivals_qty"] = np.nan
    if "mandi_name" not in price.columns:
        price["mandi_name"] = "Unknown APMC"
    # Importer emits bearing_year_flag (0/1/-1) directly; map back to ON/OFF
    # so `engineer_features` can use its existing _bearing_to_flag mapping.
    if "bearing_year" not in price.columns:
        if "bearing_year_flag" in price.columns:
            def _flag_to_bearing(v):
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    return "UNKNOWN"
                if iv == 1:
                    return "ON"
                if iv == 0:
                    return "OFF"
                return "UNKNOWN"
            price["bearing_year"] = price["bearing_year_flag"].map(_flag_to_bearing)
        else:
            price["bearing_year"] = "UNKNOWN"
    if "flowering_weather_score" not in price.columns:
        price["flowering_weather_score"] = 70.0

    return price, forex, source


def train_jharkhand_variety(
    price_df: pd.DataFrame, forex_df: pd.DataFrame, variety: str
) -> dict:
    """Train one Jharkhand variety. Writes ``arima_mango_<slug>_jharkhand.pkl``.

    Strategy:
      * Try log-ARIMAX(1,1,1) with COMMON_FEATURES.
      * If it fails to converge or yields a non-finite forecast, fall back
        to the log-naive forecaster (SDD §3.4: "if any model fails to
        converge ... fall back to log-naive and report MAPE for that").
    """
    df_v = price_df[price_df["variety"] == variety].copy()
    if df_v.empty:
        raise RuntimeError(f"No rows for variety={variety} (Jharkhand belt)")

    df_v = engineer_features(df_v, forex_df, variety)

    train_mask = (df_v["date"] >= "2015-01-01") & (df_v["date"] <= "2023-12-31")
    test_mask = (df_v["date"] >= "2024-01-01") & (df_v["date"] <= "2026-12-31")
    train = (
        df_v[train_mask]
        .dropna(subset=["price_modal_kg"] + COMMON_FEATURES)
        .reset_index(drop=True)
    )
    test = (
        df_v[test_mask]
        .dropna(subset=["price_modal_kg"] + COMMON_FEATURES)
        .reset_index(drop=True)
    )
    if len(train) == 0 or len(test) == 0:
        cut = max(1, int(len(df_v) * 0.8))
        train = df_v.iloc[:cut].dropna(subset=["price_modal_kg"]).reset_index(drop=True)
        test = df_v.iloc[cut:].dropna(subset=["price_modal_kg"]).reset_index(drop=True)

    logger.info(
        "[JH/%s] train_rows=%d test_rows=%d",
        variety, len(train), len(test),
    )

    mean_price = float(train["price_modal_kg"].mean()) if len(train) else 0.0

    fitted, pred, residual_std, fallback = _fit_log_arimax(
        train, test, COMMON_FEATURES, label=f"JH_{variety}"
    )
    mape = _mape(test["price_modal_kg"].values, pred)
    model_kind = "log_naive" if fallback else "arimax_common"

    target = TARGET_MAPE_JHARKHAND[variety]
    target_hit = bool(mape < target)
    rationale = (
        f"Jharkhand belt: {model_kind} MAPE={mape:.2f}% "
        f"(target <{target:.1f}%, hit={'YES' if target_hit else 'NO'})."
    )
    if fallback:
        rationale += " ARIMAX failed to converge - log-naive fallback used."

    iso_now = datetime.now(timezone.utc).isoformat()
    slug = variety.lower()
    pickle_path = MODELS_DIR / f"arima_mango_{slug}_jharkhand.pkl"

    last_train_log_price = float(
        np.log(max(1e-3, train["price_modal_kg"].iloc[-1]))
    ) if len(train) else 0.0

    payload = {
        "model": None if fallback else fitted,
        "features": list(COMMON_FEATURES),
        "mape": mape,
        "variety": variety,
        "commodity": "Mango",
        "version": "v2-jharkhand",
        "model_kind": model_kind,
        "region": "JH",
        "trained_at": iso_now,
        "train_rows": len(train),
        "test_rows": len(test),
        "residual_std": residual_std,
        "mean_price": mean_price,
        "last_train_log_price": last_train_log_price,
        "feature_medians": {
            f: float(train[f].median()) if f in train.columns and len(train) else 0.0
            for f in COMMON_FEATURES
        },
        "selection_rationale": rationale,
        "target_mape": target,
        "target_hit": target_hit,
    }
    joblib.dump(payload, pickle_path)

    return {
        "variety": variety,
        "model_kind": model_kind,
        "mape": mape,
        "target_mape": target,
        "target_hit": target_hit,
        "fallback_used": fallback,
        "train_rows": len(train),
        "test_rows": len(test),
        "rationale": rationale,
        "pickle_path": str(pickle_path),
    }


# --------------------------------------------------------------------------- #
# Feature engineering
# --------------------------------------------------------------------------- #
def _bearing_to_flag(val) -> int:
    """ON -> 1, OFF -> 0, anything else -> -1."""

    if isinstance(val, str):
        s = val.strip().upper()
        if s == "ON":
            return 1
        if s == "OFF":
            return 0
    return -1


def engineer_features(
    df_variety: pd.DataFrame, forex: pd.DataFrame, variety: str
) -> pd.DataFrame:
    """Compute lag / seasonal / YoY / bearing features for one variety."""

    d = df_variety.sort_values("date").reset_index(drop=True).copy()

    d["price_lag_1"] = d["price_modal_kg"].shift(1)
    d["price_lag_7"] = d["price_modal_kg"].shift(7)
    d["price_lag_14"] = d["price_modal_kg"].shift(14)

    arrivals = d["arrivals_qty"].astype(float)
    d["arrivals_lag_1"] = arrivals.shift(1)
    d["arrivals_7day_avg"] = arrivals.rolling(7, min_periods=1).mean()

    d["month"] = d["date"].dt.month
    d["year"] = d["date"].dt.year
    d["season_week"] = d["date"].dt.isocalendar().week.astype(int)

    d["price_yoy"] = d["price_modal_kg"] / d["price_modal_kg"].shift(52) - 1.0
    d["arrivals_yoy"] = arrivals / arrivals.shift(52) - 1.0

    d["bearing_year_flag"] = d["bearing_year"].map(_bearing_to_flag).astype(float)

    d["flowering_weather_score"] = pd.to_numeric(
        d.get("flowering_weather_score"), errors="coerce"
    )

    if variety == "Alphonso":
        forex_join = forex[["date", "usd_inr_rate"]].copy()
        # Align to nearest prior weekly rate.
        d = pd.merge_asof(
            d.sort_values("date"),
            forex_join.sort_values("date"),
            on="date",
            direction="backward",
        )
        # Apr 15 - May 15 export window.
        month_day = d["date"].dt.strftime("%m-%d")
        d["export_season_flag"] = (
            (month_day >= "04-15") & (month_day <= "05-15")
        ).astype(int)
        d["ratnagiri_only_flag"] = (
            d["mandi_name"].fillna("").str.contains("Ratnagiri", case=False).astype(int)
        )

    # Impute remaining gaps with column medians (or zero if all NaN).
    cols_to_fill = list(COMMON_FEATURES)
    if variety == "Alphonso":
        cols_to_fill += ALPHONSO_EXTRA_FEATURES
    for col in cols_to_fill:
        if col not in d.columns:
            d[col] = np.nan
        if d[col].isna().any():
            med = d[col].median()
            if pd.isna(med):
                med = 0.0
            d[col] = d[col].fillna(med)

    return d


# --------------------------------------------------------------------------- #
# Modelling
# --------------------------------------------------------------------------- #
@dataclass
class CandidateResult:
    variety: str
    label: str  # 'A', 'B', or 'C'
    kind: str   # 'arimax_common', 'arimax_full', 'random_forest', 'log_naive'
    mape: float
    features: list
    predictions: list
    actuals: list
    test_dates: list
    train_rows: int
    test_rows: int
    residual_std: float
    mean_price: float
    fallback_used: bool = False


def _mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    mask = (actual != 0) & np.isfinite(actual) & np.isfinite(predicted)
    if mask.sum() == 0:
        return float("inf")
    return float(
        np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100.0
    )


def _log_naive_forecast(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, float]:
    log_price = np.log(train["price_modal_kg"].astype(float).clip(lower=1e-3).values)
    last = log_price[-1] if len(log_price) else 0.0
    if len(log_price) >= 104:
        seasonal_mean = float(np.nanmean(log_price[-52:] - log_price[-104:-52]))
    else:
        seasonal_mean = 0.0
    pred = np.exp(np.full(len(test), last + seasonal_mean, dtype=float))
    resid = train["price_modal_kg"].astype(float).values - np.exp(log_price)
    residual_std = float(np.nanstd(resid)) if resid.size else 0.0
    return pred, residual_std


def _fit_log_arimax(
    train: pd.DataFrame, test: pd.DataFrame, features: list, label: str
) -> tuple[Optional[object], np.ndarray, float, bool]:
    fallback = False
    y_train = np.log(train["price_modal_kg"].astype(float).clip(lower=1e-3))
    exog_train = train[features].astype(float).values
    exog_test = test[features].astype(float).values

    try:
        model = ARIMA(
            y_train,
            exog=exog_train,
            order=(1, 1, 1),
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fitted = model.fit(method_kwargs={"warn_convergence": False})
        forecast_log = fitted.forecast(steps=len(test), exog=exog_test)
        pred = np.exp(np.asarray(forecast_log, dtype=float))
        in_sample_log = fitted.predict(exog=exog_train)
        in_sample = np.exp(np.asarray(in_sample_log, dtype=float))
        residual_std = float(
            np.nanstd(train["price_modal_kg"].values - in_sample)
        )
        if not np.all(np.isfinite(pred)) or np.any(pred <= 0):
            raise ValueError("ARIMA produced non-finite forecast")
        return fitted, pred, residual_std, fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ARIMAX fit failed (%s): %s - falling back to log-naive", label, exc
        )
        pred, residual_std = _log_naive_forecast(train, test)
        return None, pred, residual_std, True


def _fit_random_forest(
    train: pd.DataFrame, test: pd.DataFrame, features: list
) -> tuple[RandomForestRegressor, np.ndarray, float]:
    rf = RandomForestRegressor(
        n_estimators=300,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    X_train = train[features].astype(float).values
    y_train = train["price_modal_kg"].astype(float).values
    X_test = test[features].astype(float).values
    rf.fit(X_train, y_train)
    pred = rf.predict(X_test)
    in_sample = rf.predict(X_train)
    residual_std = float(np.nanstd(y_train - in_sample))
    return rf, pred, residual_std


# --------------------------------------------------------------------------- #
# Per-variety training
# --------------------------------------------------------------------------- #
def train_variety(
    price_df: pd.DataFrame, forex_df: pd.DataFrame, variety: str
) -> dict:
    df_v = price_df[price_df["variety"] == variety].copy()
    if df_v.empty:
        raise RuntimeError(f"No rows for variety={variety}")

    df_v = engineer_features(df_v, forex_df, variety)

    train_mask = (df_v["date"] >= "2015-01-01") & (df_v["date"] <= "2023-12-31")
    test_mask = (df_v["date"] >= "2024-01-01") & (df_v["date"] <= "2026-12-31")

    train = (
        df_v[train_mask]
        .dropna(subset=["price_modal_kg"] + COMMON_FEATURES)
        .reset_index(drop=True)
    )
    test = (
        df_v[test_mask]
        .dropna(subset=["price_modal_kg"] + COMMON_FEATURES)
        .reset_index(drop=True)
    )
    if len(test) == 0 or len(train) == 0:
        cut = max(1, int(len(df_v) * 0.8))
        train = df_v.iloc[:cut].dropna(subset=["price_modal_kg"]).reset_index(drop=True)
        test = df_v.iloc[cut:].dropna(subset=["price_modal_kg"]).reset_index(drop=True)

    logger.info(
        "[%s] train_rows=%d test_rows=%d range %s..%s -> %s..%s",
        variety,
        len(train),
        len(test),
        train["date"].min() if len(train) else "n/a",
        train["date"].max() if len(train) else "n/a",
        test["date"].min() if len(test) else "n/a",
        test["date"].max() if len(test) else "n/a",
    )

    mean_price = float(train["price_modal_kg"].mean()) if len(train) else 0.0
    full_features = COMMON_FEATURES + (
        ALPHONSO_EXTRA_FEATURES if variety == "Alphonso" else []
    )

    # --- Model A: common-features ARIMAX -------------------------------------
    fitted_a, pred_a, resid_a, fb_a = _fit_log_arimax(
        train, test, COMMON_FEATURES, label=f"{variety}_A_common"
    )
    mape_a = _mape(test["price_modal_kg"].values, pred_a)

    # --- Model B: full-features ARIMAX ---------------------------------------
    if variety == "Alphonso":
        fitted_b, pred_b, resid_b, fb_b = _fit_log_arimax(
            train, test, full_features, label=f"{variety}_B_full"
        )
        mape_b = _mape(test["price_modal_kg"].values, pred_b)
    else:
        # Non-Alphonso varieties don't have extra exogenous features; B == A.
        fitted_b, pred_b, resid_b, fb_b, mape_b = fitted_a, pred_a, resid_a, fb_a, mape_a

    # --- Model C: Random Forest ---------------------------------------------
    rf_model, pred_rf, resid_rf = _fit_random_forest(train, test, full_features)
    mape_rf = _mape(test["price_modal_kg"].values, pred_rf)

    candidates = {
        "A": CandidateResult(
            variety=variety,
            label="A",
            kind="log_naive" if fb_a else "arimax_common",
            mape=mape_a,
            features=list(COMMON_FEATURES),
            predictions=pred_a.tolist(),
            actuals=test["price_modal_kg"].tolist(),
            test_dates=test["date"].astype(str).tolist(),
            train_rows=len(train),
            test_rows=len(test),
            residual_std=resid_a,
            mean_price=mean_price,
            fallback_used=fb_a,
        ),
        "B": CandidateResult(
            variety=variety,
            label="B",
            kind="log_naive" if fb_b else "arimax_full",
            mape=mape_b,
            features=list(full_features),
            predictions=pred_b.tolist(),
            actuals=test["price_modal_kg"].tolist(),
            test_dates=test["date"].astype(str).tolist(),
            train_rows=len(train),
            test_rows=len(test),
            residual_std=resid_b,
            mean_price=mean_price,
            fallback_used=fb_b,
        ),
        "C": CandidateResult(
            variety=variety,
            label="C",
            kind="random_forest",
            mape=mape_rf,
            features=list(full_features),
            predictions=pred_rf.tolist(),
            actuals=test["price_modal_kg"].tolist(),
            test_dates=test["date"].astype(str).tolist(),
            train_rows=len(train),
            test_rows=len(test),
            residual_std=resid_rf,
            mean_price=mean_price,
        ),
    }

    # ---------------------------------------------------------------------- #
    # Selection: best ARIMAX first, RF only if it beats by >= 3 MAPE points.
    # ---------------------------------------------------------------------- #
    if candidates["B"].mape + 0.5 <= candidates["A"].mape:
        best_arimax_label = "B"
    else:
        best_arimax_label = "A"
    best_arimax = candidates[best_arimax_label]

    if candidates["C"].mape <= best_arimax.mape - 3.0:
        selected_label = "C"
        selected_model = rf_model
        rationale = (
            f"RandomForest beat best ARIMAX ({best_arimax_label}) by "
            f"{best_arimax.mape - candidates['C'].mape:.2f} MAPE points - selecting RF."
        )
    else:
        selected_label = best_arimax_label
        selected_model = fitted_b if selected_label == "B" else fitted_a
        rationale = (
            f"Selected ARIMAX model {selected_label} "
            f"(MAPE {best_arimax.mape:.2f}%). RF MAPE {candidates['C'].mape:.2f}% "
            f"did not beat by >=3 points."
        )

    selected = candidates[selected_label]
    target = TARGET_MAPE[variety]
    target_hit = bool(selected.mape < target)
    if not target_hit:
        rationale += (
            f"  Target MAPE {target:.1f}% not reached "
            f"(actual = {selected.mape:.2f}%)."
        )

    iso_now = datetime.now(timezone.utc).isoformat()
    slug = variety.lower()
    pickle_path = MODELS_DIR / f"arima_mango_{slug}.pkl"

    if selected.kind == "log_naive" or selected_model is None:
        # No usable model fit - persist a log-naive shell that the predictor
        # can still load.
        model_payload = None
        model_kind = "log_naive"
    else:
        model_payload = selected_model
        model_kind = selected.kind

    last_train_log_price = float(
        np.log(max(1e-3, train["price_modal_kg"].iloc[-1]))
    ) if len(train) else 0.0

    payload = {
        "model": model_payload,
        "features": selected.features,
        "mape": selected.mape,
        "variety": variety,
        "commodity": "Mango",
        "version": "v2",
        "model_kind": model_kind,
        "trained_at": iso_now,
        "train_rows": len(train),
        "test_rows": len(test),
        "residual_std": selected.residual_std,
        "mean_price": mean_price,
        "last_train_log_price": last_train_log_price,
        "feature_medians": {
            f: float(train[f].median()) if f in train.columns and len(train) else 0.0
            for f in selected.features
        },
        "selection_rationale": rationale,
        "target_mape": target,
        "target_hit": target_hit,
    }
    joblib.dump(payload, pickle_path)

    return {
        "variety": variety,
        "candidates": {k: asdict(v) for k, v in candidates.items()},
        "selected_label": selected_label,
        "selected_kind": model_kind,
        "selected_mape": selected.mape,
        "rationale": rationale,
        "target_mape": target,
        "target_hit": target_hit,
        "pickle_path": str(pickle_path),
    }


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #
def _run_maharashtra() -> int:
    price_df, forex_df, source = load_dataset()
    logger.info(
        "Dataset source=%s price_rows=%d forex_rows=%d",
        source,
        len(price_df),
        len(forex_df),
    )

    full_report: dict = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "data_source": source,
        "random_state": RANDOM_STATE,
        "varieties": {},
    }
    summary_rows = []

    for variety in VARIETIES:
        try:
            result = train_variety(price_df, forex_df, variety)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Training failed for %s: %s", variety, exc)
            full_report["varieties"][variety] = {"error": str(exc)}
            continue
        full_report["varieties"][variety] = result
        summary_rows.append(
            {
                "variety": variety,
                "model_kind": result["selected_kind"],
                "mape": result["selected_mape"],
                "target": result["target_mape"],
                "target_hit": result["target_hit"],
                "selected_label": result["selected_label"],
            }
        )

    with REPORT_PATH.open("w") as fh:
        json.dump(full_report, fh, indent=2, default=str)
    logger.info("Wrote training report: %s", REPORT_PATH)

    print()
    print("=" * 88)
    print("MANGO PRICE MODEL TRAINING SUMMARY")
    print("=" * 88)
    header = (
        f"{'variety':<14} {'model_kind':>16} {'MAPE':>8} {'target':>8} {'hit':>5}"
    )
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(
            f"{row['variety']:<14} "
            f"{row['model_kind']:>16} "
            f"{row['mape']:>8.2f} "
            f"{row['target']:>8.1f} "
            f"{'YES' if row['target_hit'] else 'NO':>5}"
        )
    print("=" * 88)
    return 0


def _run_jharkhand() -> int:
    price_df, forex_df, source = load_jharkhand_dataset()
    logger.info(
        "Jharkhand belt dataset source=%s price_rows=%d forex_rows=%d",
        source,
        len(price_df),
        len(forex_df),
    )

    full_report: dict = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "data_source": source,
        "random_state": RANDOM_STATE,
        "region": "JH",
        "varieties": {},
    }
    summary_rows = []

    for variety in JHARKHAND_VARIETIES:
        try:
            result = train_jharkhand_variety(price_df, forex_df, variety)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Jharkhand training failed for %s: %s", variety, exc)
            full_report["varieties"][variety] = {"error": str(exc)}
            continue
        full_report["varieties"][variety] = result
        summary_rows.append(result)

    with REPORT_PATH_JHARKHAND.open("w") as fh:
        json.dump(full_report, fh, indent=2, default=str)
    logger.info("Wrote Jharkhand training report: %s", REPORT_PATH_JHARKHAND)

    print()
    print("=" * 88)
    print("MANGO PRICE MODEL TRAINING SUMMARY - JHARKHAND BELT")
    print("=" * 88)
    header = (
        f"{'variety':<14} {'model_kind':>16} {'MAPE':>8} {'target':>8} {'hit':>5}"
    )
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(
            f"{row['variety']:<14} "
            f"{row['model_kind']:>16} "
            f"{row['mape']:>8.2f} "
            f"{row['target_mape']:>8.1f} "
            f"{'YES' if row['target_hit'] else 'NO':>5}"
        )
    print("=" * 88)
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train mango price models. Default: Maharashtra 5-variety stack "
            "(SDD §5). With --jharkhand: 3-variety eastern belt stack (SDD §3.4)."
        ),
    )
    parser.add_argument(
        "--jharkhand",
        action="store_true",
        help=(
            "Train the Jharkhand belt models: "
            "arima_mango_{mallika,jardalu,amrapali}_jharkhand.pkl"
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.jharkhand:
        return _run_jharkhand()
    return _run_maharashtra()


if __name__ == "__main__":
    raise SystemExit(main())
