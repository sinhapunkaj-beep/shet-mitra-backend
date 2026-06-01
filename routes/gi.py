"""routes/gi.py — SDD §8

Jardalu GI verification endpoints for Bagaan Sathi.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from geo.gi_verifier import GIVerifier


router = APIRouter(prefix="/gi", tags=["gi"])


@router.get("/verify/{plot_id}")
def verify_plot(plot_id: str) -> dict:
    """Return the SDD §3.3 verification dict for the plot.

    The dict is always populated — ``gi_eligible: false`` is a normal
    response, not an error. Callers (WPF dashboard, Flutter app) render
    the reason string when ineligible.
    """
    verifier = GIVerifier()
    result = verifier.verify_jardalu(plot_id)
    result["plot_id"] = plot_id
    return result


@router.get("/certificate/{plot_id}", response_class=PlainTextResponse)
def get_certificate(plot_id: str) -> str:
    """Return the plain-text certificate or 404 if the plot is not eligible.

    Requires that a farmer_id resolvable from the plot row exists; we
    look it up via the verifier's data shim so the route inherits the
    same fail-soft semantics as the underlying class.
    """
    verifier = GIVerifier()
    plot = verifier._get_plot(plot_id)  # noqa: SLF001 — verifier owns access
    if not plot:
        raise HTTPException(status_code=404, detail="plot not found")
    farmer_id = plot.get("farmer_id") or plot.get("farmer")
    if not farmer_id:
        raise HTTPException(
            status_code=404, detail="plot has no farmer association"
        )
    text = verifier.get_gi_certificate_text(str(farmer_id), plot_id)
    if text is None:
        raise HTTPException(
            status_code=404,
            detail="plot is not GI-eligible (see /gi/verify for reason)",
        )
    return text
