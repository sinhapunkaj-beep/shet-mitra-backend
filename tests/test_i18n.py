"""Unit tests for :mod:`pipelines.i18n` — SDD §6 (language + region routing)."""

from __future__ import annotations

import pytest

from pipelines import i18n


@pytest.fixture(autouse=True)
def _reset_cache():
    """Force every test to re-read translation files from disk."""
    i18n._clear_translation_cache()
    yield
    i18n._clear_translation_cache()


# --------------------------------------------------------------------------- #
# Translation loading
# --------------------------------------------------------------------------- #

def test_marathi_translations_load():
    table = i18n.load_translations("Marathi")
    assert "greeting" in table
    greeting = table["greeting"]
    # Devanagari "नमस्ते" (Marathi) marks the file as Devanagari content.
    assert "नमस्ते" in greeting
    assert "{name}" in greeting


def test_english_translations_load():
    table = i18n.load_translations("English")
    assert "greeting" in table
    assert "Hello" in table["greeting"]
    assert "{name}" in table["greeting"]


def test_konkani_translations_load():
    """SDD §6.1 example must be preserved verbatim."""
    table = i18n.load_translations("Konkani")
    assert table["greeting"] == "नमस्कार {name} जी! 🌿"


# --------------------------------------------------------------------------- #
# get_message resolution
# --------------------------------------------------------------------------- #

def test_get_message_with_substitution():
    result = i18n.get_message("greeting", "Marathi", name="Ramesh")
    assert "Ramesh" in result
    assert "नमस्ते" in result
    assert "{name}" not in result


def test_get_message_unknown_language_falls_back_to_marathi():
    """Unknown languages must resolve via the Marathi table."""
    marathi_value = i18n.load_translations("Marathi")["greeting"]
    expected = marathi_value.format(name="Ramesh")
    result = i18n.get_message("greeting", "Klingon", name="Ramesh")
    assert result == expected


def test_get_message_unknown_key_returns_key():
    """Missing keys never raise — they return the key string itself."""
    result = i18n.get_message("does_not_exist", "Marathi")
    assert result == "does_not_exist"


# --------------------------------------------------------------------------- #
# Konkan bbox detection (SDD §6.2)
# --------------------------------------------------------------------------- #

def test_detect_konkan_ratnagiri_coords():
    """Ratnagiri (~17.0, 73.3) sits inside the Konkan bbox."""
    assert i18n.detect_konkan_region(17.0, 73.3) is True


def test_detect_konkan_outside_returns_false():
    """Pune (18.5, 73.8) — lat above the Konkan bbox ceiling (18.0)."""
    assert i18n.detect_konkan_region(18.5, 73.8) is False


def test_suggest_language_konkan_returns_konkani():
    assert i18n.suggest_language_from_centroid(17.0, 73.3) == "Konkani"


def test_suggest_language_outside_returns_marathi():
    # Nashik area (~20.0, 75.5) is outside the Konkan bbox.
    assert i18n.suggest_language_from_centroid(20.0, 75.5) == "Marathi"
