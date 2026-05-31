"""Tests for the continuous-training system (pipelines/model_retraining.py).

We cover:
  - all 4 triggers' decision logic (fires when condition met, skips otherwise)
  - model_registry versioning (insert + flip prior is_active)
  - cron_run_log audit
  - scheduler JOB_DEFINITIONS contains the 3 new cron jobs
  - GET /models/registry returns the snapshot
  - harvest webhook trigger hook (Trigger 3) is idempotent per day
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.seed_local_sqlite import (
    build_local_db, ensure_continuous_training_schema,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def ct_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with the continuous-training schema."""
    db = tmp_path / "ct.db"
    build_local_db(db)
    monkeypatch.setenv("SHETMITRA_DB_PATH", str(db))
    # Reload the module so DEFAULT_DB_PATH picks up the env var.
    import importlib, pipelines.model_retraining as mr
    importlib.reload(mr)
    yield db


def _frozen_clock(dt: datetime):
    return lambda: dt


def _insert_price_rows(db: Path, *, commodity: str, n: int, when: datetime):
    """Insert n price_history_training rows dated at ``when`` with a
    matching created_at column (the trigger checks created_at first)."""
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    # The table exists in the SQLite mirror via build_local_db's seed.
    try:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS price_history_training "
            "(id TEXT, commodity TEXT, market_name TEXT, modal_price REAL, "
            " date TEXT, created_at TEXT)"
        )
    except sqlite3.OperationalError:
        pass
    for i in range(n):
        cur.execute(
            "INSERT INTO price_history_training "
            "(id, commodity, market_name, modal_price, date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), commodity, "Tasgaon",
             125.0 + i, when.date().isoformat(), when.isoformat()),
        )
    conn.commit()
    conn.close()


def _stub_retrain(commodity_results):
    """Build a retrain_fn that returns the supplied RetrainResult list."""
    def _fn(trigger: str):
        return list(commodity_results)
    return _fn


# ---------------------------------------------------------------------------
# Trigger 1 — Weekly new-data check
# ---------------------------------------------------------------------------
def test_weekly_skips_when_fewer_than_50_new_rows(ct_db):
    from pipelines.model_retraining import check_and_retrain_if_new_data

    now = datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc)
    _insert_price_rows(ct_db, commodity="Dry Grapes", n=20,
                       when=now - timedelta(days=2))
    sent = []
    result = check_and_retrain_if_new_data(
        clock=_frozen_clock(now),
        retrain_fn=lambda t: pytest.fail("retrain should NOT be called"),
        alert_fn=lambda body: sent.append(body) or {"ok": True},
    )
    assert result["action"] == "skipped"
    assert result["new_rows"] == 20
    assert sent == []

    # cron_run_log row should exist with status=skipped.
    conn = sqlite3.connect(ct_db)
    rows = conn.execute(
        "SELECT status, reason FROM cron_run_log "
        "WHERE job_id='WEEKLY_NEW_DATA_RETRAIN'"
    ).fetchall()
    conn.close()
    assert rows and rows[0][0] == "skipped" and "20 new rows" in rows[0][1]


def test_weekly_retrains_when_50_or_more_new_rows(ct_db):
    from pipelines.model_retraining import (
        check_and_retrain_if_new_data, RetrainResult,
    )
    now = datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc)
    _insert_price_rows(ct_db, commodity="Pomegranate", n=60,
                       when=now - timedelta(days=3))
    sent = []
    fake = RetrainResult(commodity="Pomegranate", variety=None,
                         model_version="v3", model_type="random_forest",
                         mape=18.5, training_rows=4900)
    result = check_and_retrain_if_new_data(
        clock=_frozen_clock(now),
        retrain_fn=_stub_retrain([fake]),
        alert_fn=lambda body: sent.append(body) or {"ok": True},
    )
    assert result["action"] == "retrained"
    assert result["new_rows"] == 60
    assert any(m["mape"] == 18.5 for m in result["models"])
    assert sent and "60 new price rows" in sent[0]

    # model_registry should have an active row.
    conn = sqlite3.connect(ct_db)
    rows = conn.execute(
        "SELECT commodity, mape, is_active, retrain_trigger "
        "FROM model_registry WHERE commodity='Pomegranate'"
    ).fetchall()
    conn.close()
    assert rows and rows[0][1] == pytest.approx(18.5)
    assert int(rows[0][2]) == 1
    assert rows[0][3] == "weekly_new_data"


def test_weekly_only_counts_last_7_days(ct_db):
    from pipelines.model_retraining import check_and_retrain_if_new_data

    now = datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc)
    # 100 ancient rows + 10 fresh — only the 10 should count.
    _insert_price_rows(ct_db, commodity="Dry Grapes", n=100,
                       when=now - timedelta(days=30))
    _insert_price_rows(ct_db, commodity="Dry Grapes", n=10,
                       when=now - timedelta(days=2))
    result = check_and_retrain_if_new_data(
        clock=_frozen_clock(now),
        retrain_fn=lambda t: pytest.fail("retrain should NOT fire"),
        alert_fn=lambda body: {"ok": True},
    )
    assert result["action"] == "skipped"
    assert result["new_rows"] == 10


# ---------------------------------------------------------------------------
# Trigger 2 — Monthly rolling-MAPE drift
# ---------------------------------------------------------------------------
def _seed_predictions_and_actuals(db: Path, *, commodity: str,
                                  pred: float, actual: float, days_ago: int):
    """Insert paired prediction + actual rows for MAPE math."""
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date()
    next_day = when + timedelta(days=1)
    cur.execute(
        "INSERT INTO intelligence_reports (id, report_type, commodity, "
        " report_date, content_english, price_forecast_day1, model_version) "
        "VALUES (?, 'WEEKLY', ?, ?, '', ?, 'v3')",
        (str(uuid.uuid4()), commodity, when.isoformat(), pred),
    )
    cur.execute(
        "INSERT INTO price_history_training "
        "(id, commodity, market_name, modal_price, date, created_at) "
        "VALUES (?, ?, 'Tasgaon', ?, ?, ?)",
        (str(uuid.uuid4()), commodity, actual,
         next_day.isoformat(), next_day.isoformat()),
    )
    conn.commit()
    conn.close()


def _ensure_phr_table(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS price_history_training "
        "(id TEXT, commodity TEXT, market_name TEXT, modal_price REAL, "
        " date TEXT, created_at TEXT)"
    )
    conn.commit(); conn.close()


def test_monthly_mape_skips_when_within_baseline(ct_db):
    from pipelines.model_retraining import check_rolling_mape
    _ensure_phr_table(ct_db)
    # Dry_Grapes baseline 12.39%. Pair prediction 120 / actual 122 = ~1.6% error.
    conn = sqlite3.connect(ct_db)
    conn.execute(
        "INSERT INTO intelligence_reports (id, report_type, commodity, "
        " report_date, content_english, price_forecast_day1, model_version) "
        "VALUES (?, 'WEEKLY', 'Dry_Grapes', date('now', '-5 day'), '', 120.0, 'v3')",
        (str(uuid.uuid4()),)
    )
    conn.execute(
        "INSERT INTO price_history_training "
        "(id, commodity, market_name, modal_price, date, created_at) "
        "VALUES (?, 'Dry_Grapes', 'Tasgaon', 122.0, date('now','-4 day'), "
        " datetime('now','-4 day'))",
        (str(uuid.uuid4()),)
    )
    conn.commit()
    conn.close()
    result = check_rolling_mape(
        retrain_fn=lambda t: pytest.fail("should NOT retrain on stable model"),
        alert_fn=lambda body: {"ok": True},
    )
    assert result["action"] == "skipped"


def test_monthly_mape_retrains_when_drift_exceeds_threshold(ct_db):
    from pipelines.model_retraining import check_rolling_mape, RetrainResult
    _ensure_phr_table(ct_db)
    # Inject a 40% error for Dry_Grapes (baseline 12.39, threshold mult 1.25
    # -> 15.5%); 40% is clearly over.
    conn = sqlite3.connect(ct_db)
    for _ in range(5):
        conn.execute(
            "INSERT INTO intelligence_reports (id, report_type, commodity, "
            " report_date, content_english, price_forecast_day1, model_version) "
            "VALUES (?, 'WEEKLY', 'Dry_Grapes', date('now', '-5 day'), '', "
            " 100.0, 'v3')",
            (str(uuid.uuid4()),)
        )
        conn.execute(
            "INSERT INTO price_history_training "
            "(id, commodity, market_name, modal_price, date, created_at) "
            "VALUES (?, 'Dry_Grapes', 'Tasgaon', 140.0, date('now','-4 day'), "
            " datetime('now','-4 day'))",
            (str(uuid.uuid4()),)
        )
    conn.commit()
    conn.close()
    sent = []
    fake = RetrainResult(commodity="Dry_Grapes", variety=None,
                         model_version="v3", model_type="random_forest",
                         mape=10.2, training_rows=700)
    result = check_rolling_mape(
        retrain_fn=_stub_retrain([fake]),
        alert_fn=lambda body: sent.append(body) or {"ok": True},
    )
    assert result["action"] == "retrained"
    assert sent and "Auto-retrain triggered" in sent[0]
    conn = sqlite3.connect(ct_db)
    row = conn.execute(
        "SELECT mape, retrain_trigger FROM model_registry "
        "WHERE commodity='Dry_Grapes' AND is_active=1"
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(10.2)
    assert row[1] == "monthly_mape_drift"


# ---------------------------------------------------------------------------
# Trigger 3 — Harvest actuals (idempotent per day)
# ---------------------------------------------------------------------------
def _seed_harvest_actuals(db: Path, n: int, when: datetime):
    conn = sqlite3.connect(db)
    for _ in range(n):
        conn.execute(
            "INSERT INTO farm_harvest_actuals "
            "(id, farmer_id, plot_id, season_label, crop_type, status, "
            " collection_completed_at, created_at) "
            "VALUES (?, ?, ?, '2026-rabi', 'Grapes', 'COMPLETE', ?, ?)",
            (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()),
             when.isoformat(), when.isoformat())
        )
    conn.commit()
    conn.close()


def test_harvest_trigger_skips_below_threshold(ct_db):
    from pipelines.model_retraining import retrain_on_harvest_actuals
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    _seed_harvest_actuals(ct_db, 5, now)
    result = retrain_on_harvest_actuals(
        clock=_frozen_clock(now),
        retrain_fn=lambda t: pytest.fail("retrain should NOT fire"),
        alert_fn=lambda body: {"ok": True},
    )
    assert result["action"] == "skipped"
    assert result["today_count"] == 5


def test_harvest_trigger_fires_at_ten(ct_db):
    from pipelines.model_retraining import retrain_on_harvest_actuals, RetrainResult
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    _seed_harvest_actuals(ct_db, 10, now)
    fake = RetrainResult(commodity="Dry_Grapes", variety=None,
                         model_version="v3", model_type="random_forest",
                         mape=11.5, training_rows=600)
    sent = []
    result = retrain_on_harvest_actuals(
        clock=_frozen_clock(now),
        retrain_fn=_stub_retrain([fake]),
        alert_fn=lambda b: sent.append(b) or {"ok": True},
    )
    assert result["action"] == "retrained"
    assert result["today_count"] == 10
    assert sent and "10 new harvest actuals today" in sent[0]


def test_harvest_trigger_idempotent_same_day(ct_db):
    """Calling the trigger twice on the same UTC day must retrain only once."""
    from pipelines.model_retraining import retrain_on_harvest_actuals, RetrainResult
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    _seed_harvest_actuals(ct_db, 15, now)
    fake = RetrainResult(commodity="Dry_Grapes", variety=None,
                         model_version="v3", model_type="random_forest",
                         mape=11.5, training_rows=600)
    n_calls = {"x": 0}

    def _count(t):
        n_calls["x"] += 1
        return [fake]

    r1 = retrain_on_harvest_actuals(
        clock=_frozen_clock(now),
        retrain_fn=_count, alert_fn=lambda b: {"ok": True},
    )
    r2 = retrain_on_harvest_actuals(
        clock=_frozen_clock(now),
        retrain_fn=_count, alert_fn=lambda b: {"ok": True},
    )
    assert r1["action"] == "retrained"
    assert r2["action"] == "skipped"
    assert n_calls["x"] == 1


# ---------------------------------------------------------------------------
# Trigger 4 — Annual full retrain (Oct 1)
# ---------------------------------------------------------------------------
def test_annual_full_retrain_records_versions_and_alerts(ct_db):
    from pipelines.model_retraining import annual_full_retrain, RetrainResult
    fakes = [
        RetrainResult(commodity="Dry_Grapes", variety=None, model_version="v3",
                      model_type="random_forest", mape=10.5, training_rows=700),
        RetrainResult(commodity="Mango", variety="Alphonso", model_version="v3",
                      model_type="random_forest", mape=12.0, training_rows=800),
    ]
    sent = []
    result = annual_full_retrain(
        clock=_frozen_clock(datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)),
        retrain_fn=_stub_retrain(fakes),
        alert_fn=lambda b: sent.append(b) or {"ok": True},
    )
    assert result["action"] == "retrained"
    assert result["models"] == 2
    assert sent and "Annual full retrain complete" in sent[0]
    conn = sqlite3.connect(ct_db)
    n = conn.execute(
        "SELECT COUNT(*) FROM model_registry "
        "WHERE retrain_trigger = 'annual_full' AND is_active = 1"
    ).fetchone()[0]
    conn.close()
    assert n == 2


# ---------------------------------------------------------------------------
# Model registry versioning
# ---------------------------------------------------------------------------
def test_registry_versioning_flips_prior_is_active(ct_db):
    from pipelines.model_retraining import register_model_version, RetrainResult
    conn = sqlite3.connect(ct_db)
    register_model_version(
        conn,
        RetrainResult(commodity="Dry_Grapes", variety=None, model_version="v2",
                      model_type="random_forest", mape=17.34),
        retrain_trigger="manual",
    )
    register_model_version(
        conn,
        RetrainResult(commodity="Dry_Grapes", variety=None, model_version="v3",
                      model_type="random_forest", mape=12.39),
        retrain_trigger="manual",
    )
    rows = conn.execute(
        "SELECT model_version, is_active, mape FROM model_registry "
        "WHERE commodity='Dry_Grapes' ORDER BY created_at"
    ).fetchall()
    conn.close()
    versions = [r[0] for r in rows]
    actives = [int(r[1]) for r in rows]
    assert versions == ["v2", "v3"]
    assert actives == [0, 1]   # only newest active


def test_registry_distinguishes_varieties(ct_db):
    from pipelines.model_retraining import register_model_version, RetrainResult
    conn = sqlite3.connect(ct_db)
    register_model_version(
        conn,
        RetrainResult(commodity="Mango", variety="Alphonso",
                      model_version="v3", model_type="rf", mape=13.29),
        retrain_trigger="manual",
    )
    register_model_version(
        conn,
        RetrainResult(commodity="Mango", variety="Kesar",
                      model_version="v3", model_type="rf", mape=2.85),
        retrain_trigger="manual",
    )
    rows = conn.execute(
        "SELECT variety, is_active FROM model_registry "
        "WHERE commodity='Mango' ORDER BY variety"
    ).fetchall()
    conn.close()
    assert [(r[0], int(r[1])) for r in rows] == [("Alphonso", 1), ("Kesar", 1)]


# ---------------------------------------------------------------------------
# Scheduler wiring + endpoint
# ---------------------------------------------------------------------------
def test_scheduler_job_definitions_have_three_new_jobs():
    from pipelines.scheduler import JOB_DEFINITIONS
    ids = {j["id"] for j in JOB_DEFINITIONS}
    assert "WEEKLY_NEW_DATA_RETRAIN" in ids
    assert "MONTHLY_MAPE_DRIFT_CHECK" in ids
    assert "ANNUAL_FULL_RETRAIN" in ids
    # Cron kwargs sanity
    by_id = {j["id"]: j for j in JOB_DEFINITIONS}
    assert by_id["WEEKLY_NEW_DATA_RETRAIN"]["trigger_kwargs"]["day_of_week"] == "sun"
    assert by_id["MONTHLY_MAPE_DRIFT_CHECK"]["trigger_kwargs"]["day"] == 1
    assert by_id["ANNUAL_FULL_RETRAIN"]["trigger_kwargs"]["month"] == 10


def test_models_registry_endpoint_returns_grouped_snapshot(ct_db):
    from fastapi.testclient import TestClient
    from main import app
    from pipelines.model_retraining import register_model_version, RetrainResult

    conn = sqlite3.connect(ct_db)
    register_model_version(
        conn,
        RetrainResult(commodity="Dry_Grapes", variety=None, model_version="v3",
                      model_type="random_forest", mape=12.39, training_rows=700),
        retrain_trigger="manual",
    )
    conn.close()

    client = TestClient(app)
    resp = client.get("/models/registry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    dgs = [m for m in body["models"] if m["commodity"] == "Dry_Grapes"]
    assert dgs, "no Dry_Grapes entry returned"
    assert dgs[0]["active"]["mape"] == pytest.approx(12.39)
    assert "mape_series" in dgs[0]
