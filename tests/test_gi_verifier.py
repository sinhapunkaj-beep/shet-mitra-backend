"""tests/test_gi_verifier.py — SDD §3.3

Unit tests for the Jardalu GI verifier. All data accessors are stubbed
via the GIVerifier constructor — no DB or AMED reading required.
"""

from __future__ import annotations

import pytest

from geo import gi_verifier
from geo.gi_verifier import (
    AMED_MANGO_CONFIDENCE_THRESHOLD,
    GI_PREMIUM_MULTIPLIER,
    GIVerifier,
    get_gi_badge,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_verifier(plot=None, amed=None, farmer=None):
    return GIVerifier(
        plot_loader=lambda _pid: plot,
        amed_loader=lambda _pid: amed,
        farmer_loader=lambda _fid: farmer,
    )


_IN_ZONE_PLOT = {
    "id": "plot-abc12345",
    "farmer_id": "farmer-x",
    "centroid_lat": 24.5,
    "centroid_lng": 87.0,
}

_OUT_ZONE_PLOT = {
    "id": "plot-out-zone",
    "farmer_id": "farmer-x",
    "centroid_lat": 23.0,   # below Jardalu bbox south=24.0
    "centroid_lng": 87.0,
}

_AMED_MANGO = {"crop_type_detected": "Mango", "crop_type_confidence": 0.91}
_AMED_LOW_CONF = {"crop_type_detected": "Mango", "crop_type_confidence": 0.5}
_AMED_OTHER = {"crop_type_detected": "Rice", "crop_type_confidence": 0.95}

_FARMER_JARDALU = {
    "id": "farmer-x",
    "farmer_full_name": "Ravi Kumar",
    "village": "Mahagama",
    "district": "Godda",
    "current_crop_variety": "Jardalu",
}
_FARMER_OTHER = dict(_FARMER_JARDALU, current_crop_variety="Mallika")


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_in_zone_jardalu_with_amed_confirmation_is_eligible():
    verifier = _make_verifier(
        plot=_IN_ZONE_PLOT, amed=_AMED_MANGO, farmer=_FARMER_JARDALU
    )

    result = verifier.verify_jardalu("plot-abc12345")

    assert result["gi_eligible"] is True
    assert result["in_gi_zone"] is True
    assert result["amed_confirmed"] is True
    assert result["variety_registered"] is True
    assert result["certificate_ref"] == "SKI-GI-plot-abc"
    assert result["premium_multiplier"] == GI_PREMIUM_MULTIPLIER
    assert result["reason"] == "ok"


def test_certificate_text_contains_all_required_fields():
    verifier = _make_verifier(
        plot=_IN_ZONE_PLOT, amed=_AMED_MANGO, farmer=_FARMER_JARDALU
    )

    text = verifier.get_gi_certificate_text(
        farmer_id="farmer-x", plot_id="plot-abc12345"
    )

    assert text is not None
    assert "JARDALU GI VERIFICATION CERTIFICATE" in text
    assert "Bagaan Sathi" in text
    assert "Ravi Kumar" in text
    assert "Mahagama" in text
    assert "Godda" in text
    assert "SKI-GI-plot-abc" in text
    assert "GI Zone: Confirmed" in text


# --------------------------------------------------------------------------- #
# Negative paths — each of the three checks isolated
# --------------------------------------------------------------------------- #


def test_outside_gi_zone_is_not_eligible():
    verifier = _make_verifier(
        plot=_OUT_ZONE_PLOT, amed=_AMED_MANGO, farmer=_FARMER_JARDALU
    )

    result = verifier.verify_jardalu("plot-out-zone")

    assert result["gi_eligible"] is False
    assert result["in_gi_zone"] is False
    assert result["certificate_ref"] is None
    assert result["premium_multiplier"] == 1.0
    assert "outside GI zone" in result["reason"]


def test_low_amed_confidence_blocks_eligibility():
    verifier = _make_verifier(
        plot=_IN_ZONE_PLOT, amed=_AMED_LOW_CONF, farmer=_FARMER_JARDALU
    )

    result = verifier.verify_jardalu("plot-abc12345")

    assert result["gi_eligible"] is False
    assert result["amed_confirmed"] is False
    assert "AMED did not confirm Mango" in result["reason"]


def test_amed_wrong_crop_blocks_eligibility():
    verifier = _make_verifier(
        plot=_IN_ZONE_PLOT, amed=_AMED_OTHER, farmer=_FARMER_JARDALU
    )

    result = verifier.verify_jardalu("plot-abc12345")

    assert result["gi_eligible"] is False
    assert result["amed_confirmed"] is False


def test_non_jardalu_variety_blocks_eligibility():
    verifier = _make_verifier(
        plot=_IN_ZONE_PLOT, amed=_AMED_MANGO, farmer=_FARMER_OTHER
    )

    result = verifier.verify_jardalu("plot-abc12345")

    assert result["gi_eligible"] is False
    assert result["variety_registered"] is False
    assert "not registered as Jardalu" in result["reason"]


# --------------------------------------------------------------------------- #
# Edge cases & fail-soft behaviour
# --------------------------------------------------------------------------- #


def test_missing_plot_returns_not_eligible_without_raising():
    verifier = _make_verifier(plot=None, amed=None, farmer=None)

    result = verifier.verify_jardalu("ghost-plot")

    assert result["gi_eligible"] is False
    assert result["reason"] == "plot not found"


def test_plot_without_coordinates_returns_not_eligible():
    verifier = _make_verifier(
        plot={"id": "p", "farmer_id": "f"},
        amed=_AMED_MANGO,
        farmer=_FARMER_JARDALU,
    )

    result = verifier.verify_jardalu("p")

    assert result["gi_eligible"] is False
    assert "coordinates missing" in result["reason"]


def test_certificate_returns_none_when_not_eligible():
    verifier = _make_verifier(
        plot=_OUT_ZONE_PLOT, amed=_AMED_MANGO, farmer=_FARMER_JARDALU
    )

    text = verifier.get_gi_certificate_text("farmer-x", "plot-out-zone")

    assert text is None


def test_get_gi_badge_returns_empty_string_for_failing_lookup(monkeypatch):
    """The module-level helper must swallow any error and return empty."""
    def _broken_loader(_):
        raise RuntimeError("simulated DB outage")

    # Patch the default loader so the verifier constructed inside
    # get_gi_badge sees the breakage.
    monkeypatch.setattr(gi_verifier, "_get_plot", _broken_loader)

    badge = get_gi_badge("plot-abc12345")

    assert badge == ""


def test_get_gi_badge_returns_eligible_badge(monkeypatch):
    monkeypatch.setattr(gi_verifier, "_get_plot", lambda _: _IN_ZONE_PLOT)
    monkeypatch.setattr(gi_verifier, "_get_latest_amed_reading", lambda _: _AMED_MANGO)
    monkeypatch.setattr(gi_verifier, "_get_farmer", lambda _: _FARMER_JARDALU)

    badge = get_gi_badge("plot-abc12345")

    assert "Jardalu GI Certified" in badge
    assert f"{GI_PREMIUM_MULTIPLIER:.2f}" in badge


def test_amed_threshold_constant_matches_sdd():
    assert AMED_MANGO_CONFIDENCE_THRESHOLD == 0.80


def test_bbox_boundary_inclusive():
    """A plot sitting exactly on the bbox edge counts as in-zone."""
    plot_on_edge = dict(_IN_ZONE_PLOT, centroid_lat=24.0, centroid_lng=86.5)
    verifier = _make_verifier(
        plot=plot_on_edge, amed=_AMED_MANGO, farmer=_FARMER_JARDALU
    )

    result = verifier.verify_jardalu("plot-edge")

    assert result["in_gi_zone"] is True
    assert result["gi_eligible"] is True
