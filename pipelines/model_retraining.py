"""ShetMitra continuous model training system.

Four auto-retrain triggers (each idempotent + testable):

  1. check_and_retrain_if_new_data()  - Sunday 01:00 IST cron (>=50 new rows)
  2. check_rolling_mape()              - 1st of month 02:00 IST cron (>25% drift)
  3. retrain_on_harvest_actuals()      - invoked from webhooks_harvest, fires
                                          once when today's harvest count >= 10
  4. annual_full_retrain()             - Oct 1, 00:00 IST cron

Each trigger:
  * Reads from SQLite (data/test.db) by default; honors SHETMITRA_DB_PATH env.
  * Calls the retrain function (injectable for tests).
  * Writes an audit row to cron_run_log.
  * Records new model versions in model_registry (and flips prior is_active=0).
  * Optionally pings Pankaj via WhatsApp (PANKAJ_ALERT_MOBILE) on retrain.

All side-effecting clock + DB + sender + retrainer dependencies are injectable.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)


DEFAULT_DB_PATH = Path(os.environ.get(
    "SHETMITRA_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "test.db"),
))

# Threshold knobs (overridable via env so an operator can tune without code).
WEEKLY_MIN_NEW_ROWS = int(os.environ.get("WEEKLY_MIN_NEW_ROWS", "50"))
MAPE_DEGRADATION_MULTIPLIER = float(
    os.environ.get("MAPE_DEGRADATION_MULTIPLIER", "1.25")
)
HARVEST_TRIGGER_MIN_COUNT = int(os.environ.get("HARVEST_TRIGGER_MIN_COUNT", "10"))

# Commodities + varieties that participate in the price/yield models.
PRICE_COMMODITIES = ("Dry_Grapes", "Pomegranate")
MANGO_VARIETIES = ("Alphonso", "Kesar", "Dasheri", "Totapuri", "Banganapalli")

# Jharkhand mango variety models produced by the ML training agent (Agent 4).
# Each entry maps a variety -> the pickle filename the trainer will write
# under data/models/. The retrainer tolerates FileNotFoundError so a
# partial swarm doesn't break the price-model retrain leg.
JH_MANGO_VARIETIES: tuple[str, ...] = ("Mallika", "Jardalu", "Amrapali")
JH_MANGO_MODEL_PICKLES: dict[str, str] = {
    "Mallika":  "arima_mango_mallika_jharkhand.pkl",
    "Jardalu":  "arima_mango_jardalu_jharkhand.pkl",
    "Amrapali": "arima_mango_amrapali_jharkhand.pkl",
}

# Baseline MAPE numbers anchor the "25% worse than baseline" check. Values
# match the SDD targets for the respective v2/v3 pickles; tighten over time
# by updating model_registry rows.
BASELINE_MAPE: dict[tuple[str, str | None], float] = {
    ("Dry_Grapes",     None): 12.39,   # v3 winner from our last run
    ("Pomegranate",    None): 20.30,
    ("Mango", "Alphonso"):     13.29,
    ("Mango", "Kesar"):         2.85,
    ("Mango", "Dasheri"):       3.78,
    ("Mango", "Totapuri"):      1.79,
    ("Mango", "Banganapalli"):  3.48,
}


# --------------------------------------------------------------------------- #
# Small data classes                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class RetrainResult:
    commodity: str
    variety: str | None
    model_version: str
    model_type: str
    mape: float
    mae: float | None = None
    training_rows: int = 0
    training_date_start: date | None = None
    training_date_end: date | None = None
    pickle_path: str | None = None


# --------------------------------------------------------------------------- #
# DB helpers                                                                  #
# --------------------------------------------------------------------------- #
def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Best-effort: make sure model_registry + cron_run_log exist (so the
    triggers work even when running against a tmp DB that was not built
    via seed_local_sqlite).
    """
    try:
        from scripts.seed_local_sqlite import ensure_continuous_training_schema
        ensure_continuous_training_schema(conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not ensure ct schema: %s", exc)


def log_cron_run(
    conn: sqlite3.Connection, *, job_id: str, status: str,
    reason: str | None = None, metadata: dict | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> str:
    """Insert a cron_run_log row. Status must be in {'ok','skipped','error'}."""
    if status not in ("ok", "skipped", "error"):
        raise ValueError(f"invalid status: {status!r}")
    row_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO cron_run_log (id, job_id, status, reason, metadata, fired_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            row_id, job_id, status, reason,
            json.dumps(metadata or {}, default=str),
            clock().isoformat(),
        ),
    )
    conn.commit()
    return row_id


def register_model_version(
    conn: sqlite3.Connection, result: RetrainResult,
    *, retrain_trigger: str,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> str:
    """Insert a new model_registry row and flip prior is_active for the same
    (commodity, variety) pair. Returns the new row id.
    """
    # Deactivate prior winners for this commodity/variety.
    conn.execute(
        """
        UPDATE model_registry SET is_active = 0
         WHERE commodity = ?
           AND COALESCE(variety,'') = COALESCE(?, '')
           AND is_active = 1
        """,
        (result.commodity, result.variety),
    )
    row_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO model_registry (
            id, commodity, variety, model_version, model_type,
            mape, mae, training_rows,
            training_date_start, training_date_end,
            retrain_trigger, is_active, pickle_path, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            row_id, result.commodity, result.variety,
            result.model_version, result.model_type,
            result.mape, result.mae, result.training_rows,
            result.training_date_start.isoformat() if result.training_date_start else None,
            result.training_date_end.isoformat() if result.training_date_end else None,
            retrain_trigger, result.pickle_path,
            clock().isoformat(),
        ),
    )
    conn.commit()
    return row_id


# --------------------------------------------------------------------------- #
# WhatsApp alert                                                              #
# --------------------------------------------------------------------------- #
def _alert_pankaj(body: str) -> dict:
    """Best-effort WhatsApp alert via api/whatsapp_sender. Silent on failure."""
    try:
        from api.whatsapp_sender import get_sender
    except Exception as exc:  # noqa: BLE001
        logger.warning("alert: sender unavailable (%s)", exc)
        return {"status": "skipped", "reason": "sender_unavailable"}
    mobile = os.environ.get("PANKAJ_ALERT_MOBILE", "").strip() or "9999999999"
    try:
        return get_sender().send(mobile, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("alert send failed: %s", exc)
        return {"status": "error", "reason": str(exc)}


# --------------------------------------------------------------------------- #
# Default retrainer (calls into scripts/train_price_model + train_mango_models)
# --------------------------------------------------------------------------- #
def _default_retrain_price(commodities: Iterable[str]) -> list[RetrainResult]:
    """Re-pull from Supabase and retrain v3 for each commodity. Each result
    captures the MAPE the trainer reports.
    """
    out: list[RetrainResult] = []
    try:
        from scripts.train_price_model import load_dataset, train_commodity_v3
    except Exception as exc:  # noqa: BLE001
        logger.warning("price trainer not importable: %s", exc)
        return out
    try:
        df, _ = load_dataset()
    except Exception as exc:  # noqa: BLE001
        logger.warning("price dataset load failed: %s", exc)
        return out
    for commodity in commodities:
        try:
            r = train_commodity_v3(
                df, commodity, exclude_outliers=True, tune_rf=False,
            )
            out.append(RetrainResult(
                commodity=commodity, variety=None,
                model_version="v3",
                model_type=r.get("kind", "random_forest"),
                mape=float(r["mape"]), mae=None,
                training_rows=int(r.get("train_rows", 0)),
                pickle_path=str(r.get("pickle")),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("price retrain failed for %s: %s", commodity, exc)
    return out


def _default_retrain_mango(varieties: Iterable[str]) -> list[RetrainResult]:
    out: list[RetrainResult] = []
    try:
        from scripts.train_mango_models import load_dataset, train_variety
    except Exception as exc:  # noqa: BLE001
        logger.warning("mango trainer not importable: %s", exc)
        return out
    try:
        price_df, forex_df, _src = load_dataset()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mango dataset load failed: %s", exc)
        return out
    for variety in varieties:
        try:
            r = train_variety(price_df, forex_df, variety)
            mape = r.get("selected_mape", r.get("mape"))
            kind = r.get("selected_kind", r.get("model_kind", "random_forest"))
            out.append(RetrainResult(
                commodity="Mango", variety=variety,
                model_version="v3",
                model_type=str(kind),
                mape=float(mape) if mape is not None else float("nan"),
                training_rows=int(r.get("train_rows", 0)),
                pickle_path=str(r.get("pickle_path", "")),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("mango retrain failed for %s: %s", variety, exc)
    return out


def _default_retrain_mango_jh(
    varieties: Iterable[str] = JH_MANGO_VARIETIES,
) -> list[RetrainResult]:
    """Retrain (or detect-and-register) the Jharkhand mango models.

    The ML training agent (Agent 4) produces these pickles under
    ``data/models/`` — e.g. ``arima_mango_mallika_jharkhand.pkl``. When
    the file exists we register the pickle path against the model_registry
    via a RetrainResult; when the file is missing (partial swarm) we
    tolerate FileNotFoundError and log a warning so the retrain leg does
    not crash. We deliberately do NOT call into the trainer because the
    JH trainer is owned by a sibling agent.
    """
    models_dir = Path(__file__).resolve().parent.parent / "data" / "models"
    out: list[RetrainResult] = []
    for variety in varieties:
        pickle_name = JH_MANGO_MODEL_PICKLES.get(variety)
        if pickle_name is None:
            logger.warning(
                "no JH pickle filename registered for variety %s — skipping",
                variety,
            )
            continue
        pickle_path = models_dir / pickle_name
        try:
            # Use stat() so a missing pickle raises FileNotFoundError that
            # we can swallow cleanly below.
            pickle_path.stat()
        except FileNotFoundError:
            logger.warning(
                "JH mango pickle missing for %s at %s — skipping (Agent 4 "
                "training swarm may not have completed yet)",
                variety, pickle_path,
            )
            continue
        except OSError as exc:  # noqa: BLE001
            logger.warning(
                "JH mango pickle stat failed for %s: %s — skipping",
                variety, exc,
            )
            continue
        # Pickle found — record it for the registry. We use NaN for MAPE
        # because Agent 4 owns the metric reporting; the entry exists so
        # downstream callers know the JH model has shipped.
        out.append(RetrainResult(
            commodity="Mango",
            variety=variety,
            model_version="v1_jh",
            model_type="arima",
            mape=float("nan"),
            training_rows=0,
            pickle_path=str(pickle_path),
        ))
    return out


def _default_retrain_all(trigger: str) -> list[RetrainResult]:
    out = []
    out.extend(_default_retrain_price(PRICE_COMMODITIES))
    out.extend(_default_retrain_mango(MANGO_VARIETIES))
    # Jharkhand mango models — partial-swarm-tolerant. If Agent 4's training
    # has not produced the pickles yet, this leg logs warnings and returns
    # an empty list rather than crashing the retrain.
    try:
        out.extend(_default_retrain_mango_jh(JH_MANGO_VARIETIES))
    except Exception as exc:  # noqa: BLE001
        logger.warning("JH mango retrain leg failed: %s", exc)
    return out


# --------------------------------------------------------------------------- #
# TRIGGER 1 — Weekly new-data check                                           #
# --------------------------------------------------------------------------- #
def check_and_retrain_if_new_data(
    *,
    db_path: Path | str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    retrain_fn: Callable[[str], list[RetrainResult]] | None = None,
    alert_fn: Callable[[str], dict] | None = None,
    min_new_rows: int | None = None,
) -> dict:
    """Sunday 01:00 IST. Retrains if >= WEEKLY_MIN_NEW_ROWS new rows in last 7d."""

    job_id = "WEEKLY_NEW_DATA_RETRAIN"
    threshold = int(min_new_rows if min_new_rows is not None else WEEKLY_MIN_NEW_ROWS)
    conn = _connect(db_path)
    _ensure_tables(conn)
    try:
        cutoff = (clock() - timedelta(days=7)).isoformat()
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM price_history_training "
                "WHERE COALESCE(created_at, date) >= ?", (cutoff,),
            )
            new_rows = int(cur.fetchone()[0])
        except sqlite3.OperationalError as exc:
            log_cron_run(conn, job_id=job_id, status="error",
                         reason=f"query_failed: {exc}", clock=clock)
            return {"action": "error", "reason": "price_history_training_missing"}

        if new_rows < threshold:
            log_cron_run(conn, job_id=job_id, status="skipped",
                         reason=f"only {new_rows} new rows (need {threshold})",
                         metadata={"new_rows": new_rows}, clock=clock)
            logger.info("Skipped — only %d new rows (need %d)", new_rows, threshold)
            return {"action": "skipped", "new_rows": new_rows, "threshold": threshold}

        retrain = retrain_fn or _default_retrain_all
        results = retrain("weekly_new_data") or []
        for r in results:
            register_model_version(conn, r, retrain_trigger="weekly_new_data",
                                    clock=clock)
        log_cron_run(conn, job_id=job_id, status="ok",
                     reason=f"retrained on {new_rows} new rows",
                     metadata={
                         "new_rows": new_rows,
                         "models_retrained": [
                             {"commodity": r.commodity, "variety": r.variety,
                              "mape": r.mape} for r in results
                         ],
                     }, clock=clock)
        send = alert_fn or _alert_pankaj
        send(f"Models retrained — {new_rows} new price rows added "
             f"({len(results)} models updated).")
        return {"action": "retrained", "new_rows": new_rows,
                "models": [{"commodity": r.commodity, "variety": r.variety,
                            "mape": r.mape, "model_type": r.model_type}
                           for r in results]}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# TRIGGER 2 — Monthly MAPE drift check                                        #
# --------------------------------------------------------------------------- #
def check_rolling_mape(
    *,
    db_path: Path | str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    retrain_fn: Callable[[str], list[RetrainResult]] | None = None,
    alert_fn: Callable[[str], dict] | None = None,
    multiplier: float | None = None,
    rolling_window_days: int = 30,
) -> dict:
    """1st of month 02:00 IST. For each (commodity, variety) joins the last 30
    days of intelligence_reports.price_forecast_day1 to price_history_training
    modal prices, computes rolling MAPE, and retrains if it drifted past
    BASELINE_MAPE * multiplier.
    """

    job_id = "MONTHLY_MAPE_DRIFT_CHECK"
    threshold_mult = float(
        multiplier if multiplier is not None else MAPE_DEGRADATION_MULTIPLIER
    )
    conn = _connect(db_path)
    _ensure_tables(conn)
    drift_findings: list[dict] = []
    try:
        cutoff = (clock() - timedelta(days=rolling_window_days)).isoformat()

        for (commodity, variety), baseline in BASELINE_MAPE.items():
            try:
                cur = conn.execute(
                    """
                    SELECT ir.price_forecast_day1 AS pred,
                           ph.modal_price        AS actual
                    FROM intelligence_reports ir
                    LEFT JOIN price_history_training ph
                      ON ph.commodity = ir.commodity
                     AND date(ph.date) = date(ir.report_date, '+1 day')
                    WHERE ir.commodity = ?
                      AND COALESCE(ir.report_date, '') >= ?
                      AND ir.price_forecast_day1 IS NOT NULL
                      AND ph.modal_price IS NOT NULL
                    """,
                    (commodity, cutoff),
                )
                rows = cur.fetchall()
            except sqlite3.OperationalError as exc:
                logger.debug("rolling MAPE query failed for %s: %s", commodity, exc)
                continue
            if not rows:
                continue
            errs = []
            for r in rows:
                pred = float(r["pred"] or 0.0)
                actual = float(r["actual"] or 0.0)
                if actual > 1e-6:
                    errs.append(abs(pred - actual) / actual)
            if not errs:
                continue
            rolling_mape = (sum(errs) / len(errs)) * 100.0
            ratio = rolling_mape / max(baseline, 0.01)
            drift_findings.append({
                "commodity": commodity, "variety": variety,
                "baseline_mape": baseline,
                "rolling_mape": round(rolling_mape, 2),
                "ratio": round(ratio, 3),
                "samples": len(rows),
                "degraded": ratio > threshold_mult,
            })

        degraded = [f for f in drift_findings if f["degraded"]]
        if not degraded:
            log_cron_run(conn, job_id=job_id, status="skipped",
                         reason="no model degraded past threshold",
                         metadata={"findings": drift_findings,
                                    "multiplier": threshold_mult},
                         clock=clock)
            return {"action": "skipped", "findings": drift_findings}

        retrain = retrain_fn or _default_retrain_all
        results = retrain("monthly_mape_drift") or []
        for r in results:
            register_model_version(conn, r, retrain_trigger="monthly_mape_drift",
                                    clock=clock)
        log_cron_run(conn, job_id=job_id, status="ok",
                     reason=f"retrained {len(results)} models after drift",
                     metadata={"findings": drift_findings,
                                "models_retrained": [
                                    {"commodity": r.commodity,
                                     "variety": r.variety, "mape": r.mape}
                                    for r in results
                                ]}, clock=clock)
        send = alert_fn or _alert_pankaj
        worst = max(degraded, key=lambda f: f["rolling_mape"])
        send(
            f"Auto-retrain triggered — rolling MAPE degraded to "
            f"{worst['rolling_mape']:.1f}% (baseline "
            f"{worst['baseline_mape']:.1f}%) for "
            f"{worst['commodity']}/{worst['variety'] or '-'}."
        )
        return {"action": "retrained", "findings": drift_findings,
                "models": [r.commodity for r in results]}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# TRIGGER 3 — Harvest actuals ingest threshold                                #
# --------------------------------------------------------------------------- #
def retrain_on_harvest_actuals(
    *,
    db_path: Path | str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    retrain_fn: Callable[[str], list[RetrainResult]] | None = None,
    alert_fn: Callable[[str], dict] | None = None,
    min_count: int | None = None,
) -> dict:
    """Invoked from webhooks_harvest after each completed collection.
    Idempotent: only fires once per UTC day even if called many times.
    """

    job_id = "HARVEST_ACTUALS_RETRAIN"
    threshold = int(min_count if min_count is not None else HARVEST_TRIGGER_MIN_COUNT)
    conn = _connect(db_path)
    _ensure_tables(conn)
    try:
        today_iso = clock().date().isoformat()
        # Already retrained today?
        cur = conn.execute(
            "SELECT COUNT(*) FROM cron_run_log "
            "WHERE job_id = ? AND status = 'ok' AND date(fired_at) = date(?)",
            (job_id, clock().isoformat()),
        )
        if int(cur.fetchone()[0]) > 0:
            return {"action": "skipped", "reason": "already_fired_today"}

        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM farm_harvest_actuals "
                "WHERE status = 'COMPLETE' "
                "  AND date(COALESCE(collection_completed_at, created_at)) = date(?)",
                (clock().isoformat(),),
            )
            today_count = int(cur.fetchone()[0])
        except sqlite3.OperationalError as exc:
            log_cron_run(conn, job_id=job_id, status="error",
                         reason=f"farm_harvest_actuals missing: {exc}",
                         clock=clock)
            return {"action": "error", "reason": "farm_harvest_actuals_missing"}

        if today_count < threshold:
            log_cron_run(conn, job_id=job_id, status="skipped",
                         reason=f"only {today_count} harvest actuals today "
                                f"(need {threshold})",
                         metadata={"today_count": today_count}, clock=clock)
            return {"action": "skipped", "today_count": today_count,
                    "threshold": threshold}

        retrain = retrain_fn or _default_retrain_all
        # Yield + grade prediction live alongside price for our current trainer
        # surface; retrain everything is the safe answer until we split them.
        results = retrain("harvest_actuals") or []
        for r in results:
            register_model_version(conn, r, retrain_trigger="harvest_actuals",
                                    clock=clock)
        log_cron_run(conn, job_id=job_id, status="ok",
                     reason=f"retrained on {today_count} harvest actuals today",
                     metadata={"today_count": today_count,
                                "models_retrained":
                                    [r.commodity for r in results]},
                     clock=clock)
        send = alert_fn or _alert_pankaj
        send(f"Models retrained on {today_count} new harvest actuals today. "
             f"({len(results)} models updated.)")
        return {"action": "retrained", "today_count": today_count,
                "models": [{"commodity": r.commodity, "variety": r.variety,
                            "mape": r.mape} for r in results]}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# TRIGGER 4 — Annual full retrain (Oct 1)                                     #
# --------------------------------------------------------------------------- #
def annual_full_retrain(
    *,
    db_path: Path | str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    retrain_fn: Callable[[str], list[RetrainResult]] | None = None,
    alert_fn: Callable[[str], dict] | None = None,
) -> dict:
    """Oct 1, 00:00 IST. Full retrain + a pre-season report queued for traders."""

    job_id = "ANNUAL_FULL_RETRAIN"
    conn = _connect(db_path)
    _ensure_tables(conn)
    try:
        retrain = retrain_fn or _default_retrain_all
        results = retrain("annual_full") or []
        for r in results:
            register_model_version(conn, r, retrain_trigger="annual_full",
                                    clock=clock)

        # Pre-season report (best-effort, won't crash the retrain).
        try:
            from pipelines.report_generator import generate_pre_season_content
            preseason = generate_pre_season_content(
                commodity="Dry Grapes", region="Tasgaon/Sangli Belt",
                bearing_year="ON", belt_ndvi=0.62, vs_3yr_avg=8.5,
                expected_volume_mt=12000, peak_week="April Week 3",
            )
        except Exception as exc:  # noqa: BLE001
            preseason = None
            logger.warning("pre-season report skipped: %s", exc)

        try:
            conn.execute(
                """
                INSERT INTO intelligence_reports (
                    id, report_type, commodity, report_date,
                    content_english, signal, model_version
                ) VALUES (?, 'PRE_SEASON', 'Dry Grapes', ?, ?, 'HOLD', ?)
                """,
                (str(uuid.uuid4()), clock().date().isoformat(),
                 preseason or "Pre-season forecast pending.", "v3"),
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            logger.warning("could not write pre-season to intelligence_reports: %s", exc)

        log_cron_run(conn, job_id=job_id, status="ok",
                     reason=f"annual retrain done — {len(results)} models",
                     metadata={"models_retrained":
                                [{"commodity": r.commodity,
                                  "variety": r.variety, "mape": r.mape}
                                 for r in results]},
                     clock=clock)
        send = alert_fn or _alert_pankaj
        send(f"Annual full retrain complete — {len(results)} models updated. "
             "Pre-season forecast queued for subscribers.")
        return {"action": "retrained", "models": len(results),
                "preseason_queued": preseason is not None}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Read endpoint helper used by GET /models/registry                           #
# --------------------------------------------------------------------------- #
def get_registry_snapshot(db_path: Path | str | None = None,
                          *, limit: int = 200) -> dict:
    """Returns the registry sorted commodity/variety/created_at for the
    GET /models/registry endpoint. Each commodity is grouped, with the
    current active row and the MAPE-over-time history.
    """

    conn = _connect(db_path)
    _ensure_tables(conn)
    try:
        cur = conn.execute(
            """
            SELECT id, commodity, variety, model_version, model_type,
                   mape, mae, training_rows,
                   training_date_start, training_date_end,
                   retrain_trigger, is_active, pickle_path, created_at
              FROM model_registry
             ORDER BY commodity, COALESCE(variety,''), created_at DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        groups: dict[tuple[str, str | None], dict[str, Any]] = {}
        for r in rows:
            key = (r["commodity"], r["variety"])
            g = groups.setdefault(key, {
                "commodity": r["commodity"], "variety": r["variety"],
                "history": [], "active": None,
            })
            g["history"].append(r)
            if int(r["is_active"]) == 1 and g["active"] is None:
                g["active"] = r
        # MAPE-over-time series for charting.
        for g in groups.values():
            g["mape_series"] = [
                {"created_at": h["created_at"], "mape": h["mape"]}
                for h in sorted(g["history"], key=lambda x: x["created_at"])
            ]
        return {"models": list(groups.values()), "count": len(rows)}
    finally:
        conn.close()


__all__ = [
    "RetrainResult",
    "BASELINE_MAPE", "WEEKLY_MIN_NEW_ROWS",
    "MAPE_DEGRADATION_MULTIPLIER", "HARVEST_TRIGGER_MIN_COUNT",
    "PRICE_COMMODITIES", "MANGO_VARIETIES",
    "JH_MANGO_VARIETIES", "JH_MANGO_MODEL_PICKLES",
    "log_cron_run", "register_model_version",
    "check_and_retrain_if_new_data",
    "check_rolling_mape",
    "retrain_on_harvest_actuals",
    "annual_full_retrain",
    "get_registry_snapshot",
    "_default_retrain_mango_jh",
]
