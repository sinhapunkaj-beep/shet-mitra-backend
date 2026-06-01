"""geo/gi_verifier.py — SDD §3.3

Jardalu GI (Geographical Indication) eligibility check for Bagaan Sathi.

A plot qualifies for the Jardalu GI premium (1.60×) when **all three**
checks pass:

1. The plot centroid lies inside the Jardalu GI bounding box
   (Godda/Bhagalpur belt — bbox from ``variety_config.Mango.Jardalu``).
2. The most recent AMED reading on the plot confirmed ``Mango`` with
   crop-type confidence ≥ 0.80.
3. The farmer is registered as growing the ``Jardalu`` variety.

The class is designed to fail soft: any data-access exception collapses
to ``gi_eligible=False`` with a descriptive ``reason`` field, never an
uncaught error — sender / report paths must never crash on a GI lookup.

Public API
----------
    GIVerifier()
        .verify_jardalu(plot_id)             -> dict
        .get_gi_certificate_text(farmer_id, plot_id) -> Optional[str]

Module-level helpers
--------------------
    get_gi_badge(plot_id) -> str
        Returns ``""`` when not GI-eligible, else the Jardalu GI badge
        line used by all farmer-side WhatsApp templates.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
VARIETY_CONFIG_PATH = REPO_ROOT / "data" / "variety_config.json"

# Static fallback if variety_config.json is unreadable. Matches SDD §3.2.
_DEFAULT_JARDALU_BBOX = {
    "north": 25.0,
    "south": 24.0,
    "east": 87.5,
    "west": 86.5,
}

#: Minimum AMED crop-type confidence required to certify Mango (SDD §3.3).
AMED_MANGO_CONFIDENCE_THRESHOLD = 0.80

#: Multiplier applied to the prevailing mandi price when a lot carries
#: a valid Jardalu GI certificate. Comes directly from SDD §3.3.
GI_PREMIUM_MULTIPLIER = 1.60


# --------------------------------------------------------------------------- #
# Data access shims — overridable by tests via monkeypatch
# --------------------------------------------------------------------------- #


def _get_plot(plot_id: str) -> Optional[dict]:
    """Resolve ``plot_id`` → ``farm_plots`` row dict.

    Returns ``None`` on any error so callers can treat the plot as
    ineligible without crashing.
    """
    try:
        from api import whatsapp_db  # local import keeps module standalone
    except Exception as exc:  # noqa: BLE001
        LOG.debug("gi_verifier: whatsapp_db import failed: %s", exc)
        return None
    try:
        return whatsapp_db.get_plot_by_id(plot_id)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("gi_verifier: get_plot_by_id(%s) failed: %s", plot_id, exc)
        return None


def _get_latest_amed_reading(plot_id: str) -> Optional[dict]:
    """Return the most recent ``amed_readings`` row for ``plot_id``.

    The schema differs slightly between dev (SQLite mirror) and prod
    (Supabase) — we read whichever columns exist and tolerate missing
    fields by mapping them to ``None``.
    """
    try:
        from api import whatsapp_db
    except Exception:  # noqa: BLE001
        return None

    try:
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if not whatsapp_db._table_exists(conn, "amed_readings"):  # noqa: SLF001
                return None
            cur = conn.execute(
                "SELECT * FROM amed_readings WHERE plot_id = ? "
                "ORDER BY captured_at DESC LIMIT 1",
                (plot_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {key: row[key] for key in row.keys()}
    except Exception as exc:  # noqa: BLE001
        LOG.debug("gi_verifier: amed lookup failed for %s: %s", plot_id, exc)
        return None


def _get_farmer(farmer_id: str) -> Optional[dict]:
    try:
        from api import whatsapp_db
    except Exception:  # noqa: BLE001
        return None
    try:
        return whatsapp_db.get_farmer_by_id(farmer_id)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("gi_verifier: get_farmer_by_id(%s) failed: %s", farmer_id, exc)
        return None


# --------------------------------------------------------------------------- #
# Variety config loader (cached, mtime-aware)
# --------------------------------------------------------------------------- #

_VARIETY_CFG_CACHE: dict[str, Any] = {"mtime": None, "data": None}


def _load_jardalu_bbox() -> dict:
    """Read ``Mango.Jardalu.gi_zone_bbox`` from variety_config.json.

    Falls back to :data:`_DEFAULT_JARDALU_BBOX` if the file is missing,
    malformed, or lacks the key.
    """
    path = VARIETY_CONFIG_PATH
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return dict(_DEFAULT_JARDALU_BBOX)

    cache = _VARIETY_CFG_CACHE
    if cache["mtime"] == mtime and cache["data"] is not None:
        return cache["data"]

    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        bbox = cfg.get("Mango", {}).get("Jardalu", {}).get("gi_zone_bbox") or {}
        if not all(k in bbox for k in ("north", "south", "east", "west")):
            bbox = dict(_DEFAULT_JARDALU_BBOX)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        LOG.warning("gi_verifier: variety_config load failed: %s", exc)
        bbox = dict(_DEFAULT_JARDALU_BBOX)

    cache["mtime"] = mtime
    cache["data"] = bbox
    return bbox


# --------------------------------------------------------------------------- #
# GIVerifier class
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _PlotCoords:
    lat: float
    lng: float


class GIVerifier:
    """Verify Jardalu GI eligibility (SDD §3.3)."""

    def __init__(
        self,
        plot_loader: Optional[Callable[[str], Optional[dict]]] = None,
        amed_loader: Optional[Callable[[str], Optional[dict]]] = None,
        farmer_loader: Optional[Callable[[str], Optional[dict]]] = None,
    ) -> None:
        # Resolve at construction time so monkeypatch on the module-level
        # shims is honoured by code that doesn't pass explicit loaders.
        import sys as _sys  # local — avoids polluting module namespace
        _mod = _sys.modules[__name__]
        self._get_plot = plot_loader or _mod._get_plot
        self._get_amed = amed_loader or _mod._get_latest_amed_reading
        self._get_farmer = farmer_loader or _mod._get_farmer

    # --------------------------------------------------------------------- #
    # Core verification
    # --------------------------------------------------------------------- #

    def verify_jardalu(self, plot_id: str) -> dict:
        """Return the SDD §3.3 verification dict for ``plot_id``.

        Keys (always present):
          - ``gi_eligible``       : bool
          - ``in_gi_zone``        : bool
          - ``amed_confirmed``    : bool
          - ``variety_registered``: bool
          - ``certificate_ref``   : str | None
          - ``premium_multiplier``: float (1.60 if eligible, else 1.0)
          - ``reason``            : str (human-readable, for debugging)
        """
        result = {
            "gi_eligible": False,
            "in_gi_zone": False,
            "amed_confirmed": False,
            "variety_registered": False,
            "certificate_ref": None,
            "premium_multiplier": 1.0,
            "reason": "",
        }

        plot = self._get_plot(plot_id)
        if not plot:
            result["reason"] = "plot not found"
            return result

        coords = self._extract_coords(plot)
        if coords is None:
            result["reason"] = "plot centroid coordinates missing"
            return result

        bbox = _load_jardalu_bbox()
        in_zone = (
            bbox["south"] <= coords.lat <= bbox["north"]
            and bbox["west"] <= coords.lng <= bbox["east"]
        )
        result["in_gi_zone"] = in_zone

        amed = self._get_amed(plot_id) or {}
        crop_type = (amed.get("crop_type_detected") or "").strip().lower()
        try:
            confidence = float(amed.get("crop_type_confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        amed_confirmed = (
            crop_type == "mango"
            and confidence >= AMED_MANGO_CONFIDENCE_THRESHOLD
        )
        result["amed_confirmed"] = amed_confirmed

        farmer_id = plot.get("farmer_id") or plot.get("farmer")
        variety_registered = False
        if farmer_id:
            farmer = self._get_farmer(str(farmer_id)) or {}
            current = (farmer.get("current_crop_variety") or "").strip().lower()
            variety_registered = current == "jardalu"
        result["variety_registered"] = variety_registered

        if in_zone and amed_confirmed and variety_registered:
            result["gi_eligible"] = True
            result["certificate_ref"] = f"SKI-GI-{str(plot_id)[:8]}"
            result["premium_multiplier"] = GI_PREMIUM_MULTIPLIER
            result["reason"] = "ok"
        else:
            missing = []
            if not in_zone:
                missing.append("outside GI zone")
            if not amed_confirmed:
                missing.append("AMED did not confirm Mango")
            if not variety_registered:
                missing.append("farmer not registered as Jardalu")
            result["reason"] = "; ".join(missing) or "not eligible"

        return result

    # --------------------------------------------------------------------- #
    # Certificate rendering
    # --------------------------------------------------------------------- #

    def get_gi_certificate_text(
        self, farmer_id: str, plot_id: str
    ) -> Optional[str]:
        """Return the human-readable GI certificate, or ``None`` if not eligible."""
        result = self.verify_jardalu(plot_id)
        if not result["gi_eligible"]:
            return None

        farmer = self._get_farmer(farmer_id) or {}
        name = (
            farmer.get("farmer_full_name")
            or farmer.get("full_name")
            or farmer.get("name")
            or "—"
        )
        village = farmer.get("village") or "—"
        district = farmer.get("district") or "—"

        return (
            "JARDALU GI VERIFICATION CERTIFICATE\n"
            "Bagaan Sathi | Sahyadri Krushi Intelligence\n"
            "\n"
            f"Farmer: {name}\n"
            f"Village: {village}, {district}\n"
            f"Certificate: {result['certificate_ref']}\n"
            "\n"
            "[OK] GI Zone: Confirmed (Godda/Bhagalpur belt)\n"
            "[OK] Crop: AMED satellite verified Mango\n"
            "[OK] Variety: Jardalu (farmer registered)\n"
            "\n"
            "This lot is eligible for Jardalu GI premium pricing."
        )

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #

    @staticmethod
    def _extract_coords(plot: dict) -> Optional[_PlotCoords]:
        """Pull centroid lat/lng from a plot row regardless of column naming."""
        lat = plot.get("centroid_lat") or plot.get("lat") or plot.get("latitude")
        lng = (
            plot.get("centroid_lng")
            or plot.get("centroid_lon")
            or plot.get("lng")
            or plot.get("lon")
            or plot.get("longitude")
        )
        if lat is None or lng is None:
            return None
        try:
            return _PlotCoords(lat=float(lat), lng=float(lng))
        except (TypeError, ValueError):
            return None


# --------------------------------------------------------------------------- #
# Module-level convenience helpers
# --------------------------------------------------------------------------- #


def get_gi_badge(plot_id: str) -> str:
    """Return the Jardalu GI badge line, or empty string.

    Used by the farmer-side WhatsApp templates in
    :mod:`api.marketplace_whatsapp`. Never raises — a GI lookup must
    never block a message send.
    """
    try:
        verifier = GIVerifier()
        result = verifier.verify_jardalu(plot_id)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("get_gi_badge failed for %s: %s", plot_id, exc)
        return ""
    if not result.get("gi_eligible"):
        return ""
    multiplier = result.get("premium_multiplier", GI_PREMIUM_MULTIPLIER)
    return (
        "[GI] Jardalu GI Certified -- Premium "
        f"{multiplier:.2f}x"
    )


__all__ = [
    "GIVerifier",
    "get_gi_badge",
    "GI_PREMIUM_MULTIPLIER",
    "AMED_MANGO_CONFIDENCE_THRESHOLD",
]
