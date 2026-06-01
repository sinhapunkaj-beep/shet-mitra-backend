"""tests/test_bagaan_sathi.py — SDD §10/§11 integration coverage.

Cross-module sanity checks that the Jharkhand region build (Bagaan Sathi)
is wired end-to-end:
    * JH region routes to Hindi.
    * JH farmer resolves to "Bagaan Sathi" sender.
    * Jardalu GI verification round-trips.
    * Jharkhand AMED bboxes are present in the environment.
    * Variety config carries the 5 Jharkhand mango varieties.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import whatsapp_sender
from geo.gi_verifier import GIVerifier, get_gi_badge
from pipelines import i18n


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_caches():
    whatsapp_sender.reset_region_cache()
    i18n._clear_translation_cache()
    yield
    whatsapp_sender.reset_region_cache()
    i18n._clear_translation_cache()


# --------------------------------------------------------------------------- #
# Region routing
# --------------------------------------------------------------------------- #


def test_jh_region_resolves_to_hindi_language():
    assert i18n.language_for_region("JH") == "Hindi"


def test_mh_region_resolves_to_marathi_language():
    assert i18n.language_for_region("MH") == "Marathi"


def test_jh_farmer_gets_bagaan_sathi_sender(monkeypatch):
    monkeypatch.setattr(
        whatsapp_sender, "_lookup_region_code_for_farmer", lambda fid: "JH"
    )
    monkeypatch.setattr(
        whatsapp_sender, "_lookup_sender_for_region",
        lambda code: {"MH": "ShetMitra", "JH": "Bagaan Sathi"}.get(code),
    )
    assert whatsapp_sender.get_sender_name("farmer-jh-99") == "Bagaan Sathi"


def test_jh_report_footer_contains_bagaan_sathi(monkeypatch):
    monkeypatch.setattr(
        whatsapp_sender, "_lookup_region_code_for_farmer", lambda fid: "JH"
    )
    monkeypatch.setattr(
        whatsapp_sender, "_lookup_sender_for_region",
        lambda code: "Bagaan Sathi",
    )
    footer = whatsapp_sender.get_report_footer("f-jh-100")
    assert "Bagaan Sathi" in footer
    assert "Sahyadri Krushi Intelligence" in footer


# --------------------------------------------------------------------------- #
# Jardalu GI
# --------------------------------------------------------------------------- #


_FARMER = {
    "id": "f-x", "farmer_full_name": "Anita Devi",
    "village": "Mahagama", "district": "Godda",
    "current_crop_variety": "Jardalu",
}


def test_jardalu_in_zone_with_amed_returns_eligible():
    plot = {"id": "p-1", "farmer_id": "f-x",
            "centroid_lat": 24.5, "centroid_lng": 87.0}
    amed = {"crop_type_detected": "Mango", "crop_type_confidence": 0.93}
    verifier = GIVerifier(
        plot_loader=lambda _: plot,
        amed_loader=lambda _: amed,
        farmer_loader=lambda _: _FARMER,
    )
    result = verifier.verify_jardalu("p-1")
    assert result["gi_eligible"] is True
    assert result["certificate_ref"].startswith("SKI-GI-")
    assert result["premium_multiplier"] == 1.60


def test_jardalu_outside_zone_is_not_eligible():
    plot = {"id": "p-2", "farmer_id": "f-x",
            "centroid_lat": 22.0, "centroid_lng": 85.0}
    amed = {"crop_type_detected": "Mango", "crop_type_confidence": 0.93}
    verifier = GIVerifier(
        plot_loader=lambda _: plot,
        amed_loader=lambda _: amed,
        farmer_loader=lambda _: _FARMER,
    )
    result = verifier.verify_jardalu("p-2")
    assert result["gi_eligible"] is False
    assert "outside GI zone" in result["reason"]


def test_gi_badge_renders_when_eligible(monkeypatch):
    from geo import gi_verifier as gv
    monkeypatch.setattr(gv, "_get_plot", lambda _: {
        "id": "p-9", "farmer_id": "f-x",
        "centroid_lat": 24.5, "centroid_lng": 87.0,
    })
    monkeypatch.setattr(gv, "_get_latest_amed_reading",
                        lambda _: {"crop_type_detected": "Mango",
                                   "crop_type_confidence": 0.95})
    monkeypatch.setattr(gv, "_get_farmer", lambda _: _FARMER)

    badge = get_gi_badge("p-9")
    assert "Jardalu GI Certified" in badge


# --------------------------------------------------------------------------- #
# Config + env wiring
# --------------------------------------------------------------------------- #


def test_jharkhand_amed_bboxes_present_in_env():
    """SDD §3.5 — Jharkhand + Bihar bboxes must be configured."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env not present in this dev environment")
    content = env_path.read_text(encoding="utf-8")
    for key in (
        "AMED_BBOX_JHARKHAND_NORTH",
        "AMED_BBOX_JHARKHAND_SOUTH",
        "AMED_BBOX_JHARKHAND_EAST",
        "AMED_BBOX_JHARKHAND_WEST",
        "AMED_BBOX_BIHAR_NORTH",
    ):
        assert key in content, f"missing {key} in .env"


def test_jharkhand_mango_varieties_in_config():
    path = REPO_ROOT / "data" / "variety_config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    mango = cfg["Mango"]
    for variety in ("Mallika", "Amrapali", "Jardalu", "Himsagar", "Langra_JH"):
        assert variety in mango
        assert mango[variety]["region_code"] == "JH"
        assert mango[variety]["language_hint"] == "Hindi"


def test_jardalu_is_gi_certification_eligible_in_config():
    path = REPO_ROOT / "data" / "variety_config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    jardalu = cfg["Mango"]["Jardalu"]
    assert jardalu.get("gi_certification_eligible") is True
    assert jardalu.get("gi_tagged") is True
    assert jardalu.get("price_premium_pct") >= 50
    assert "export_markets" in jardalu


def test_hindi_translation_file_exists():
    path = REPO_ROOT / "data" / "translations" / "hindi.json"
    assert path.exists()
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert "greeting" in cfg
    assert "नमस्ते" in cfg["greeting"]


# --------------------------------------------------------------------------- #
# Regions seed (SDD §2.1)
# --------------------------------------------------------------------------- #


def test_regions_route_returns_both_regions():
    """The fallback region list must include both MH and JH."""
    from routes.regions import _FALLBACK_REGIONS
    codes = {r["region_code"] for r in _FALLBACK_REGIONS}
    assert {"MH", "JH"} <= codes
    jh = next(r for r in _FALLBACK_REGIONS if r["region_code"] == "JH")
    assert jh["whatsapp_sender_name"] == "Bagaan Sathi"
    assert jh["default_language"] == "Hindi"
    assert "Bhagalpur APMC" in jh["primary_mandis"]
