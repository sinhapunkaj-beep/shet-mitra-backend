"""AMED client facade.

Public entry point for the rest of ShetMitra (pipeline, dashboard,
ML training, etc). Hides the mock-vs-live decision behind a single
class so callers never reach into ``amed_mock`` or ``amed_live``
directly.

Switching backend:
    AMED_USE_MOCK=true   -> always return geo.amed_mock data (default)
    AMED_USE_MOCK=false  -> attempt geo.amed_live; on ANY error
                            (including NotImplementedError while the
                            real API key is pending) fall back to the
                            mock and log a warning.

Environment loading order:
    1. python-dotenv .env at the project root (if present)
    2. python-dotenv shet_mitra/.env (if present)
    3. existing os.environ values (never overwritten)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from . import amed_live, amed_mock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Environment bootstrap
# ---------------------------------------------------------------------------

def _load_env_files() -> None:
    """Load .env files in priority order without overwriting real env vars."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        logger.debug("python-dotenv not installed; relying on os.environ only")
        return

    # Project root is the parent of the geo package directory.
    here = Path(__file__).resolve().parent
    project_root = here.parent
    candidate_paths = [
        project_root / ".env",
        project_root / "shet_mitra" / ".env",
    ]
    for path in candidate_paths:
        if path.is_file():
            load_dotenv(dotenv_path=path, override=False)


_load_env_files()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AMEDClient:
    """Facade over the mock and (eventual) live AMED backends."""

    def __init__(self, api_key: str | None = None) -> None:
        # Re-run env loading on every construction so tests that mutate
        # os.environ between AMEDClient instances behave predictably.
        _load_env_files()
        self.api_key = api_key if api_key is not None else os.getenv("AMED_API_KEY", "")
        self._live = amed_live.AMEDLiveClient(api_key=self.api_key)

    # ------------------------------------------------------------------
    # Mode switch
    # ------------------------------------------------------------------

    def _use_mock(self) -> bool:
        """Return True if the mock backend should be used.

        Reads AMED_USE_MOCK from the environment each call so a test or
        a long-running pipeline can flip the toggle without rebuilding
        the client. The flag defaults to True when unset or when the
        value is unparseable.
        """
        raw = os.getenv("AMED_USE_MOCK", "true")
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_field_data(
        self,
        polygon: list[tuple[float, float]] | None = None,
        centroid: tuple[float, float] | None = None,
        area_acres: float | None = None,
        plot_id: str | None = None,
        crop_season: str = "rabi_2025_26",
    ) -> dict[str, Any]:
        """Return AMED field-level data (SDD Section 2.1)."""
        if self._use_mock():
            return amed_mock.get_field_data(
                polygon=polygon,
                centroid=centroid,
                area_acres=area_acres,
                plot_id=plot_id,
                crop_season=crop_season,
            )
        try:
            return self._live.get_field_data(
                polygon=polygon,
                centroid=centroid,
                area_acres=area_acres,
                plot_id=plot_id,
                crop_season=crop_season,
            )
        except Exception as exc:  # noqa: BLE001 -- intentional broad catch
            logger.warning(
                "AMED live get_field_data failed (%s); falling back to mock",
                exc,
            )
            return amed_mock.get_field_data(
                polygon=polygon,
                centroid=centroid,
                area_acres=area_acres,
                plot_id=plot_id,
                crop_season=crop_season,
            )

    def get_belt_data(
        self,
        bbox: dict[str, float],
        crop_type: str,
        season: str = "rabi_2025_26",
    ) -> dict[str, Any]:
        """Return AMED belt-level data (SDD Section 2.2)."""
        if self._use_mock():
            return amed_mock.get_belt_data(bbox=bbox, crop_type=crop_type, season=season)
        try:
            return self._live.get_belt_data(bbox=bbox, crop_type=crop_type, season=season)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AMED live get_belt_data failed (%s); falling back to mock",
                exc,
            )
            return amed_mock.get_belt_data(bbox=bbox, crop_type=crop_type, season=season)

    def get_historical_data(
        self,
        bbox: dict[str, float],
        crop_type: str,
        seasons: list[str],
    ) -> list[dict[str, Any]]:
        """Return AMED historical belt data (SDD Section 2.3)."""
        if self._use_mock():
            return amed_mock.get_historical_data(bbox=bbox, crop_type=crop_type, seasons=seasons)
        try:
            return self._live.get_historical_data(
                bbox=bbox, crop_type=crop_type, seasons=seasons,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AMED live get_historical_data failed (%s); falling back to mock",
                exc,
            )
            return amed_mock.get_historical_data(
                bbox=bbox, crop_type=crop_type, seasons=seasons,
            )
