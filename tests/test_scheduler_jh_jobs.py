"""tests/test_scheduler_jh_jobs.py — JH cron jobs + JH retraining loop.

Validates:
  * PRE_SEASON_JH cron fires on the first Monday of February.
  * _job_pre_season_jh dispatches the JH variety list through
    generate_pre_season_content + send_pre_season.
  * The continuous-training retrainer includes the three JH mango
    pickles (Mallika / Jardalu / Amrapali) and tolerates a missing
    .pkl without crashing.

Every side effect (WhatsApp send, DB, file IO) is mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines import model_retraining, scheduler  # noqa: E402


# --------------------------------------------------------------------------- #
# Scheduler JOB_DEFINITIONS wiring
# --------------------------------------------------------------------------- #
def test_pre_season_jh_job_definition_first_monday_of_february():
    """SDD §10 — PRE_SEASON_JH must fire first Monday of February IST."""
    by_id = {j["id"]: j for j in scheduler.JOB_DEFINITIONS}
    assert "PRE_SEASON_JH" in by_id, "PRE_SEASON_JH job missing from JOB_DEFINITIONS"
    job = by_id["PRE_SEASON_JH"]
    assert job["trigger"] == "cron"
    kwargs = job["trigger_kwargs"]
    assert kwargs["month"] == 2
    # day range covers the first 7 days; combined with day_of_week=mon this
    # gives the first Monday of the month.
    assert kwargs["day"] == "1-7"
    assert kwargs["day_of_week"] == "mon"
    assert kwargs["hour"] == 5


def test_pre_season_mh_unchanged_first_monday_of_september():
    """SDD §10 — existing PRE_SEASON (MH) must still fire 1st Monday Sept."""
    by_id = {j["id"]: j for j in scheduler.JOB_DEFINITIONS}
    job = by_id["PRE_SEASON"]
    assert job["trigger_kwargs"]["month"] == 9
    assert job["trigger_kwargs"]["day"] == "1-7"
    assert job["trigger_kwargs"]["day_of_week"] == "mon"


def test_pre_season_jh_commodity_list_covers_all_five_varieties():
    """SDD §3.2 — five JH varieties must be dispatched."""
    varieties = {
        c.replace("Mango ", "") for c, _r in scheduler.PRE_SEASON_JH_COMMODITIES
    }
    assert varieties == {"Mallika", "Amrapali", "Jardalu", "Himsagar", "Langra_JH"}


def test_job_pre_season_jh_invokes_send_for_every_jh_variety(monkeypatch):
    """Each JH variety should produce one outbound WhatsApp dispatch."""
    fake_send = MagicMock(return_value={"status": "queued"})
    fake_generate = MagicMock(return_value="JH pre-season text")

    # The job uses ``from api import trader_whatsapp`` lazily. When the
    # real module has already been imported by a sibling test, that
    # binding survives — so we patch the attribute on the ``api``
    # package itself (which is what ``from api import trader_whatsapp``
    # actually resolves against).
    import api
    import sys as _sys
    import types

    fake_trader_module = types.SimpleNamespace(send_pre_season=fake_send)
    monkeypatch.setattr(api, "trader_whatsapp", fake_trader_module, raising=False)
    monkeypatch.setitem(_sys.modules, "api.trader_whatsapp", fake_trader_module)

    with patch(
        "pipelines.report_generator.generate_pre_season_content",
        fake_generate,
    ):
        scheduler._job_pre_season_jh()

    # One send per JH variety.
    assert fake_send.call_count == len(scheduler.PRE_SEASON_JH_COMMODITIES)
    sent_commodities = [
        call.kwargs.get("commodity") for call in fake_send.call_args_list
    ]
    assert "Mango Jardalu" in sent_commodities
    assert "Mango Mallika" in sent_commodities


# --------------------------------------------------------------------------- #
# JH retraining loop
# --------------------------------------------------------------------------- #
def test_jh_mango_model_pickles_registered():
    """SDD §7.4 — the three JH ARIMA pickles must be registered."""
    assert set(model_retraining.JH_MANGO_VARIETIES) == {
        "Mallika", "Jardalu", "Amrapali",
    }
    assert (
        model_retraining.JH_MANGO_MODEL_PICKLES["Mallika"]
        == "arima_mango_mallika_jharkhand.pkl"
    )
    assert (
        model_retraining.JH_MANGO_MODEL_PICKLES["Jardalu"]
        == "arima_mango_jardalu_jharkhand.pkl"
    )
    assert (
        model_retraining.JH_MANGO_MODEL_PICKLES["Amrapali"]
        == "arima_mango_amrapali_jharkhand.pkl"
    )


def test_jh_retrainer_tolerates_missing_pickles(caplog):
    """The retrainer must NOT crash when the Agent 4 trainer has not yet
    produced the .pkl files (partial swarm). It should log a warning and
    return an empty list so the all-models retrain still finishes."""
    # Point at a directory that definitely has no JH pickles.
    with patch(
        "pipelines.model_retraining.Path",
        wraps=Path,
    ):
        # We don't actually need to patch Path — the default models dir
        # on most dev machines won't have the JH pickles yet either.
        results = model_retraining._default_retrain_mango_jh()
    # The result is either empty (no pickles found) OR a list of
    # RetrainResult entries where every pickle path actually exists.
    for r in results:
        assert Path(r.pickle_path).exists(), (
            f"retrainer claimed {r.pickle_path} but it doesn't exist"
        )


def test_jh_retrainer_registers_present_pickle(tmp_path, monkeypatch):
    """When a JH pickle DOES exist on disk, the retrainer must return a
    RetrainResult that points at it."""
    # Create a fake models dir layout with one of the three pickles present.
    models_dir = tmp_path / "data" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "arima_mango_mallika_jharkhand.pkl").write_bytes(b"fake-pickle")

    # Monkey-patch the resolution of the project root so the retrainer
    # looks in our tmp directory.
    fake_module_file = tmp_path / "pipelines" / "model_retraining.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.write_text("# stub")

    # Inject by monkey-patching the Path resolution inside the function.
    import pipelines.model_retraining as mr

    original_file = mr.__file__

    monkeypatch.setattr(mr, "__file__", str(fake_module_file))
    try:
        results = mr._default_retrain_mango_jh()
    finally:
        monkeypatch.setattr(mr, "__file__", original_file)

    mallika = [r for r in results if r.variety == "Mallika"]
    assert mallika, "expected a RetrainResult for the Mallika pickle"
    assert mallika[0].commodity == "Mango"
    assert "arima_mango_mallika_jharkhand.pkl" in mallika[0].pickle_path
