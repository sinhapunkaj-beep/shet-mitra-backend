"""ShetMitra continuous training - public read endpoints.

Currently exposes a single endpoint:

    GET /models/registry

which returns every (commodity, variety) model with its training history,
the currently active row, and a MAPE-over-time series for charting.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])


@router.get("/models/registry")
def models_registry(limit: int = 200) -> dict[str, Any]:
    """Return every model in model_registry grouped by (commodity, variety).

    Response shape::

        {
          "count": <int>,
          "models": [
            {
              "commodity": "Dry_Grapes", "variety": null,
              "active": { id, model_version, mape, ... },
              "history": [ ... ],
              "mape_series": [ {created_at, mape}, ... ]
            },
            ...
          ]
        }
    """
    try:
        from pipelines.model_retraining import get_registry_snapshot
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "model registry unavailable") from exc
    try:
        return get_registry_snapshot(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("registry read failed: %s", exc)
        raise HTTPException(500, "registry read failed") from exc
