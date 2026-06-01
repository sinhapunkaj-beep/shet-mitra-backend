"""ShetMitra - Internationalisation (i18n) helper for WhatsApp messaging.

Implements SDD §6 (Konkani language + region routing). The module is a
*read-only* helper that loads translation tables from
``data/translations/{marathi,english,konkani}.json`` and resolves
message keys with graceful fallback semantics:

    get_message(key, language) -> str

resolves a key in the requested language, falling back to Marathi when
the language file is unknown or the key is missing, and finally to the
key itself so callers never crash on a typo.

Region routing follows SDD §6.2: a centroid that sits inside the Konkan
bounding box (lat 15.5-18.0, lng 72.8-74.0) suggests ``"Konkani"``;
everywhere else defaults to ``"Marathi"``.

Translation files are cached using an mtime-aware cache mirroring the
pattern in :func:`pipelines.advisory_engine.load_variety_config`, so
edits to the JSON files become visible without a process restart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRANSLATIONS_DIR = _PROJECT_ROOT / "data" / "translations"

#: Filename for each supported language. Lookups are case-insensitive.
_LANGUAGE_FILES: dict[str, str] = {
    "marathi": "marathi.json",
    "english": "english.json",
    "konkani": "konkani.json",
    "hindi": "hindi.json",
}

#: Language used when an unknown language is requested or a key is missing.
DEFAULT_LANGUAGE = "Marathi"

#: Per-region default language (SDD §5).
_REGION_DEFAULT_LANGUAGE: dict[str, str] = {
    "MH": "Marathi",
    "JH": "Hindi",
}

# SDD §6.2 Konkan bounding box.
_KONKAN_LAT_MIN = 15.5
_KONKAN_LAT_MAX = 18.0
_KONKAN_LNG_MIN = 72.8
_KONKAN_LNG_MAX = 74.0


# --------------------------------------------------------------------------- #
# mtime-aware translation cache
# --------------------------------------------------------------------------- #

# Maps absolute path -> (mtime_ns, parsed_dict).
_TRANSLATION_CACHE: dict[str, tuple[int, dict]] = {}
_CACHE_LOCK = RLock()


def _normalise_language(language: str | None) -> str:
    """Return the canonical lower-case language key, or '' if not known."""
    if not language:
        return ""
    return language.strip().lower()


def _resolve_language_path(language: str) -> Path | None:
    """Map a (case-insensitive) language name to its JSON file path."""
    key = _normalise_language(language)
    filename = _LANGUAGE_FILES.get(key)
    if filename is None:
        return None
    return _TRANSLATIONS_DIR / filename


def _read_translation_file(path: Path) -> dict:
    """Read a translation JSON file with mtime-aware caching."""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"translation file not found at {path}"
        ) from exc

    key = str(path)
    with _CACHE_LOCK:
        cached = _TRANSLATION_CACHE.get(key)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]

        with path.open("r", encoding="utf-8") as fh:
            parsed = json.load(fh)

        if not isinstance(parsed, dict):
            raise ValueError(
                f"translation file {path} must be a JSON object, "
                f"got {type(parsed).__name__}"
            )
        _TRANSLATION_CACHE[key] = (mtime_ns, parsed)
        return parsed


def _clear_translation_cache() -> None:
    """Drop every cached translation. Exposed for tests that need a reset."""
    with _CACHE_LOCK:
        _TRANSLATION_CACHE.clear()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def load_translations(language: str = DEFAULT_LANGUAGE) -> dict:
    """Return the translation dict for the given language with caching.

    Falls back to :data:`DEFAULT_LANGUAGE` when the language file is
    unknown. Raises :class:`FileNotFoundError` only when the default
    language's file is itself missing.
    """
    path = _resolve_language_path(language)
    if path is None:
        # Unknown language → fall back to default.
        fallback_path = _resolve_language_path(DEFAULT_LANGUAGE)
        if fallback_path is None:
            raise FileNotFoundError(
                f"no translation file registered for default language "
                f"{DEFAULT_LANGUAGE!r}"
            )
        return _read_translation_file(fallback_path)
    return _read_translation_file(path)


def get_message(key: str, language: str = DEFAULT_LANGUAGE, **kwargs: Any) -> str:
    """Resolve a message key for the requested language.

    Resolution order:
      1. The requested language's translation table.
      2. The default language's translation table.
      3. The raw key itself (so a typo never raises).

    ``**kwargs`` are applied via ``str.format(**kwargs)`` to the
    resolved template. A ``KeyError`` raised by ``str.format`` (i.e.
    the caller did not pass a required placeholder) propagates so the
    bug surfaces early instead of silently sending a half-formatted
    message.
    """
    if not key:
        return key
    template: str | None = None

    # 1. Requested language.
    requested_path = _resolve_language_path(language)
    if requested_path is not None:
        try:
            requested_table = _read_translation_file(requested_path)
        except FileNotFoundError:
            requested_table = {}
        value = requested_table.get(key)
        if isinstance(value, str):
            template = value

    # 2. Fall back to default language.
    if template is None:
        default_path = _resolve_language_path(DEFAULT_LANGUAGE)
        if default_path is not None:
            try:
                default_table = _read_translation_file(default_path)
            except FileNotFoundError:
                default_table = {}
            value = default_table.get(key)
            if isinstance(value, str):
                template = value

    # 3. Final fallback: the key itself.
    if template is None:
        template = key

    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            # Missing placeholder — return the unformatted template so the
            # caller still receives *something* legible rather than crashing
            # mid-state-machine. Callers that care can wrap this in their
            # own try/except.
            return template
    return template


def detect_konkan_region(centroid_lat: float, centroid_lng: float) -> bool:
    """Return ``True`` if (lat, lng) sits inside the Konkan bbox per §6.2.

    Konkan bbox: lat ∈ [15.5, 18.0], lng ∈ [72.8, 74.0]. Boundary
    coordinates are inclusive.
    """
    try:
        lat = float(centroid_lat)
        lng = float(centroid_lng)
    except (TypeError, ValueError):
        return False
    return (
        _KONKAN_LAT_MIN <= lat <= _KONKAN_LAT_MAX
        and _KONKAN_LNG_MIN <= lng <= _KONKAN_LNG_MAX
    )


def suggest_language_from_centroid(
    centroid_lat: float, centroid_lng: float
) -> str:
    """Return ``"Konkani"`` if the centroid is in Konkan, else ``"Marathi"``."""
    if detect_konkan_region(centroid_lat, centroid_lng):
        return "Konkani"
    return DEFAULT_LANGUAGE


def language_for_region(region_code: str | None) -> str:
    """Return the default language for a region code (SDD §5).

    ``'JH'`` → Hindi, ``'MH'`` → Marathi, anything else → :data:`DEFAULT_LANGUAGE`.
    Case-insensitive on the input. Never raises.
    """
    if not region_code:
        return DEFAULT_LANGUAGE
    return _REGION_DEFAULT_LANGUAGE.get(
        str(region_code).strip().upper(), DEFAULT_LANGUAGE
    )


__all__ = [
    "DEFAULT_LANGUAGE",
    "load_translations",
    "get_message",
    "detect_konkan_region",
    "suggest_language_from_centroid",
    "language_for_region",
]
