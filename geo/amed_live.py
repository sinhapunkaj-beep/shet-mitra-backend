"""AMED live API client (stub).

This module will host the real Google Earth Engine / AMED partner-API
implementation once Google DeepMind issues the API key. Until then every
call raises NotImplementedError so the AMEDClient facade transparently
falls back to ``geo.amed_mock``.

Expected real-world flow (per SDD Section 2):

    1. Authenticate via a Google OAuth2 service account.
       Service-account JSON path is read from ``AMED_API_KEY`` (or a
       sibling env var) and exchanged for a short-lived bearer token at
       https://oauth2.googleapis.com/token using the
       ``https://www.googleapis.com/auth/earthengine`` scope.

    2. Issue an Earth Engine REST call to
       ``https://earthengine.googleapis.com/v1/projects/{project}/...``
       with the polygon (field-level) or bbox + crop_type (belt-level) as
       the query payload. AMED-specific routing may instead use a partner
       endpoint surfaced after key approval.

    3. Map the upstream AMED payload back to the shape documented in
       SDD Section 2.1 / 2.2 / 2.3 so the rest of ShetMitra never has to
       care which backend served the request.

    4. Retry transient failures with exponential backoff; surface other
       errors as exceptions so the facade can fall through to the mock.

When this file is implemented, leave the AMEDClient public method
signatures untouched — they are the contract every other Agent (2..6)
builds against.
"""

from __future__ import annotations

import os
from typing import Any

_NOT_READY_MESSAGE = (
    "AMED live API not yet available - set AMED_USE_MOCK=true"
)


class AMEDLiveClient:
    """Stub implementation of the live AMED backend.

    The constructor accepts an optional api_key so calling code can be
    written today; passing a key has no effect because every method
    raises NotImplementedError.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("AMED_API_KEY", "")
        # Reserved for the eventual Earth Engine project id / service-account
        # JSON path. Captured here so future implementers see the shape.
        self.project_id = os.getenv("AMED_PROJECT_ID", "")
        self.base_url = os.getenv(
            "AMED_BASE_URL",
            "https://earthengine.googleapis.com/v1",
        )

    # ------------------------------------------------------------------
    # Public API — every call is a stub until the AMED key arrives.
    # ------------------------------------------------------------------

    def get_field_data(
        self,
        polygon: list[tuple[float, float]] | None = None,
        centroid: tuple[float, float] | None = None,
        area_acres: float | None = None,
        plot_id: str | None = None,
        crop_season: str = "rabi_2025_26",
    ) -> dict[str, Any]:
        raise NotImplementedError(_NOT_READY_MESSAGE)

    def get_belt_data(
        self,
        bbox: dict[str, float],
        crop_type: str,
        season: str = "rabi_2025_26",
    ) -> dict[str, Any]:
        raise NotImplementedError(_NOT_READY_MESSAGE)

    def get_historical_data(
        self,
        bbox: dict[str, float],
        crop_type: str,
        seasons: list[str],
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_NOT_READY_MESSAGE)
