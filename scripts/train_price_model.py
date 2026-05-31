"""
scripts/train_price_model.py
ShetMitra — Agent 5 — Price Model Training Entry Point

Trains and compares:
  Model A (v1 baseline): log-ARIMAX with classical features only.
  Model B (v2):          log-ARIMAX with v1 features + AMED features.
  Model C (RF):          RandomForestRegressor sanity baseline.

Best v2 model is selected only if it beats v1 (and the RF must beat v2 by
>=3 MAPE points to override the ARIMAX choice). All artifacts go into
data/models/.

Aligned with SDD Section 5.4.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import joblib

from sklearn.ensemble import RandomForestRegressor

# Statsmodels emits a lot of convergence chatter for log-ARIMAX on small
# segments. Silence everything noisy here; we still log results ourselves.
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

PRIMARY_CSV = DATA_DIR / "price_history_training_backfilled.csv"
FALLBACK_CSV = DATA_DIR / "price_history_training_synthetic.csv"
REPORT_PATH = MODELS_DIR / "training_report.json"

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("train_price_model")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
COMMODITIES = ("Dry_Grapes", "Pomegranate")
TARGET_MAPE = {"Dry_Grapes": 25.0, "Pomegranate": 17.0}
RANDOM_STATE = 42

import argparse

V1_FEATURES = [
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
]

V2_EXTRA_FEATURES = [
    "amed_belt_volume_mt",
    "amed_belt_volume_mt_lag1",
    "amed_belt_volume_mt_lag2",
    "amed_fields_harvesting",
    "amed_health_pct_good",
    "amed_season_timing_dev",
]

# v3 adds commodity-specific seasonality + YoY lookback + outlier flag on top
# of every v2 column. See engineer_features for how each is computed.
V3_EXTRA_FEATURES = [
    "harvest_season_flag",
    "weeks_from_season_peak",
    "prev_year_same_week_price",
    "outlier_flag",
]

V2_FEATURES = V1_FEATURES + V2_EXTRA_FEATURES
V3_FEATURES = V2_FEATURES + V3_EXTRA_FEATURES

# Optional exogenous feature merged in via --include-world-production. The
# annual USDA FAS PSD world raisin production figure is the same value for
# every row in a given year, so it acts as a slow-moving year-over-year
# supply pressure signal for the ARIMAX models.
WORLD_PRODUCTION_FEATURE = "world_production_mt"

# Window the user identified as the 2025 Dry Grapes price spike (mean ~Rs194
# vs Rs124 in 2024; 4-week pct change > 50% in Feb-Apr). Used by
# --exclude-spike to keep ARIMAX from being dragged by the outlier season.
SPIKE_WINDOWS: dict[str, list[tuple[str, str]]] = {
    "Dry_Grapes": [("2025-02-01", "2025-05-31")],
}
SPIKE_PCT_THRESHOLD = 0.50  # 50% absolute 4-week pct change.
USDA_CSV = Path(__file__).resolve().parent.parent / "data" / "usda_world_raisin_production.csv"


def _apply_spike_exclusion(df: "pd.DataFrame", commodity: str) -> tuple["pd.DataFrame", int]:
    """Drop rows inside the spike window AND any rows whose 4-week pct change
    exceeds SPIKE_PCT_THRESHOLD. Returns (df, n_dropped)."""

    if "date" not in df.columns:
        return df, 0
    n0 = len(df)
    keep = pd.Series(True, index=df.index)
    for start, end in SPIKE_WINDOWS.get(commodity, []):
        win = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
        keep &= ~win
    # Programmatic detection too: anything with |4-week pct change| > threshold.
    if "price_modal_kg" in df.columns:
        pct = df["price_modal_kg"].pct_change(periods=4).abs()
        keep &= ~(pct > SPIKE_PCT_THRESHOLD)
    out = df.loc[keep].copy()
    return out, n0 - len(out)


def _join_world_production(df: "pd.DataFrame") -> "pd.DataFrame":
    """Left-join USDA world raisin production by year."""

    if not USDA_CSV.exists():
        df[WORLD_PRODUCTION_FEATURE] = np.nan
        return df
    usda = pd.read_csv(USDA_CSV)[["year", "world_production_mt"]]
    if "year" not in df.columns and "date" in df.columns:
        df["year"] = df["date"].dt.year
    return df.merge(usda, on="year", how="left")


# --------------------------------------------------------------------------- #
# Data loading and synthesis
# --------------------------------------------------------------------------- #
def _generate_inline_synthetic() -> pd.DataFrame:
    """Last-resort synthetic dataset used when no CSV is available."""

    logger.warning(
        "No backfilled or synthetic CSV found - generating inline synthetic "
        "dataset for both commodities (2015 -> Apr 2026, weekly)."
    )
    rng = np.random.default_rng(RANDOM_STATE)
    rows = []
    weeks = pd.date_range("2015-01-04", "2026-04-26", freq="W-SUN")

    profiles = {
        "Dry_Grapes": {
            "base_price": 165.0,
            "season_amp": 35.0,
            "noise": 12.0,
            "peak_week": 16,  # roughly mid-April
            "arrivals_base": 90.0,
            "arrivals_amp": 70.0,
            "belt_base": 520.0,
            "belt_amp": 320.0,
            "health_base": 78.0,
            "spike_year": 2025,
            "spike_amp": 55.0,
        },
        "Pomegranate": {
            "base_price": 95.0,
            "season_amp": 22.0,
            "noise": 8.0,
            "peak_week": 40,
            "arrivals_base": 140.0,
            "arrivals_amp": 60.0,
            "belt_base": 410.0,
            "belt_amp": 180.0,
            "health_base": 72.0,
            "spike_year": 2025,
            "spike_amp": 18.0,
        },
    }

    for commodity, p in profiles.items():
        prev_price = p["base_price"]
        for w in weeks:
            week_of_year = w.isocalendar().week
            year = w.year
            phase = 2 * np.pi * ((week_of_year - p["peak_week"]) / 52.0)
            season = p["season_amp"] * np.cos(phase)
            belt = max(
                15.0,
                p["belt_base"]
                + p["belt_amp"] * np.cos(phase + 0.4)
                + rng.normal(0, 35),
            )
            belt_pressure = -0.06 * (belt - p["belt_base"])
            spike = p["spike_amp"] if year == p["spike_year"] else 0.0
            noise = rng.normal(0, p["noise"])
            price = max(
                10.0,
                p["base_price"] + season + belt_pressure + spike + noise,
            )
            arrivals = max(
                5.0,
                p["arrivals_base"]
                + p["arrivals_amp"] * np.cos(phase + 0.2)
                + 0.08 * belt
                + rng.normal(0, 18),
            )
            health = float(
                np.clip(
                    p["health_base"] + rng.normal(0, 6) - 0.02 * max(0, belt - p["belt_base"]),
                    35,
                    98,
                )
            )
            fields_harvesting = max(0, int(belt / 12 + rng.normal(0, 4)))
            timing_dev = float(rng.normal(0, 4))

            rows.append(
                {
                    "date": w.normalize(),
                    "commodity": commodity,
                    "price_modal_kg": round(price, 2),
                    "arrivals_qty": round(arrivals, 2),
                    "amed_belt_volume_mt": round(belt, 2),
                    "amed_fields_harvesting": fields_harvesting,
                    "amed_health_pct_good": round(health, 2),
                    "amed_season_week": int(week_of_year),
                    "amed_season_timing_dev": round(timing_dev, 2),
                }
            )
            prev_price = price

    return pd.DataFrame(rows)


def load_dataset() -> tuple[pd.DataFrame, str]:
    """Load the training dataset, preferring Agent 4's backfilled output."""

    if PRIMARY_CSV.exists():
        logger.info("Loading backfilled training dataset: %s", PRIMARY_CSV)
        df = pd.read_csv(PRIMARY_CSV)
        source = "backfilled"
    elif FALLBACK_CSV.exists():
        logger.info("Loading fallback synthetic dataset: %s", FALLBACK_CSV)
        df = pd.read_csv(FALLBACK_CSV)
        source = "synthetic_csv"
    else:
        df = _generate_inline_synthetic()
        source = "inline_synthetic"

    date_col = None
    for candidate in ("date", "week_start", "arrival_date", "fetch_date"):
        if candidate in df.columns:
            date_col = candidate
            break
    if date_col is None:
        raise ValueError(
            "Dataset must include a 'date', 'week_start', 'arrival_date', or "
            "'fetch_date' column."
        )
    df["date"] = pd.to_datetime(df[date_col], errors="coerce")

    df = df.dropna(subset=["date"]).sort_values(["commodity", "date"]).reset_index(
        drop=True
    )

    # Make sure expected base columns exist even if Agent 4 named them differently.
    rename_map = {
        "modal_price_per_kg": "price_modal_kg",
        "modal_price_kg": "price_modal_kg",
        "arrivals_quantity": "arrivals_qty",
        "arrivals_tonnes": "arrivals_qty",
        "arrivals_mt": "arrivals_qty",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    required = {"commodity", "price_modal_kg"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Dataset missing required columns: {missing}")

    if "arrivals_qty" not in df.columns:
        df["arrivals_qty"] = np.nan

    # AMED columns - keep flexibility with naming.  Skip a rename if the
    # destination column already exists (avoids duplicate columns when Agent 4
    # ships both `season_week` and `amed_season_week`).
    amed_renames = {
        "amed_belt_volume_estimated_mt": "amed_belt_volume_mt",
        "belt_volume_mt": "amed_belt_volume_mt",
        "fields_harvesting": "amed_fields_harvesting",
        "health_pct_good": "amed_health_pct_good",
    }
    safe_renames = {
        k: v for k, v in amed_renames.items() if k in df.columns and v not in df.columns
    }
    if safe_renames:
        df = df.rename(columns=safe_renames)
    # De-duplicate any accidental duplicate columns (keep first).
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()].copy()

    for col in V2_EXTRA_FEATURES + ["amed_season_week"]:
        if col not in df.columns:
            df[col] = np.nan

    return df, source


# --------------------------------------------------------------------------- #
# Feature engineering
# --------------------------------------------------------------------------- #
def engineer_features(df_one_commodity: pd.DataFrame) -> pd.DataFrame:
    """Compute lag / seasonal / YoY features for a single commodity."""

    d = df_one_commodity.sort_values("date").reset_index(drop=True).copy()

    d["price_lag_1"] = d["price_modal_kg"].shift(1)
    d["price_lag_7"] = d["price_modal_kg"].shift(7)
    d["price_lag_14"] = d["price_modal_kg"].shift(14)

    arrivals = d["arrivals_qty"].astype(float)
    d["arrivals_lag_1"] = arrivals.shift(1)
    d["arrivals_7day_avg"] = arrivals.rolling(7, min_periods=1).mean()

    d["month"] = d["date"].dt.month
    d["year"] = d["date"].dt.year
    iso_week = d["date"].dt.isocalendar().week.astype(int)
    season_source = None
    if "amed_season_week" in d.columns:
        col = d["amed_season_week"]
        if isinstance(col, pd.DataFrame):
            col = col.iloc[:, 0]
        if bool(col.notna().any()):
            season_source = col.fillna(iso_week)
    if season_source is None and "season_week" in d.columns:
        col = d["season_week"]
        if isinstance(col, pd.DataFrame):
            col = col.iloc[:, 0]
        if bool(col.notna().any()):
            season_source = col.fillna(iso_week)
    d["season_week"] = season_source if season_source is not None else iso_week

    # YoY (52-week lookback, weekly cadence).
    d["price_yoy"] = d["price_modal_kg"] / d["price_modal_kg"].shift(52) - 1.0
    d["arrivals_yoy"] = arrivals / arrivals.shift(52) - 1.0

    # AMED forecast-lookahead lags. Per SDD 5.3 these are "next-week" and
    # "week-after" AMED volume forecasts; the pull script populates them as
    # shift(-1)/(-2) (FORWARD-looking). If the column came in already populated
    # we keep that value; otherwise we synthesise from amed_belt_volume_mt.
    if "amed_belt_volume_mt_lag1" not in d.columns:
        d["amed_belt_volume_mt_lag1"] = np.nan
    d["amed_belt_volume_mt_lag1"] = d["amed_belt_volume_mt_lag1"].combine_first(
        d["amed_belt_volume_mt"].shift(-1)
    )
    if "amed_belt_volume_mt_lag2" not in d.columns:
        d["amed_belt_volume_mt_lag2"] = np.nan
    d["amed_belt_volume_mt_lag2"] = d["amed_belt_volume_mt_lag2"].combine_first(
        d["amed_belt_volume_mt"].shift(-2)
    )

    # ---- Commodity-specific seasonal features (SDD recap) ----------------
    commodity_name = (
        d["commodity"].iloc[0] if "commodity" in d.columns and len(d) else ""
    )
    seasonal_config = {
        "Pomegranate": {
            "harvest_months": {8, 9, 10, 11, 12, 1, 2},
            "peak_iso_week": 46,   # mid-November
        },
        "Dry_Grapes": {
            "harvest_months": {3, 4, 5},
            "peak_iso_week": 16,   # ~ third week of April
        },
    }
    cfg = seasonal_config.get(commodity_name)
    if cfg is not None:
        d["harvest_season_flag"] = d["month"].isin(cfg["harvest_months"]).astype(int)
        # Distance in weeks from the seasonal peak, wrapped to [0, 26].
        delta = (iso_week - cfg["peak_iso_week"]).abs()
        d["weeks_from_season_peak"] = np.minimum(delta, 52 - delta)
    else:
        d["harvest_season_flag"] = 0
        d["weeks_from_season_peak"] = 0

    # Year-over-year same-week price. Pull script provides it for live rows;
    # synthesise here if absent (e.g. CSV-only test runs).
    if "prev_year_same_week_price" not in d.columns:
        d["prev_year_same_week_price"] = np.nan
    d["prev_year_same_week_price"] = d["prev_year_same_week_price"].combine_first(
        d["price_modal_kg"].shift(52)
    )

    # Outlier flag: week-over-week price change > 50% within the same mandi
    # (computing across mandis would create false positives at the boundary).
    if "mandi" in d.columns:
        wow_pct = d.groupby("mandi", group_keys=False)["price_modal_kg"].pct_change(periods=1).abs()
    else:
        wow_pct = d["price_modal_kg"].pct_change(periods=1).abs()
    d["outlier_flag"] = (wow_pct > 0.50).astype(int)

    # Sensible imputation: forward-fill seasonal context, fill remaining gaps
    # with column medians.
    for col in V1_FEATURES + V2_EXTRA_FEATURES:
        if col not in d.columns:
            d[col] = np.nan
        if d[col].isna().any():
            med = d[col].median()
            if pd.isna(med):
                med = 0.0
            d[col] = d[col].fillna(med)

    return d


# --------------------------------------------------------------------------- #
# Modeling
# --------------------------------------------------------------------------- #
@dataclass
class ModelResult:
    commodity: str
    kind: str  # 'arimax_v1', 'arimax_v2', 'random_forest', 'log_naive'
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
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100.0)


def _log_naive_forecast(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, float]:
    """Last-value + seasonal delta forecast on log price."""

    log_price = np.log(train["price_modal_kg"].astype(float).values)
    last = log_price[-1]
    if len(log_price) >= 52:
        seasonal = log_price[-52:] - log_price[-104:-52] if len(log_price) >= 104 else 0.0
        if isinstance(seasonal, np.ndarray):
            seasonal_mean = float(np.nanmean(seasonal))
        else:
            seasonal_mean = float(seasonal)
    else:
        seasonal_mean = 0.0
    pred_log = np.full(len(test), last + seasonal_mean, dtype=float)
    pred = np.exp(pred_log)
    resid = train["price_modal_kg"].astype(float).values - np.exp(
        log_price + (0.0 if len(log_price) < 2 else np.mean(np.diff(log_price)))
    )
    residual_std = float(np.nanstd(resid)) if resid.size else 0.0
    return pred, residual_std


def _fit_log_arimax(
    train: pd.DataFrame, test: pd.DataFrame, features: list, label: str
) -> tuple[Optional[object], np.ndarray, float, bool]:
    """Fit a log-ARIMAX(1,1,1) model and forecast on the test set.

    Returns (fitted_model_or_None, predictions, residual_std, fallback_used).
    """

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
        logger.warning("ARIMAX fit failed (%s): %s - falling back to log-naive", label, exc)
        fallback = True
        pred, residual_std = _log_naive_forecast(train, test)
        return None, pred, residual_std, fallback


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


def _fit_random_forest_tuned(
    train: pd.DataFrame, test: pd.DataFrame, features: list,
    *, cv_splits: int = 5,
) -> tuple[RandomForestRegressor, np.ndarray, float, dict]:
    """GridSearchCV over the SDD-prescribed RF hyperparameter grid.

    n_estimators: [100, 200, 500]
    max_depth:    [5, 10, 15, None]
    min_samples_leaf: [1, 2, 5]

    Cross-validated on the training set (2015-2023) with TimeSeriesSplit so we
    do not leak future information. Returns best estimator + test predictions
    + residual std + best-params dict.
    """

    from sklearn.model_selection import GridSearchCV, TimeSeriesSplit

    X_train = train[features].astype(float).values
    y_train = train["price_modal_kg"].astype(float).values
    X_test = test[features].astype(float).values

    grid = {
        "n_estimators": [100, 200, 500],
        "max_depth": [5, 10, 15, None],
        "min_samples_leaf": [1, 2, 5],
    }
    base = RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1)
    search = GridSearchCV(
        base, grid,
        cv=TimeSeriesSplit(n_splits=cv_splits),
        scoring="neg_mean_absolute_percentage_error",
        n_jobs=-1, refit=True,
    )
    search.fit(X_train, y_train)
    best = search.best_estimator_
    pred = best.predict(X_test)
    in_sample = best.predict(X_train)
    residual_std = float(np.nanstd(y_train - in_sample))
    return best, pred, residual_std, dict(search.best_params_)


def train_commodity(
    df_full: pd.DataFrame, commodity: str
) -> dict:
    """Train all three model variants for a single commodity."""

    df_c = df_full[df_full["commodity"] == commodity].copy()
    if df_c.empty:
        raise RuntimeError(f"No rows for commodity={commodity}")

    df_c = engineer_features(df_c)

    train_mask = (df_c["date"] >= "2015-01-01") & (df_c["date"] <= "2024-12-31")
    test_mask = (df_c["date"] >= "2025-01-01") & (df_c["date"] <= "2026-04-30")
    train = df_c[train_mask].dropna(subset=["price_modal_kg"]).reset_index(drop=True)
    test = df_c[test_mask].dropna(subset=["price_modal_kg"]).reset_index(drop=True)

    if len(test) == 0:
        # Fallback: use the last 20% of rows as test.
        cut = int(len(df_c) * 0.8)
        train = df_c.iloc[:cut].reset_index(drop=True)
        test = df_c.iloc[cut:].reset_index(drop=True)

    logger.info(
        "[%s] train_rows=%d  test_rows=%d  date range train=%s..%s test=%s..%s",
        commodity,
        len(train),
        len(test),
        train["date"].min(),
        train["date"].max(),
        test["date"].min(),
        test["date"].max(),
    )

    mean_price = float(train["price_modal_kg"].mean())

    # --- Model A: v1 ARIMAX baseline ----------------------------------------
    fitted_v1, pred_v1, resid_v1, fb_v1 = _fit_log_arimax(
        train, test, V1_FEATURES, label=f"{commodity}_v1"
    )
    mape_v1 = _mape(test["price_modal_kg"].values, pred_v1)

    # --- Model B: v2 ARIMAX with AMED features ------------------------------
    fitted_v2, pred_v2, resid_v2, fb_v2 = _fit_log_arimax(
        train, test, V2_FEATURES, label=f"{commodity}_v2"
    )
    mape_v2 = _mape(test["price_modal_kg"].values, pred_v2)

    # --- Model C: RandomForest sanity-check ---------------------------------
    rf, pred_rf, resid_rf = _fit_random_forest(train, test, V2_FEATURES)
    mape_rf = _mape(test["price_modal_kg"].values, pred_rf)

    results = {
        "v1": ModelResult(
            commodity=commodity,
            kind="log_naive" if fb_v1 else "arimax_v1",
            mape=mape_v1,
            features=V1_FEATURES,
            predictions=pred_v1.tolist(),
            actuals=test["price_modal_kg"].tolist(),
            test_dates=test["date"].astype(str).tolist(),
            train_rows=len(train),
            test_rows=len(test),
            residual_std=resid_v1,
            mean_price=mean_price,
            fallback_used=fb_v1,
        ),
        "v2": ModelResult(
            commodity=commodity,
            kind="log_naive" if fb_v2 else "arimax_v2",
            mape=mape_v2,
            features=V2_FEATURES,
            predictions=pred_v2.tolist(),
            actuals=test["price_modal_kg"].tolist(),
            test_dates=test["date"].astype(str).tolist(),
            train_rows=len(train),
            test_rows=len(test),
            residual_std=resid_v2,
            mean_price=mean_price,
            fallback_used=fb_v2,
        ),
        "rf": ModelResult(
            commodity=commodity,
            kind="random_forest",
            mape=mape_rf,
            features=V2_FEATURES,
            predictions=pred_rf.tolist(),
            actuals=test["price_modal_kg"].tolist(),
            test_dates=test["date"].astype(str).tolist(),
            train_rows=len(train),
            test_rows=len(test),
            residual_std=resid_rf,
            mean_price=mean_price,
        ),
    }

    # Decide which model becomes "v2" on disk.
    selected_kind = "v2"
    rationale = "ARIMAX v2 selected by default."
    if results["rf"].mape <= results["v2"].mape - 3.0:
        selected_kind = "rf"
        rationale = (
            f"RandomForest beat ARIMAX v2 by "
            f"{results['v2'].mape - results['rf'].mape:.2f} MAPE points - selecting RF."
        )
    elif results["v2"].mape > results["v1"].mape:
        selected_kind = "v1"
        rationale = (
            "ARIMAX v2 did not improve on v1 - selecting v1 features for v2 pickle as a "
            "safety net (no regression vs baseline)."
        )

    target_hit = results["v2"].mape < TARGET_MAPE[commodity]
    if not target_hit:
        rationale += (
            f"  Target MAPE {TARGET_MAPE[commodity]:.1f}% not reached "
            f"(actual v2 = {results['v2'].mape:.2f}%)."
        )

    # Persist artifacts.
    iso_now = datetime.now(timezone.utc).isoformat()
    suffix = "dry_grapes" if commodity == "Dry_Grapes" else "pomegranate"

    v1_payload = {
        "model": fitted_v1,
        "features": V1_FEATURES,
        "mape": results["v1"].mape,
        "commodity": commodity,
        "version": "v1",
        "model_kind": results["v1"].kind,
        "trained_at": iso_now,
        "train_rows": len(train),
        "test_rows": len(test),
        "residual_std": resid_v1,
        "mean_price": mean_price,
        "last_train_log_price": float(np.log(max(1e-3, train["price_modal_kg"].iloc[-1]))),
        "feature_medians": {f: float(train[f].median()) for f in V1_FEATURES},
    }
    joblib.dump(v1_payload, MODELS_DIR / f"arima_{suffix}_v1.pkl")

    if selected_kind == "rf":
        chosen_model = rf
        chosen_features = V2_FEATURES
        chosen_kind = "random_forest"
        chosen_mape = results["rf"].mape
        chosen_resid = resid_rf
    elif selected_kind == "v1":
        chosen_model = fitted_v1
        chosen_features = V1_FEATURES
        chosen_kind = results["v1"].kind
        chosen_mape = results["v1"].mape
        chosen_resid = resid_v1
    else:
        chosen_model = fitted_v2
        chosen_features = V2_FEATURES
        chosen_kind = results["v2"].kind
        chosen_mape = results["v2"].mape
        chosen_resid = resid_v2

    v2_payload = {
        "model": chosen_model,
        "features": chosen_features,
        "mape": chosen_mape,
        "commodity": commodity,
        "version": "v2",
        "model_kind": chosen_kind,
        "trained_at": iso_now,
        "train_rows": len(train),
        "test_rows": len(test),
        "residual_std": chosen_resid,
        "mean_price": mean_price,
        "last_train_log_price": float(
            np.log(max(1e-3, train["price_modal_kg"].iloc[-1]))
        ),
        "feature_medians": {f: float(train[f].median()) for f in chosen_features},
        "selection_rationale": rationale,
        "target_mape": TARGET_MAPE[commodity],
        "target_hit": target_hit,
    }
    joblib.dump(v2_payload, MODELS_DIR / f"arima_{suffix}_v2.pkl")

    return {
        "commodity": commodity,
        "results": {k: asdict(v) for k, v in results.items()},
        "selected": selected_kind,
        "selected_mape": chosen_mape,
        "selected_kind": chosen_kind,
        "rationale": rationale,
        "target_mape": TARGET_MAPE[commodity],
        "target_hit": target_hit,
        "v1_pickle": str(MODELS_DIR / f"arima_{suffix}_v1.pkl"),
        "v2_pickle": str(MODELS_DIR / f"arima_{suffix}_v2.pkl"),
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def train_commodity_v3(
    df_full: "pd.DataFrame", commodity: str,
    *, exclude_outliers: bool, tune_rf: bool,
) -> dict:
    """v3 trainer: V3 features (incl. commodity-specific seasonal + outlier
    flag), optional outlier exclusion, and optional GridSearchCV-tuned RF.
    Persists arima_{slug}_v3.pkl on disk.
    """

    df_c = df_full[df_full["commodity"] == commodity].copy()
    df_c["date"] = pd.to_datetime(df_c["date"], errors="coerce")
    df_c = df_c.dropna(subset=["date"])
    df_c = engineer_features(df_c)

    if exclude_outliers:
        n0 = len(df_c)
        df_c = df_c[df_c["outlier_flag"] == 0].copy()
        logger.info("[%s v3] dropped %d outlier rows (week-over-week >50%%)",
                    commodity, n0 - len(df_c))

    # Backfill missing v3 columns with sensible defaults.
    for col in V3_FEATURES:
        if col not in df_c.columns:
            df_c[col] = 0.0
    df_c[V3_FEATURES] = df_c[V3_FEATURES].apply(
        lambda s: s.fillna(s.median() if s.notna().any() else 0.0), axis=0
    )
    df_c = df_c.dropna(subset=["price_modal_kg"]).reset_index(drop=True)

    train = df_c[(df_c["date"] >= "2015-01-01") & (df_c["date"] <= "2024-12-31")].reset_index(drop=True)
    test = df_c[(df_c["date"] >= "2025-01-01") & (df_c["date"] <= "2026-04-30")].reset_index(drop=True)
    if len(train) < 20 or len(test) < 5:
        raise ValueError(
            f"[{commodity} v3] insufficient rows train={len(train)} test={len(test)}"
        )

    if tune_rf:
        rf, pred, resid, best_params = _fit_random_forest_tuned(
            train, test, V3_FEATURES,
        )
        kind = "random_forest_tuned"
    else:
        rf, pred, resid = _fit_random_forest(train, test, V3_FEATURES)
        best_params = {}
        kind = "random_forest"

    y_test = test["price_modal_kg"].astype(float).values
    mape_val = float(np.mean(np.abs((y_test - pred) / np.maximum(y_test, 1e-6)))) * 100.0
    mean_price = float(train["price_modal_kg"].astype(float).mean())

    slug = commodity.lower().replace(" ", "_")
    payload = {
        "model": rf,
        "model_kind": kind,
        "best_params": best_params,
        "features": V3_FEATURES,
        "feature_medians": {f: float(train[f].median()) for f in V3_FEATURES},
        "mape": round(mape_val, 4),
        "mean_price": mean_price,
        "residual_std": resid,
        "version": "v3",
        "commodity": commodity,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_rows": len(train),
        "test_rows": len(test),
        "exclude_outliers": exclude_outliers,
    }
    out_path = MODELS_DIR / f"arima_{slug}_v3.pkl"
    joblib.dump(payload, out_path)
    return {
        "commodity": commodity, "kind": kind, "mape": mape_val,
        "best_params": best_params,
        "train_rows": len(train), "test_rows": len(test),
        "exclude_outliers": exclude_outliers, "pickle": str(out_path),
    }


def _run_one_config(df_master: "pd.DataFrame", commodities: list[str],
                    *, exclude_spike: bool, with_usda: bool,
                    label: str) -> dict:
    """Train one config and return a small summary dict."""

    df = df_master.copy()
    if with_usda:
        df = _join_world_production(df)
        if WORLD_PRODUCTION_FEATURE not in V2_FEATURES:
            V2_FEATURES.append(WORLD_PRODUCTION_FEATURE)
            V2_EXTRA_FEATURES.append(WORLD_PRODUCTION_FEATURE)
    rows = []
    for commodity in commodities:
        df_c = df[df["commodity"] == commodity].copy()
        dropped = 0
        if exclude_spike:
            df_c, dropped = _apply_spike_exclusion(df_c, commodity)
        # Stitch the (possibly trimmed) commodity back into a per-config df
        # the trainer will filter on commodity name again.
        df_mod = pd.concat([df[df["commodity"] != commodity], df_c], ignore_index=True)
        try:
            res = train_commodity(df_mod, commodity)
            rows.append({
                "config": label, "commodity": commodity,
                "v1": res["results"]["v1"]["mape"],
                "v2": res["results"]["v2"]["mape"],
                "rf": res["results"]["rf"]["mape"],
                "selected": res["selected"], "selected_mape": res["selected_mape"],
                "target": TARGET_MAPE[commodity], "hit": res["target_hit"],
                "rows_dropped": dropped,
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "config": label, "commodity": commodity, "error": str(exc),
            })
    if with_usda:
        # Don't pollute later configs.
        if WORLD_PRODUCTION_FEATURE in V2_FEATURES:
            V2_FEATURES.remove(WORLD_PRODUCTION_FEATURE)
        if WORLD_PRODUCTION_FEATURE in V2_EXTRA_FEATURES:
            V2_EXTRA_FEATURES.remove(WORLD_PRODUCTION_FEATURE)
    return {"label": label, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exclude-spike", action="store_true",
                        help="Drop Mar-May 2025 + 4-week >50%% pct-change outliers.")
    parser.add_argument("--include-world-production", action="store_true",
                        help="Join USDA FAS world raisin production as annual feature.")
    parser.add_argument("--commodities", nargs="*",
                        help="Subset of commodities to train (default: all).")
    parser.add_argument("--compare-dry-grapes", action="store_true",
                        help="Run all 4 configs for Dry_Grapes and print a comparison.")
    parser.add_argument("--train-v3", nargs="*", metavar="COMMODITY",
                        help="Train v3 models (default: Pomegranate Dry_Grapes).")
    parser.add_argument("--tune-rf", action="store_true",
                        help="GridSearchCV on n_estimators/max_depth/min_samples_leaf when training v3.")
    parser.add_argument("--dry-grapes-ab", action="store_true",
                        help="v3 Dry_Grapes Model A (drop outliers) vs Model B (keep + flag).")
    args = parser.parse_args()

    df, source = load_dataset()
    logger.info("Dataset source=%s  rows=%d", source, len(df))

    target_commodities = args.commodities or COMMODITIES

    # --- Dry Grapes Model A vs Model B (outlier handling) ---------------
    if args.dry_grapes_ab:
        print()
        print("=" * 92)
        print("DRY GRAPES v3 — Model A (drop outliers) vs Model B (keep + outlier_flag)")
        print("=" * 92)
        rA = train_commodity_v3(df, "Dry_Grapes",
                                exclude_outliers=True, tune_rf=args.tune_rf)
        rB = train_commodity_v3(df, "Dry_Grapes",
                                exclude_outliers=False, tune_rf=args.tune_rf)
        winner = rA if rA["mape"] <= rB["mape"] else rB
        print(f"  Model A (drop outliers): {rA['mape']:.2f}% MAPE  "
              f"({rA['train_rows']} train / {rA['test_rows']} test)")
        print(f"  Model B (keep + flag):   {rB['mape']:.2f}% MAPE  "
              f"({rB['train_rows']} train / {rB['test_rows']} test)")
        if args.tune_rf:
            print(f"  Best params A: {rA['best_params']}")
            print(f"  Best params B: {rB['best_params']}")
        print(f"\n  WINNER: {'A' if winner is rA else 'B'} "
              f"at {winner['mape']:.2f}% MAPE")
        # Re-save the winner under the canonical v3 path (in case A != B).
        joblib.dump(joblib.load(winner["pickle"]),
                    MODELS_DIR / "arima_dry_grapes_v3.pkl")
        print(f"  saved: {MODELS_DIR / 'arima_dry_grapes_v3.pkl'}")
        return 0

    # --- v3 training mode (Pomegranate + optionally Dry_Grapes) -----------
    if args.train_v3 is not None:
        v3_commodities = args.train_v3 or ["Pomegranate", "Dry_Grapes"]
        print()
        print("=" * 92)
        print(f"V3 TRAINING  tune_rf={args.tune_rf}")
        print("=" * 92)
        for commodity in v3_commodities:
            try:
                r = train_commodity_v3(df, commodity,
                                       exclude_outliers=False, tune_rf=args.tune_rf)
                print(f"  {commodity:<14} {r['kind']:<22} "
                      f"MAPE={r['mape']:.2f}%  "
                      f"train={r['train_rows']} test={r['test_rows']}")
                if args.tune_rf and r["best_params"]:
                    print(f"    best_params: {r['best_params']}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {commodity}: FAILED ({exc})")
        return 0

    # --- comparison mode: 4 configs side-by-side ---
    if args.compare_dry_grapes:
        configs = [
            ("baseline",       False, False),
            ("exclude_spike",  True,  False),
            ("with_usda",      False, True),
            ("exclude+usda",   True,  True),
        ]
        all_rows = []
        for label, ex, usda in configs:
            print(f"\n[config] {label}  exclude_spike={ex}  with_usda={usda}")
            r = _run_one_config(df, ["Dry_Grapes"], exclude_spike=ex,
                                with_usda=usda, label=label)
            all_rows.extend(r["rows"])
        print()
        print("=" * 92)
        print("DRY GRAPES MAPE COMPARISON  (with vs. without 2025 outlier + USDA feature)")
        print("=" * 92)
        hdr = f"{'config':<14} {'v1':>8} {'v2':>8} {'rf':>8} {'selected':>10} {'mape':>7} {'dropped':>8}"
        print(hdr); print("-" * len(hdr))
        for r in all_rows:
            if "error" in r:
                print(f"{r['config']:<14} ERROR {r['error']}"); continue
            print(f"{r['config']:<14} {r['v1']:>8.2f} {r['v2']:>8.2f} {r['rf']:>8.2f} "
                  f"{r['selected']:>10} {r['selected_mape']:>7.2f} {r['rows_dropped']:>8}")
        print("=" * 92)
        return 0

    # --- single-config mode (back-compat) ---
    if args.include_world_production:
        df = _join_world_production(df)
        if WORLD_PRODUCTION_FEATURE not in V2_FEATURES:
            V2_FEATURES.append(WORLD_PRODUCTION_FEATURE)
            V2_EXTRA_FEATURES.append(WORLD_PRODUCTION_FEATURE)

    summary_rows = []
    full_report: dict = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "data_source": source,
        "random_state": RANDOM_STATE,
        "exclude_spike": args.exclude_spike,
        "include_world_production": args.include_world_production,
        "commodities": {},
    }

    for commodity in target_commodities:
        df_c = df[df["commodity"] == commodity].copy()
        if args.exclude_spike:
            df_c, dropped = _apply_spike_exclusion(df_c, commodity)
            logger.info("[%s] excluded %d spike rows", commodity, dropped)
        df_mod = pd.concat([df[df["commodity"] != commodity], df_c], ignore_index=True)
        try:
            result = train_commodity(df_mod, commodity)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Training failed for %s: %s", commodity, exc)
            full_report["commodities"][commodity] = {"error": str(exc)}
            continue
        full_report["commodities"][commodity] = result
        summary_rows.append(
            {
                "commodity": commodity,
                "v1_mape": result["results"]["v1"]["mape"],
                "v2_mape": result["results"]["v2"]["mape"],
                "rf_mape": result["results"]["rf"]["mape"],
                "selected": result["selected"],
                "selected_mape": result["selected_mape"],
                "target": TARGET_MAPE[commodity],
                "target_hit": result["target_hit"],
            }
        )

    with REPORT_PATH.open("w") as fh:
        json.dump(full_report, fh, indent=2, default=str)
    logger.info("Wrote training report: %s", REPORT_PATH)

    print()
    print("=" * 92)
    print("PRICE MODEL TRAINING SUMMARY")
    print("=" * 92)
    header = f"{'commodity':<14} {'v1_mape':>8} {'v2_mape':>8} {'rf_mape':>8} {'selected':>10} {'target':>8} {'hit':>5}"
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(
            f"{row['commodity']:<14} "
            f"{row['v1_mape']:>8.2f} {row['v2_mape']:>8.2f} {row['rf_mape']:>8.2f} "
            f"{row['selected']:>10} {row['target']:>8.1f} "
            f"{'YES' if row['target_hit'] else 'NO':>5}"
        )
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
