"""routes/regions.py — SDD §8

Multi-region metadata endpoint. Reads from the ``regions`` table created
by migration 009. When the SQLite mirror has no ``regions`` table yet
(fresh dev box, tests with a tmp DB) the route returns the SDD-default
seed rows so the WPF dashboard and Flutter app can still render.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter

from api import whatsapp_db


logger = logging.getLogger(__name__)
router = APIRouter(tags=["regions"])


# SDD §2.1 seed — used as the fallback when the regions table doesn't exist.
_FALLBACK_REGIONS = [
    {
        "region_code": "MH",
        "region_name": "Maharashtra",
        "whatsapp_sender_name": "ShetMitra",
        "default_language": "Marathi",
        "primary_crops": ["Grapes", "Pomegranate", "Mango"],
        "primary_mandis": [
            "Tasgaon APMC",
            "Sangli APMC",
            "Solapur APMC",
            "Nashik APMC",
            "Ratnagiri APMC",
        ],
        "is_active": True,
    },
    {
        "region_code": "JH",
        "region_name": "Jharkhand",
        "whatsapp_sender_name": "Bagaan Sathi",
        "default_language": "Hindi",
        "primary_crops": ["Mango"],
        "primary_mandis": [
            "Bhagalpur APMC",
            "Ranchi APMC",
            "Malda APMC",
            "Patna APMC",
            "Delhi Azadpur APMC",
        ],
        "is_active": True,
    },
]


def _parse_list_field(value) -> list[str]:
    """Postgres arrays come back as Python lists; SQLite mirrors them as
    JSON-encoded TEXT. Normalise either to a list[str].
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("[") and v.endswith("]"):
            try:
                import json
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except (ValueError, TypeError):
                pass
        # Postgres '{a,b,c}' literal — strip braces, split on comma.
        if v.startswith("{") and v.endswith("}"):
            inner = v[1:-1]
            return [seg.strip().strip('"') for seg in inner.split(",") if seg.strip()]
    return []


def _row_to_region_dict(row) -> dict:
    return {
        "region_code": row["region_code"],
        "region_name": row["region_name"],
        "whatsapp_sender_name": row["whatsapp_sender_name"],
        "default_language": row["default_language"],
        "primary_crops": _parse_list_field(row["primary_crops"])
            if "primary_crops" in row.keys() else [],
        "primary_mandis": _parse_list_field(row["primary_mandis"])
            if "primary_mandis" in row.keys() else [],
        "is_active": bool(row["is_active"]) if "is_active" in row.keys() else True,
    }


@router.get("/regions")
def list_regions(active_only: bool = True) -> list[dict]:
    """List active regions (SDD §8).

    Falls back to the SDD seed when the ``regions`` table is missing —
    keeps the dashboard render path green in tests / fresh dev DBs.
    """
    try:
        with whatsapp_db._connect() as conn:  # noqa: SLF001
            if not whatsapp_db._table_exists(conn, "regions"):  # noqa: SLF001
                rows = list(_FALLBACK_REGIONS)
            else:
                sql = "SELECT * FROM regions"
                params: tuple = ()
                if active_only:
                    sql += " WHERE is_active = 1 OR is_active = TRUE"
                cur = conn.execute(sql, params)
                rows = [_row_to_region_dict(r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("regions list: DB unavailable, using fallback: %s", exc)
        rows = list(_FALLBACK_REGIONS)

    if active_only:
        rows = [r for r in rows if r.get("is_active", True)]
    return rows
