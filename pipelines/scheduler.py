"""pipelines/scheduler.py

ShetMitra - Trader Intelligence - APScheduler wiring.

This module is intentionally lightweight: importing it has no side effects.
``build_scheduler()`` only constructs (does not start) a BackgroundScheduler
with the 4 trader jobs added. APScheduler itself is an optional dependency;
if it is missing, ``build_scheduler`` raises a clear RuntimeError and the
``_job_*`` callables remain importable for direct use or testing.

Jobs (SDD section 10, Agent 2):
  WEEKLY_REPORT - cron Mon 05:00 Asia/Kolkata
  DAILY_UPDATE  - cron daily 06:45 Asia/Kolkata
  FLASH_CHECK   - interval every 2h, hours 6-20 IST
  PRE_SEASON    - cron first Monday of September, 05:00 IST
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Job definitions (data, not behaviour) - exposed for tests
# --------------------------------------------------------------------------- #
JOB_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "WEEKLY_REPORT",
        "func": "pipelines.scheduler:_job_weekly_report",
        "trigger": "cron",
        "trigger_kwargs": {"day_of_week": "mon", "hour": 5, "minute": 0},
        "description": "Generate and dispatch weekly trader report",
    },
    {
        "id": "DAILY_UPDATE",
        "func": "pipelines.scheduler:_job_daily_update",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 6, "minute": 45},
        "description": "Premium daily price update",
    },
    {
        "id": "FLASH_CHECK",
        "func": "pipelines.scheduler:_job_flash_check",
        "trigger": "interval",
        "trigger_kwargs": {"hours": 2, "start_hour": 6, "end_hour": 20},
        "description": "Flash alert detection sweep every 2 hours, 6 AM-8 PM IST",
    },
    {
        "id": "PRE_SEASON",
        "func": "pipelines.scheduler:_job_pre_season",
        "trigger": "cron",
        "trigger_kwargs": {
            "month": 9, "day": "1-7", "day_of_week": "mon",
            "hour": 5, "minute": 0,
        },
        "description": "Annual pre-season forecast - first Monday of September",
    },
    {
        "id": "PRE_SEASON_JH",
        "func": "pipelines.scheduler:_job_pre_season_jh",
        "trigger": "cron",
        "trigger_kwargs": {
            "month": 2, "day": "1-7", "day_of_week": "mon",
            "hour": 5, "minute": 0,
        },
        "description": (
            "Jharkhand pre-season forecast - first Monday of February "
            "(JH mango harvest starts April-May)."
        ),
    },
    {
        "id": "KEEPALIVE_PING",
        "func": "pipelines.scheduler:_job_keepalive_ping",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 3, "minute": 0},
        "description": (
            "Daily 03:00 IST keepalive ping against Supabase to prevent the "
            "free-tier project from auto-pausing after a week of idle."
        ),
    },
    # ----- Continuous model retraining (4 triggers) ---------------------- #
    {
        "id": "WEEKLY_NEW_DATA_RETRAIN",
        "func": "pipelines.scheduler:_job_weekly_new_data_retrain",
        "trigger": "cron",
        "trigger_kwargs": {"day_of_week": "sun", "hour": 1, "minute": 0},
        "description": (
            "Sunday 01:00 IST - retrain all commodity models when >= 50 new "
            "price_history_training rows have landed in the past 7 days."
        ),
    },
    {
        "id": "MONTHLY_MAPE_DRIFT_CHECK",
        "func": "pipelines.scheduler:_job_monthly_mape_drift_check",
        "trigger": "cron",
        "trigger_kwargs": {"day": 1, "hour": 2, "minute": 0},
        "description": (
            "1st of every month 02:00 IST - check rolling MAPE vs baseline; "
            "retrain any model whose rolling MAPE exceeds baseline x 1.25."
        ),
    },
    {
        "id": "ANNUAL_FULL_RETRAIN",
        "func": "pipelines.scheduler:_job_annual_full_retrain",
        "trigger": "cron",
        "trigger_kwargs": {"month": 10, "day": 1, "hour": 0, "minute": 0},
        "description": (
            "October 1 00:00 IST - full annual retrain of all commodity + "
            "variety models, plus a pre-season report queued to traders."
        ),
    },
    # Trigger 3 (harvest actuals) is intentionally NOT scheduled - it is
    # invoked from api/webhooks_harvest.py on each completed collection,
    # and itself dedupes via cron_run_log (one fire per UTC day).

    # ----- Bagaan Sathi marketplace jobs (SDD §9) ----------------------- #
    {
        "id": "DAILY_MATCHING",
        "func": "pipelines.scheduler:_job_daily_matching",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 16, "minute": 0},
        "description": (
            "Daily 16:00 IST — match active trader requirements with "
            "available farmer lots and notify farmers."
        ),
    },
    {
        "id": "PLANT_PRIORITY_QUEUE",
        "func": "pipelines.scheduler:_job_plant_priority_queue",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 17, "minute": 0},
        "description": (
            "Daily 17:00 IST (MH only) — fill Tasgaon plant capacity "
            "with Grade A grape supply before external traders."
        ),
    },
    {
        "id": "WEEKLY_AGGREGATION",
        "func": "pipelines.scheduler:_job_weekly_aggregation",
        "trigger": "cron",
        "trigger_kwargs": {"day_of_week": "sun", "hour": 23, "minute": 0},
        "description": (
            "Sunday 23:00 IST — group small farmer lots into tradeable "
            "aggregated lots for the next 3 harvest weeks."
        ),
    },
    {
        "id": "LOT_EXPIRY_CLEANUP",
        "func": "pipelines.scheduler:_job_lot_expiry_cleanup",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 2, "minute": 0},
        "description": (
            "Daily 02:00 IST — mark expired farmer_lots and "
            "trader_requirements rows as EXPIRED."
        ),
    },
    {
        "id": "TRADE_CONFIRMATION_FOLLOWUP",
        "func": "pipelines.scheduler:_job_trade_confirmation_followup",
        "trigger": "cron",
        "trigger_kwargs": {"hour": 10, "minute": 0},
        "description": (
            "Daily 10:00 IST — send WhatsApp confirmation requests to "
            "farmer + trader for connections 7 days old and unconfirmed."
        ),
    },
]


# --------------------------------------------------------------------------- #
# Job callables  (small - lazy imports inside)
# --------------------------------------------------------------------------- #
def _job_weekly_report() -> None:
    """Generate the weekly report for each active commodity and dispatch."""

    logger.info("scheduler: WEEKLY_REPORT job fired")
    try:
        from pipelines.signal_engine import generate_signal
        from pipelines.report_generator import generate_weekly_report_content
        try:
            from api import trader_whatsapp  # noqa: F401
        except Exception:  # noqa: BLE001
            trader_whatsapp = None  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        logger.warning("WEEKLY_REPORT: import failed (%s)", exc)
        return

    commodities = [
        ("Dry Grapes", "Tasgaon/Sangli Belt"),
        ("Pomegranate", "Solapur/Nashik Belt"),
    ]
    for commodity, region in commodities:
        try:
            signal_data = generate_signal(commodity)
            content = generate_weekly_report_content(
                commodity, region, signal_data, {}, {}, {},
            )
            logger.info("WEEKLY_REPORT generated for %s (%d chars)",
                        commodity, len(content))
            if trader_whatsapp is not None:
                send_fn = getattr(trader_whatsapp, "send_weekly_report", None)
                if callable(send_fn):
                    send_fn(commodity=commodity, content=content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WEEKLY_REPORT failed for %s: %s", commodity, exc)


def _job_daily_update() -> None:
    """Send the daily price update to PREMIUM subscribers."""

    logger.info("scheduler: DAILY_UPDATE job fired")
    try:
        from pipelines.signal_engine import generate_signal
        from pipelines.report_generator import generate_daily_update_content
        try:
            from api import trader_whatsapp  # noqa: F401
        except Exception:  # noqa: BLE001
            trader_whatsapp = None  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        logger.warning("DAILY_UPDATE: import failed (%s)", exc)
        return

    for commodity in ("Dry Grapes", "Pomegranate"):
        try:
            sig = generate_signal(commodity)
            content = generate_daily_update_content(
                commodity,
                modal_price=round(float(sig.get("fair_value", 0.0)), 2),
                change_pct=0.0,
                arrivals_mt=0.0,
                vs_forecast_pct=0.0,
                tomorrow_low=round(float(sig.get("entry_range", [0, 0])[0]), 2),
                tomorrow_high=round(float(sig.get("entry_range", [0, 0])[1]), 2),
                confidence_pct=round(float(sig.get("confidence", 0.0)) * 100.0, 1),
                signal=sig.get("signal", "HOLD"),
            )
            logger.info("DAILY_UPDATE generated for %s", commodity)
            if trader_whatsapp is not None:
                send_fn = getattr(trader_whatsapp, "send_daily_update", None)
                if callable(send_fn):
                    send_fn(commodity=commodity, content=content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DAILY_UPDATE failed for %s: %s", commodity, exc)


def _job_flash_check() -> None:
    """Sweep for flash-alert triggers and dispatch any that fit the weekly cap."""

    logger.info("scheduler: FLASH_CHECK job fired")
    try:
        from pipelines.flash_alert_detector import (
            check_flash_triggers, enforce_weekly_limit, persist_flash_alerts,
        )
        from pipelines.report_generator import generate_flash_alert_content
        try:
            from api import trader_whatsapp  # noqa: F401
        except Exception:  # noqa: BLE001
            trader_whatsapp = None  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        logger.warning("FLASH_CHECK: import failed (%s)", exc)
        return

    triggered = check_flash_triggers()
    if not triggered:
        return
    allowed = enforce_weekly_limit(triggered, "data/test.db")
    if not allowed:
        logger.info("FLASH_CHECK: weekly cap reached - %d skipped",
                    len(triggered))
        return
    try:
        persist_flash_alerts(allowed, "data/test.db")
    except Exception as exc:  # noqa: BLE001
        logger.warning("FLASH_CHECK persist failed: %s", exc)
    for alert in allowed:
        try:
            content = generate_flash_alert_content(
                commodity=alert["commodity"],
                trigger_event=alert.get("description", alert["trigger_type"]),
                current_price=alert.get("price_after") or 0.0,
                fair_value=alert.get("price_before") or 0.0,
                signal=alert["signal"],
                confidence_pct=70.0,
                next_update_time="Tomorrow 07:00 IST",
            )
            if trader_whatsapp is not None:
                send_fn = getattr(trader_whatsapp, "send_flash_alert", None)
                if callable(send_fn):
                    send_fn(commodity=alert["commodity"], content=content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("FLASH_CHECK dispatch failed: %s", exc)


def _job_keepalive_ping() -> dict:
    """Daily keepalive: hit Supabase so the free-tier project never auto-pauses.

    Strategy: a tiny REST query ``GET /rest/v1/farmers?select=id&limit=1`` with
    the anon key. PostgREST converts this to ``SELECT id FROM farmers LIMIT 1``
    which Supabase counts as activity for free-tier pausing purposes.

    Returns a small dict for logging / testability:
        {"status": "ok" | "skipped" | "error",
         "http_status": int | None,
         "url": str,
         "elapsed_ms": float}

    Never raises. A keepalive failure must not crash the scheduler loop.
    """

    import os
    import time
    import urllib.error
    import urllib.request

    url = os.environ.get(
        "SUPABASE_URL", "https://euydubpywdsettjywkms.supabase.co"
    ).rstrip("/")
    anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not anon:
        # Fall back to reading from common .env locations so the job stays
        # useful even when started via uvicorn without explicit env injection.
        for candidate in (
            "shet_mitra/.env", ".env",
            r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env",
        ):
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith("SUPABASE_ANON_KEY="):
                            anon = line.split("=", 1)[1].strip()
                            break
                if anon:
                    break
            except OSError:
                continue

    if not anon:
        logger.warning("KEEPALIVE_PING: no SUPABASE_ANON_KEY found; skipping.")
        return {"status": "skipped", "http_status": None, "url": url,
                "elapsed_ms": 0.0, "reason": "no_anon_key"}

    ping_url = f"{url}/rest/v1/farmers?select=id&limit=1"
    req = urllib.request.Request(
        ping_url, method="GET",
        headers={
            "apikey": anon,
            "Authorization": f"Bearer {anon}",
            "Accept": "application/json",
            "Range": "0-0",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read(256)  # discard body, we only care it answered
            elapsed_ms = (time.time() - t0) * 1000.0
            logger.info(
                "KEEPALIVE_PING: ok %d in %.0f ms", resp.status, elapsed_ms,
            )
            return {"status": "ok", "http_status": resp.status, "url": ping_url,
                    "elapsed_ms": elapsed_ms}
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.time() - t0) * 1000.0
        logger.warning(
            "KEEPALIVE_PING: HTTP %s in %.0f ms (still counts as activity)",
            exc.code, elapsed_ms,
        )
        return {"status": "ok", "http_status": exc.code, "url": ping_url,
                "elapsed_ms": elapsed_ms}
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.time() - t0) * 1000.0
        logger.warning(
            "KEEPALIVE_PING: error %s after %.0f ms",
            type(exc).__name__, elapsed_ms,
        )
        return {"status": "error", "http_status": None, "url": ping_url,
                "elapsed_ms": elapsed_ms, "error": str(exc)}


def _job_weekly_new_data_retrain() -> None:
    """Sunday 01:00 IST cron - lazy-imports model_retraining so a broken
    trainer cannot crash the scheduler thread."""
    logger.info("scheduler: WEEKLY_NEW_DATA_RETRAIN job fired")
    try:
        from pipelines.model_retraining import check_and_retrain_if_new_data
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly-retrain: import failed (%s)", exc)
        return
    try:
        check_and_retrain_if_new_data()
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly-retrain: run failed: %s", exc)


def _job_monthly_mape_drift_check() -> None:
    """1st of month 02:00 IST cron."""
    logger.info("scheduler: MONTHLY_MAPE_DRIFT_CHECK job fired")
    try:
        from pipelines.model_retraining import check_rolling_mape
    except Exception as exc:  # noqa: BLE001
        logger.warning("monthly-mape: import failed (%s)", exc)
        return
    try:
        check_rolling_mape()
    except Exception as exc:  # noqa: BLE001
        logger.warning("monthly-mape: run failed: %s", exc)


def _job_annual_full_retrain() -> None:
    """October 1 00:00 IST cron."""
    logger.info("scheduler: ANNUAL_FULL_RETRAIN job fired")
    try:
        from pipelines.model_retraining import annual_full_retrain
    except Exception as exc:  # noqa: BLE001
        logger.warning("annual-retrain: import failed (%s)", exc)
        return
    try:
        annual_full_retrain()
    except Exception as exc:  # noqa: BLE001
        logger.warning("annual-retrain: run failed: %s", exc)


def _job_pre_season() -> None:
    """Generate pre-season forecast on first Monday of September."""

    logger.info("scheduler: PRE_SEASON job fired")
    try:
        from pipelines.report_generator import generate_pre_season_content
        try:
            from api import trader_whatsapp  # noqa: F401
        except Exception:  # noqa: BLE001
            trader_whatsapp = None  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        logger.warning("PRE_SEASON: import failed (%s)", exc)
        return

    for commodity, region in (
        ("Mango Alphonso", "Ratnagiri/Devgad"),
        ("Pomegranate", "Solapur/Nashik"),
    ):
        try:
            content = generate_pre_season_content(
                commodity=commodity,
                region=region,
                bearing_year="ON",
                belt_ndvi=0.62,
                vs_3yr_avg=8.5,
                expected_volume_mt=12000,
                peak_week="April Week 3",
            )
            if trader_whatsapp is not None:
                send_fn = getattr(trader_whatsapp, "send_pre_season", None)
                if callable(send_fn):
                    send_fn(commodity=commodity, content=content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PRE_SEASON failed for %s: %s", commodity, exc)


#: JH pre-season commodity list (each entry maps a Jharkhand mango variety to
#: its main belt region). The cron fires on the first Monday of February so
#: traders see the JH harvest forecast 8-12 weeks before the April-May start.
PRE_SEASON_JH_COMMODITIES: tuple[tuple[str, str], ...] = (
    ("Mango Mallika", "Jharkhand (Godda/Deoghar/Dumka)"),
    ("Mango Amrapali", "Jharkhand (Ranchi/Hazaribagh)"),
    ("Mango Jardalu", "Jharkhand/Bihar (Bhagalpur GI belt)"),
    ("Mango Himsagar", "Jharkhand/West Bengal (Sahebganj/Malda)"),
    ("Mango Langra_JH", "Jharkhand (Godda/Dumka/Deoghar)"),
)


def _job_pre_season_jh() -> None:
    """Generate the Jharkhand pre-season forecast on the first Monday of
    February. Same engine as ``_job_pre_season``; only the timing + commodity
    scope differ (April-May harvest start in JH vs March-April in MH)."""

    logger.info("scheduler: PRE_SEASON_JH job fired")
    try:
        from pipelines.report_generator import generate_pre_season_content
        try:
            from api import trader_whatsapp  # noqa: F401
        except Exception:  # noqa: BLE001
            trader_whatsapp = None  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        logger.warning("PRE_SEASON_JH: import failed (%s)", exc)
        return

    for commodity, region in PRE_SEASON_JH_COMMODITIES:
        try:
            content = generate_pre_season_content(
                commodity=commodity,
                region=region,
                bearing_year="ON",
                belt_ndvi=0.58,
                vs_3yr_avg=6.0,
                expected_volume_mt=8500,
                peak_week="May Week 2",
            )
            if trader_whatsapp is not None:
                send_fn = getattr(trader_whatsapp, "send_pre_season", None)
                if callable(send_fn):
                    send_fn(commodity=commodity, content=content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PRE_SEASON_JH failed for %s: %s", commodity, exc)


# --------------------------------------------------------------------------- #
# Scheduler builder
# --------------------------------------------------------------------------- #
def build_scheduler(timezone: str = "Asia/Kolkata") -> Any:
    """Return a configured (but NOT started) BackgroundScheduler.

    Raises RuntimeError if apscheduler is not installed - kept as a hard
    dependency only for the production deploy, not for unit tests.
    """

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "APScheduler not installed - pip install apscheduler"
        ) from exc

    scheduler = BackgroundScheduler(timezone=timezone)

    for job in JOB_DEFINITIONS:
        func_path = job["func"]
        module_name, attr = func_path.split(":")
        # late-import resolved by APScheduler when fired; pass the dotted path
        if job["trigger"] == "cron":
            kwargs = dict(job["trigger_kwargs"])
            kwargs["timezone"] = timezone
            trigger = CronTrigger(**kwargs)
        elif job["trigger"] == "interval":
            ikwargs = dict(job["trigger_kwargs"])
            start_hour = ikwargs.pop("start_hour", None)
            end_hour = ikwargs.pop("end_hour", None)
            if start_hour is not None and end_hour is not None:
                # Encode the windowed interval as a CronTrigger so APScheduler
                # only fires within the active hours. ``hour="6-20/2"`` runs at
                # 6,8,10,...,20. The original `minute="*/60"` was invalid
                # (minute step must be <= 59) and prevented the scheduler from
                # starting at all.
                step_hours = int(ikwargs.get("hours", 2) or 2)
                trigger = CronTrigger(
                    hour=f"{start_hour}-{end_hour}/{step_hours}",
                    minute="0",
                    timezone=timezone,
                )
            else:
                trigger = IntervalTrigger(**ikwargs, timezone=timezone)
        else:
            raise ValueError(f"unknown trigger: {job['trigger']}")

        scheduler.add_job(
            func_path, trigger=trigger, id=job["id"],
            name=job["description"], replace_existing=True,
        )

    return scheduler


# --------------------------------------------------------------------------- #
# Bagaan Sathi marketplace job callables (SDD §9)
# --------------------------------------------------------------------------- #


def _job_daily_matching() -> None:
    logger.info("scheduler: DAILY_MATCHING job fired")
    try:
        from pipelines.matching_engine import run_daily_matching
    except Exception as exc:  # noqa: BLE001
        logger.warning("DAILY_MATCHING: import failed (%s)", exc)
        return
    try:
        summary = run_daily_matching()
        logger.info("DAILY_MATCHING summary: %s", summary)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DAILY_MATCHING run failed: %s", exc)


def _job_plant_priority_queue() -> None:
    logger.info("scheduler: PLANT_PRIORITY_QUEUE job fired")
    try:
        from pipelines.plant_supply_queue import run_plant_priority_queue
    except Exception as exc:  # noqa: BLE001
        logger.warning("PLANT_PRIORITY_QUEUE: import failed (%s)", exc)
        return
    try:
        summary = run_plant_priority_queue()
        logger.info("PLANT_PRIORITY_QUEUE summary: %s", summary)
    except Exception as exc:  # noqa: BLE001
        logger.warning("PLANT_PRIORITY_QUEUE run failed: %s", exc)


def _job_weekly_aggregation() -> None:
    logger.info("scheduler: WEEKLY_AGGREGATION job fired")
    try:
        from pipelines.lot_aggregator import run_weekly_aggregation
    except Exception as exc:  # noqa: BLE001
        logger.warning("WEEKLY_AGGREGATION: import failed (%s)", exc)
        return
    try:
        summary = run_weekly_aggregation()
        logger.info(
            "WEEKLY_AGGREGATION created %d aggregations",
            summary.get("aggregations_created", 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("WEEKLY_AGGREGATION run failed: %s", exc)


def _job_lot_expiry_cleanup() -> None:
    """Mark expired farmer_lots and trader_requirements as EXPIRED."""
    logger.info("scheduler: LOT_EXPIRY_CLEANUP job fired")
    try:
        from api import whatsapp_db
        from datetime import datetime as _dt, timezone as _tz

        now = _dt.now(_tz.utc).isoformat()
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if whatsapp_db._table_exists(conn, "farmer_lots"):  # noqa: SLF001
                conn.execute(
                    "UPDATE farmer_lots SET status = 'EXPIRED' "
                    "WHERE expires_at IS NOT NULL AND expires_at < ? "
                    "AND status IN ('AVAILABLE','PARTIALLY_MATCHED','MATCHED')",
                    (now,),
                )
            if whatsapp_db._table_exists(conn, "trader_requirements"):  # noqa: SLF001
                conn.execute(
                    "UPDATE trader_requirements SET status = 'EXPIRED' "
                    "WHERE expires_at IS NOT NULL AND expires_at < ? "
                    "AND status = 'ACTIVE'",
                    (now,),
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LOT_EXPIRY_CLEANUP failed: %s", exc)


def _job_trade_confirmation_followup() -> None:
    """Send confirmation WhatsApp to farmer + trader for trades that are
    7 days old and unconfirmed (SDD §4.7)."""
    logger.info("scheduler: TRADE_CONFIRMATION_FOLLOWUP job fired")
    try:
        from api import whatsapp_db
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        cutoff = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if not whatsapp_db._table_exists(conn, "lot_matches"):  # noqa: SLF001
                return
            cur = conn.execute(
                "SELECT id, farmer_id, trader_id FROM lot_matches "
                "WHERE connection_made = 1 AND connection_made_at <= ? "
                "AND id NOT IN (SELECT match_id FROM farmer_trades "
                "WHERE match_id IS NOT NULL)",
                (cutoff,),
            )
            matches = [dict(row) for row in cur.fetchall()] if hasattr(cur, "fetchall") else []

        try:
            from api import marketplace_whatsapp

            send_req = getattr(
                marketplace_whatsapp, "send_trade_confirmation_request", None
            )
            for m in matches:
                if callable(send_req):
                    send_req(match_id=m.get("id"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("trade confirmation dispatch skipped: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TRADE_CONFIRMATION_FOLLOWUP failed: %s", exc)


__all__ = [
    "JOB_DEFINITIONS",
    "PRE_SEASON_JH_COMMODITIES",
    "build_scheduler",
    "_job_weekly_report",
    "_job_daily_update",
    "_job_flash_check",
    "_job_pre_season",
    "_job_pre_season_jh",
    "_job_keepalive_ping",
    "_job_weekly_new_data_retrain",
    "_job_monthly_mape_drift_check",
    "_job_annual_full_retrain",
    "_job_daily_matching",
    "_job_plant_priority_queue",
    "_job_weekly_aggregation",
    "_job_lot_expiry_cleanup",
    "_job_trade_confirmation_followup",
]
