"""Revised AMED-primary / Sentinel-2-secondary daily pipeline.

Implements the orchestration described in SDD Section 4.1 and Section 6.
``AmedSentinelPipeline`` runs five steps:

  1. AMED field fetch (with 14-day cache)
  2. AMED belt fetch (only when the previous belt fetch is >= 14 days old)
  3. Sentinel-2 fetch (wrapped in try/except — the real service is a stub)
  4. Combine AMED + Sentinel-2 into a harvest window and combined health
  5. Crop and area mismatch detection per SDD Section 6.2

Downstream consumers (advisory engine, dashboard) receive the structured
result dict and decide what to do. The pipeline itself never raises on
upstream failures — every error is appended to ``result['errors']``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import random
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Mapping

try:
    # Provided by Agent 1 — import is wrapped so this file remains importable
    # in environments where Agent 1's work has not yet landed.
    from geo.amed_client import AMEDClient  # type: ignore
except Exception:  # pragma: no cover - exercised in unit tests via patching
    AMEDClient = None  # type: ignore[assignment]

from pipelines.cache import AmedCache, CACHE_TTL_DAYS, DEFAULT_DB_PATH
from pipelines.harvest_window import calculate_harvest_window, merge_signals


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REGION = "Tasgaon_Sangli_belt"
TASGAON_BBOX = {
    "north": float(os.getenv("AMED_BBOX_TASGAON_NORTH", "17.2")),
    "south": float(os.getenv("AMED_BBOX_TASGAON_SOUTH", "16.8")),
    "east": float(os.getenv("AMED_BBOX_TASGAON_EAST", "74.8")),
    "west": float(os.getenv("AMED_BBOX_TASGAON_WEST", "74.3")),
}

# Area mismatch threshold per SDD 6.2.
AREA_MISMATCH_PCT_THRESHOLD = 20.0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class AmedSentinelPipeline:
    """Orchestrator for the revised daily intelligence sequence."""

    def __init__(
        self,
        amed_client: Any | None = None,
        cache: AmedCache | None = None,
        db_path: str = DEFAULT_DB_PATH,
        region: str = DEFAULT_REGION,
    ) -> None:
        if amed_client is None:
            if AMEDClient is None:
                raise RuntimeError(
                    "AMEDClient unavailable. Provide an instance explicitly "
                    "or ensure geo.amed_client.AMEDClient is importable."
                )
            amed_client = AMEDClient()
        self.amed_client = amed_client
        self.cache = cache if cache is not None else AmedCache(db_path=db_path)
        self.db_path = db_path
        self.region = region

    # ------------------------------------------------------------------
    # Step 1 — AMED field fetch
    # ------------------------------------------------------------------

    async def fetch_amed_field(
        self,
        plot: Mapping[str, Any],
        errors: list[str],
    ) -> tuple[dict | None, bool]:
        """Return ``(amed_response, cache_hit)``.

        Falls back to ``(None, False)`` when the AMED client raises. The
        error is appended to ``errors`` so the caller can surface it.
        """
        plot_id = str(plot.get("id") or plot.get("plot_id") or "")
        if plot_id:
            cached = self.cache.get_field_cached(plot_id)
            if cached is not None:
                return cached, True

        try:
            polygon = plot.get("boundary_polygon") or plot.get("polygon")
            response = await _maybe_await(
                self.amed_client.get_field_data,
                polygon=polygon,
                plot_id=plot_id,
            )
        except Exception as exc:
            errors.append(f"amed_field_fetch_failed: {exc}")
            return None, False

        if response is None:
            errors.append("amed_field_fetch_returned_none")
            return None, False

        # Persist to cache for the next 14 days.
        if plot_id:
            try:
                self.cache.put_field(plot_id, response)
            except Exception as exc:  # pragma: no cover - defensive only
                errors.append(f"amed_field_cache_write_failed: {exc}")
        return dict(response), False

    # ------------------------------------------------------------------
    # Step 2 — AMED belt fetch
    # ------------------------------------------------------------------

    async def fetch_amed_belt(
        self,
        crop_type: str,
        errors: list[str],
    ) -> dict | None:
        """Return cached belt data when fresh, otherwise hit AMED."""
        cached = self.cache.get_belt_cached(self.region)
        if cached is not None:
            return cached

        try:
            response = await _maybe_await(
                self.amed_client.get_belt_data,
                bbox=TASGAON_BBOX,
                crop_type=crop_type,
            )
        except Exception as exc:
            errors.append(f"amed_belt_fetch_failed: {exc}")
            return None

        if response is None:
            errors.append("amed_belt_fetch_returned_none")
            return None

        try:
            self.cache.put_belt(self.region, response)
        except Exception as exc:  # pragma: no cover - defensive only
            errors.append(f"amed_belt_cache_write_failed: {exc}")
        return dict(response)

    # ------------------------------------------------------------------
    # Step 3 — Sentinel-2 fetch (stub-safe)
    # ------------------------------------------------------------------

    async def fetch_sentinel(
        self,
        plot: Mapping[str, Any],
        errors: list[str],
    ) -> dict | None:
        """Return a Sentinel-2 reading dict.

        Tries the real ``services.sentinel`` module first. Because that
        module is currently empty (per the project state) the call always
        falls through to :meth:`_fetch_sentinel_stub`. The stub is
        deterministic per plot so repeated runs in a test are stable.
        """
        try:
            from services import sentinel as sentinel_mod  # type: ignore

            fetch = getattr(sentinel_mod, "fetch_indices", None) or getattr(
                sentinel_mod, "fetch_sentinel_indices", None
            )
            if callable(fetch):
                return await _maybe_await(fetch, plot)
        except Exception as exc:
            errors.append(f"sentinel_fetch_failed: {exc}")

        # Fall through to synthetic stub.
        return self._fetch_sentinel_stub(plot)

    def _fetch_sentinel_stub(self, plot: Mapping[str, Any]) -> dict:
        """Deterministic synthetic NDVI/RECI/NDWI/LAI/NPP for development.

        Uses ``plot['id']`` as the random seed so a given plot always
        yields the same indices within a test run. ``cloud_cover_flag``
        defaults to False; if the plot dict explicitly sets it to True we
        honour it and the merge logic will reduce confidence per SDD 4.2.
        """
        seed_source = str(plot.get("id") or plot.get("plot_id") or "stub")
        rng = random.Random(seed_source)

        ndvi = round(0.55 + rng.uniform(-0.15, 0.25), 3)
        reci = round(1.6 + rng.uniform(-0.4, 1.2), 3)
        ndwi = round(0.15 + rng.uniform(-0.20, 0.25), 3)
        lai = round(2.4 + rng.uniform(-0.6, 1.4), 3)
        npp = round(1100 + rng.uniform(-150, 220), 1)

        cloud_cover_flag = bool(plot.get("cloud_cover_flag", False))

        # Estimate a 30-day NDVI trend so harvest_window can refine the AMED
        # date. A slightly negative slope mimics late-season decline.
        trend_slope = round(rng.uniform(-0.0075, -0.0010), 5)
        if cloud_cover_flag:
            quality = 0.6
        else:
            quality = round(0.80 + rng.uniform(-0.05, 0.15), 3)

        return {
            "ndvi": ndvi,
            "reci": reci,
            "ndwi": ndwi,
            "lai": lai,
            "npp": npp,
            "cloud_cover_flag": cloud_cover_flag,
            "ndvi_30day_trend": {
                "slope": trend_slope,
                "quality": quality,
                "cloud_cover_flag": cloud_cover_flag,
            },
            "source": "stub",
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------
    # Step 5 — Mismatch detection
    # ------------------------------------------------------------------

    def detect_mismatches(
        self,
        plot: Mapping[str, Any],
        amed: Mapping[str, Any],
    ) -> list[dict]:
        """Detect crop and area mismatches per SDD Section 6.2."""
        mismatches: list[dict] = []

        registered_crop = (plot.get("current_crop") or "").strip()
        amed_crop = (amed.get("crop_type") or "").strip()
        if registered_crop and amed_crop and registered_crop.lower() != amed_crop.lower():
            mismatches.append(
                {
                    "type": "crop_type",
                    "plot_id": plot.get("id") or plot.get("plot_id"),
                    "registered": registered_crop,
                    "detected": amed_crop,
                    "message": (
                        f"Crop mismatch: registered as {registered_crop} but "
                        f"AMED detects {amed_crop}"
                    ),
                }
            )

        registered_area = plot.get("area_acres")
        amed_area = amed.get("field_size_acres")
        if (
            isinstance(registered_area, (int, float))
            and isinstance(amed_area, (int, float))
            and registered_area > 0
        ):
            diff_pct = abs(float(amed_area) - float(registered_area)) / float(
                registered_area
            ) * 100.0
            if diff_pct > AREA_MISMATCH_PCT_THRESHOLD:
                mismatches.append(
                    {
                        "type": "area",
                        "plot_id": plot.get("id") or plot.get("plot_id"),
                        "registered_acres": float(registered_area),
                        "detected_acres": float(amed_area),
                        "area_mismatch_pct": round(diff_pct, 2),
                        "message": (
                            f"Area mismatch: registered {registered_area} acres but "
                            f"AMED detects {amed_area} acres ({diff_pct:.1f}% diff)"
                        ),
                    }
                )

        return mismatches

    def _flag_mismatches_in_db(self, mismatches: list[dict]) -> bool:
        """Write mismatch flags to ``farm_plots`` if SQLite is available.

        Returns ``True`` when at least one UPDATE actually ran.
        """
        if not mismatches:
            return False
        if not os.path.exists(self.db_path):
            return False

        crop_flag = any(m["type"] == "crop_type" for m in mismatches)
        area_entry = next((m for m in mismatches if m["type"] == "area"), None)
        plot_id = mismatches[0].get("plot_id")
        if not plot_id:
            return False

        try:
            conn = sqlite3.connect(self.db_path)
        except sqlite3.Error:
            return False
        try:
            cur = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='farm_plots'"
            )
            if cur.fetchone() is None:
                return False

            sets = []
            params: list[Any] = []
            if crop_flag:
                sets.append("crop_type_mismatch = ?")
                params.append(1)
            if area_entry is not None:
                sets.append("area_mismatch_pct = ?")
                params.append(area_entry["area_mismatch_pct"])
            sets.append("amed_last_fetch = ?")
            params.append(datetime.now().date().isoformat())
            params.append(str(plot_id))
            conn.execute(
                f"UPDATE farm_plots SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Step 4 — Combine signals (used by run_full_pipeline)
    # ------------------------------------------------------------------

    def _combine(
        self,
        amed: Mapping[str, Any] | None,
        sentinel: Mapping[str, Any] | None,
        errors: list[str],
    ) -> tuple[dict | None, dict, str, float]:
        """Build the harvest window and combined-health summary.

        Returns ``(harvest_window, combined_health, harvest_source, confidence)``.
        ``harvest_source`` is ``'amed_confirmed'`` when AMED supplied the
        date and ``'ndvi_estimate'`` when we had to fall back.
        """
        sentinel = sentinel or {}
        ndvi_trend = sentinel.get("ndvi_30day_trend") if sentinel else None

        amed_harvest = amed.get("harvest_date_predicted") if amed else None
        amed_confidence = float(amed.get("crop_confidence", 0.0)) if amed else 0.0

        harvest_window: dict | None = None
        if amed_harvest:
            try:
                window_start, window_end, confidence = calculate_harvest_window(
                    amed_harvest, ndvi_trend, amed_confidence
                )
                harvest_window = {
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                    "confidence": confidence,
                }
                harvest_source = "amed_confirmed"
            except Exception as exc:
                errors.append(f"harvest_window_calc_failed: {exc}")
                harvest_window = None
                harvest_source = "ndvi_estimate"
                confidence = 0.4
        else:
            # NDVI-only fallback: rough window of today+14..today+28.
            today = datetime.now().date()
            harvest_window = {
                "start": (today + timedelta(days=14)).isoformat(),
                "end": (today + timedelta(days=28)).isoformat(),
                "confidence": 0.4,
            }
            harvest_source = "ndvi_estimate"
            confidence = 0.4

        combined = merge_signals(
            amed.get("growth_stage") if amed else None,
            sentinel.get("ndvi"),
            sentinel.get("reci"),
            sentinel.get("ndwi"),
        )
        return harvest_window, combined, harvest_source, confidence

    # ------------------------------------------------------------------
    # Step 4b — Mango phenology branch (SDD §2.2-§2.3, Agent 4)
    # ------------------------------------------------------------------

    def _run_mango_phenology(
        self,
        plot: Mapping[str, Any],
        amed: Mapping[str, Any] | None,
        sentinel: Mapping[str, Any] | None,
        errors: list[str],
    ) -> dict | None:
        """Detect mango-specific phenology events and persist them.

        Wrapped in try/except by the caller — failures in this branch
        never break the surrounding pipeline (per Agent 4 spec).
        """
        from pipelines import mango_phenology  # local import — additive only

        result: dict[str, Any] = {
            "bearing_year": None,
            "bearing_confidence": None,
            "flowering_detected": False,
            "flowering_detected_date": None,
            "fruit_set_detected": False,
            "fruit_set_detected_date": None,
            "heat_stress_events_count": 0,
            "water_status": None,
        }

        sentinel = sentinel or {}
        amed = amed or {}
        plot_id = str(plot.get("id") or plot.get("plot_id") or "")

        # ----- Bearing year (Aug-Sep NDVI mean) --------------------------
        aug_sep_ndvi = (
            plot.get("aug_sep_ndvi_mean")
            or amed.get("aug_sep_ndvi_mean")
            or sentinel.get("aug_sep_ndvi_mean")
        )
        if aug_sep_ndvi is not None:
            try:
                history = plot.get("aug_sep_ndvi_history") or []
                bearing, bconf = mango_phenology.detect_bearing_year(
                    float(aug_sep_ndvi), history=list(history) or None
                )
                result["bearing_year"] = bearing
                result["bearing_confidence"] = bconf
            except Exception as exc:
                errors.append(f"mango_bearing_detect_failed: {exc}")

        # ----- Flowering / fruit set (monthly NDVI series) ---------------
        monthly_ndvi = (
            plot.get("monthly_ndvi")
            or sentinel.get("monthly_ndvi")
            or []
        )
        today = datetime.now().date()
        current_month = today.month
        if monthly_ndvi:
            try:
                series = [
                    (
                        d if isinstance(d, date) else date.fromisoformat(str(d)),
                        float(v),
                    )
                    for d, v in monthly_ndvi
                ]
            except Exception as exc:
                series = []
                errors.append(f"mango_monthly_ndvi_parse_failed: {exc}")

            # Flowering (Dec/Jan/Feb).
            if current_month in (12, 1, 2):
                try:
                    flowered, peak_date = mango_phenology.detect_flowering(
                        series, current_month=current_month
                    )
                    result["flowering_detected"] = flowered
                    result["flowering_detected_date"] = (
                        peak_date.isoformat() if peak_date else None
                    )
                except Exception as exc:
                    errors.append(f"mango_flowering_detect_failed: {exc}")

            # Fruit set (Feb/Mar).
            if current_month in (2, 3):
                try:
                    reci_trend = (
                        sentinel.get("reci_trend")
                        or sentinel.get("reci")
                        or 0.0
                    )
                    set_flag, set_date = mango_phenology.detect_fruit_set(
                        series, float(reci_trend)
                    )
                    result["fruit_set_detected"] = set_flag
                    result["fruit_set_detected_date"] = (
                        set_date.isoformat() if set_date else None
                    )
                except Exception as exc:
                    errors.append(f"mango_fruit_set_detect_failed: {exc}")

        # ----- Heat stress (Feb-Mar temps > 38C) -------------------------
        weather_temps = (
            plot.get("weather_temps")
            or plot.get("temps")
            or sentinel.get("weather_temps")
            or []
        )
        if weather_temps:
            try:
                normed = [
                    (
                        d if isinstance(d, date) else date.fromisoformat(str(d)),
                        float(t),
                    )
                    for d, t in weather_temps
                ]
                result["heat_stress_events_count"] = (
                    mango_phenology.detect_heat_stress(normed)
                )
            except Exception as exc:
                errors.append(f"mango_heat_stress_detect_failed: {exc}")

        # ----- Water status ----------------------------------------------
        ndwi = sentinel.get("ndwi")
        if ndwi is not None:
            try:
                result["water_status"] = mango_phenology.assess_water_status(
                    float(ndwi)
                )
            except Exception as exc:
                errors.append(f"mango_water_status_failed: {exc}")

        # ----- Persistence (best-effort, SQLite mirror) ------------------
        if plot_id and os.path.exists(self.db_path):
            try:
                self._persist_mango_phenology(plot_id, result)
            except Exception as exc:
                errors.append(f"mango_phenology_persist_failed: {exc}")

        return result

    def _persist_mango_phenology(self, plot_id: str, mango: Mapping[str, Any]) -> None:
        """Upsert mango_phenology_log + update farm_plots when SQLite mirror has the schema.

        No-op when the tables are absent (Agent 1 hasn't run yet) — this
        keeps the dev environment safe before the migration lands.
        """
        try:
            conn = sqlite3.connect(self.db_path)
        except sqlite3.Error:
            return

        try:
            # Detect schema readiness.
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name IN ('mango_phenology_log','farm_plots')"
            )
            tables = {row[0] for row in cur.fetchall()}

            today_iso = datetime.now().date().isoformat()
            season_label = self._mango_season_label(datetime.now().date())

            if "mango_phenology_log" in tables:
                # Check whether the row exists; if so update, else insert.
                cur = conn.execute(
                    "SELECT id FROM mango_phenology_log "
                    "WHERE plot_id = ? AND season_label = ?",
                    (plot_id, season_label),
                )
                row = cur.fetchone()
                fields = {
                    "bearing_year": mango.get("bearing_year") or "UNKNOWN",
                    "flowering_peak_date": mango.get("flowering_detected_date"),
                    "fruit_set_date": mango.get("fruit_set_detected_date"),
                    "heat_stress_events_count": mango.get("heat_stress_events_count") or 0,
                }
                if row is None:
                    conn.execute(
                        "INSERT INTO mango_phenology_log "
                        "(plot_id, season_label, bearing_year, "
                        " flowering_peak_date, fruit_set_date, "
                        " heat_stress_events_count) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            plot_id,
                            season_label,
                            fields["bearing_year"],
                            fields["flowering_peak_date"],
                            fields["fruit_set_date"],
                            fields["heat_stress_events_count"],
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE mango_phenology_log SET "
                        " bearing_year = ?, "
                        " flowering_peak_date = COALESCE(?, flowering_peak_date), "
                        " fruit_set_date = COALESCE(?, fruit_set_date), "
                        " heat_stress_events_count = ? "
                        "WHERE plot_id = ? AND season_label = ?",
                        (
                            fields["bearing_year"],
                            fields["flowering_peak_date"],
                            fields["fruit_set_date"],
                            fields["heat_stress_events_count"],
                            plot_id,
                            season_label,
                        ),
                    )

            if "farm_plots" in tables:
                # Only update mango columns when they exist in the mirror.
                cur = conn.execute("PRAGMA table_info(farm_plots)")
                cols = {row[1] for row in cur.fetchall()}
                sets: list[str] = []
                params: list[Any] = []
                if mango.get("bearing_year") and "bearing_year" in cols:
                    sets.append("bearing_year = ?")
                    params.append(mango["bearing_year"])
                if mango.get("bearing_confidence") is not None and "bearing_confidence" in cols:
                    sets.append("bearing_confidence = ?")
                    params.append(mango["bearing_confidence"])
                if "last_bearing_detection_date" in cols and mango.get("bearing_year"):
                    sets.append("last_bearing_detection_date = ?")
                    params.append(today_iso)
                if "flowering_detected" in cols:
                    sets.append("flowering_detected = ?")
                    params.append(1 if mango.get("flowering_detected") else 0)
                if mango.get("flowering_detected_date") and "flowering_detected_date" in cols:
                    sets.append("flowering_detected_date = ?")
                    params.append(mango["flowering_detected_date"])
                if "fruit_set_detected" in cols:
                    sets.append("fruit_set_detected = ?")
                    params.append(1 if mango.get("fruit_set_detected") else 0)
                if mango.get("fruit_set_detected_date") and "fruit_set_detected_date" in cols:
                    sets.append("fruit_set_detected_date = ?")
                    params.append(mango["fruit_set_detected_date"])

                if sets:
                    params.append(plot_id)
                    conn.execute(
                        f"UPDATE farm_plots SET {', '.join(sets)} WHERE id = ?",
                        params,
                    )

            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _mango_season_label(today: date) -> str:
        """Return e.g. '2025-26' for a date in May 2026 (mango season Jul-Jun)."""
        # Mango bearing cycle anchors on Aug-Sep canopy; treat Jul as the season
        # boundary so that the Apr-Jun harvest belongs to the prior label.
        if today.month >= 7:
            start = today.year
        else:
            start = today.year - 1
        return f"{start}-{str(start + 1)[2:]}"

    # ------------------------------------------------------------------
    # Top-level orchestration
    # ------------------------------------------------------------------

    async def run_full_pipeline(
        self, farmer_id: str, plot: Mapping[str, Any]
    ) -> dict:
        """Execute steps 1-5 and return a structured result dict.

        The pipeline never raises on upstream failures — every issue is
        recorded in ``result['errors']`` so the caller can surface partial
        results to downstream consumers.
        """
        errors: list[str] = []
        result: dict[str, Any] = {
            "farmer_id": farmer_id,
            "plot_id": plot.get("id") or plot.get("plot_id"),
            "amed": None,
            "amed_cache_hit": False,
            "belt": None,
            "sentinel": None,
            "harvest_window": None,
            "combined_health": None,
            "mismatches": [],
            "harvest_source": "ndvi_estimate",
            "harvest_confidence": 0.0,
            "errors": errors,
        }

        # Steps 1 & 3 can run concurrently — Step 2 needs AMED's crop_type
        # so we await Step 1 first.
        amed, cache_hit = await self.fetch_amed_field(plot, errors)
        result["amed"] = amed
        result["amed_cache_hit"] = cache_hit

        crop_for_belt = (
            (amed or {}).get("crop_type")
            or plot.get("current_crop")
            or "Grapes"
        )

        belt_task = asyncio.create_task(self.fetch_amed_belt(crop_for_belt, errors))
        sentinel_task = asyncio.create_task(self.fetch_sentinel(plot, errors))
        belt, sentinel = await asyncio.gather(belt_task, sentinel_task)
        result["belt"] = belt
        result["sentinel"] = sentinel

        # Step 4: Combine.
        harvest_window, combined, harvest_source, confidence = self._combine(
            amed, sentinel, errors
        )
        result["harvest_window"] = harvest_window
        result["combined_health"] = combined
        result["harvest_source"] = harvest_source
        result["harvest_confidence"] = confidence

        # Step 4b: Mango phenology branch (Agent 4, SDD §2.2-§2.3).
        # Wrapped — failures here never break the pipeline.
        registered_crop = (plot.get("current_crop") or "").strip().lower()
        amed_crop = ((amed or {}).get("crop_type") or "").strip().lower()
        if "mango" in (registered_crop, amed_crop):
            try:
                result["mango_phenology"] = self._run_mango_phenology(
                    plot, amed, sentinel, errors
                )
            except Exception as exc:
                errors.append(f"mango_phenology_branch_failed: {exc}")
                result["mango_phenology"] = None

        # Step 5: Mismatch detection + DB flag write when DB exists.
        if amed:
            mismatches = self.detect_mismatches(plot, amed)
            if mismatches:
                if self._flag_mismatches_in_db(mismatches):
                    for m in mismatches:
                        m["db_flagged"] = True
                result["mismatches"] = mismatches

        # Post-step-5: variety collection trigger (Agent 3).
        # Best-effort — never crash the pipeline on a trigger failure.
        try:
            from pipelines.variety_trigger import (
                trigger_variety_collection_if_needed,
            )

            result["variety_trigger"] = trigger_variety_collection_if_needed(
                farmer_id=farmer_id,
                plot_id=plot.get("id") or plot.get("plot_id"),
                amed_data=result["amed"] or {},
                db_path=self.db_path,
            )
        except Exception as exc:  # pragma: no cover - defensive only
            logger.warning("variety_trigger_failed: %s", exc)
            result["variety_trigger"] = {
                "action": "skipped",
                "reason": f"trigger_exception: {exc}",
                "session_id": None,
            }

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _maybe_await(func, *args, **kwargs):
    """Call ``func`` and ``await`` it if it returned a coroutine.

    Lets ``AmedSentinelPipeline`` work transparently against both the
    async AMEDClient (per SDD §2) and synchronous mocks used in tests.
    """
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


__all__ = [
    "AmedSentinelPipeline",
    "DEFAULT_REGION",
    "TASGAON_BBOX",
    "CACHE_TTL_DAYS",
]
