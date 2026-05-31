"""SQLite-backed 14-day cache for AMED API responses.

Implements the caching rules from SDD Section 4.1:
  * AMED field data refreshes every 14 days per plot.
  * AMED belt data refreshes every 14 days per region.

The implementation is intentionally tolerant. If the SQLite database file
or the required tables are missing, the cache silently degrades to a
no-op so unit tests and offline development still work.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Mapping


# 14 days per SDD Section 4.1 ("AMED refreshes every 14 days").
CACHE_TTL_DAYS = 14

# Default location seeded by AGENT 2 (scripts/seed_local_sqlite.py).
DEFAULT_DB_PATH = os.path.join("data", "test.db")


def _today() -> date:
    return datetime.now().date()


class AmedCache:
    """Read-through cache for AMED field and belt responses.

    The cache is keyed in SQLite by ``plot_id`` (field) and ``region``
    (belt). A row is considered fresh when its ``fetch_date`` is no more
    than :data:`CACHE_TTL_DAYS` days old.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Low-level connection helpers
    # ------------------------------------------------------------------

    def _available(self) -> bool:
        return bool(self.db_path) and os.path.exists(self.db_path)

    def _connect(self) -> sqlite3.Connection | None:
        if not self._available():
            return None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    def _table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        try:
            cur = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            )
            return cur.fetchone() is not None
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------
    # Field-level cache (amed_readings)
    # ------------------------------------------------------------------

    def get_field_cached(self, plot_id: str) -> dict | None:
        """Return the most recent cached AMED field row if still fresh.

        Returns ``None`` when the row is older than 14 days, missing, or
        the SQLite layer is unavailable.
        """
        conn = self._connect()
        if conn is None:
            return None
        try:
            if not self._table_exists(conn, "amed_readings"):
                return None
            cutoff = (_today() - timedelta(days=CACHE_TTL_DAYS)).isoformat()
            cur = conn.execute(
                """
                SELECT fetch_date, crop_type_detected, crop_type_confidence,
                       field_size_acres_amed, sowing_date, harvest_date_predicted,
                       growth_stage, growth_stage_confidence, irrigation_detected,
                       last_event, last_event_date, data_refresh_date, use_mock,
                       raw_response
                FROM amed_readings
                WHERE plot_id = ?
                  AND fetch_date >= ?
                ORDER BY fetch_date DESC, created_at DESC
                LIMIT 1
                """,
                (str(plot_id), cutoff),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_field_dict(row)
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def put_field(self, plot_id: str, response: Mapping[str, Any]) -> None:
        """Persist a fresh AMED field response into ``amed_readings``.

        Silently no-ops when the database or table is absent.
        """
        conn = self._connect()
        if conn is None:
            return
        try:
            if not self._table_exists(conn, "amed_readings"):
                return
            conn.execute(
                """
                INSERT INTO amed_readings (
                    plot_id, fetch_date, crop_type_detected, crop_type_confidence,
                    field_size_acres_amed, sowing_date, harvest_date_predicted,
                    growth_stage, growth_stage_confidence, irrigation_detected,
                    last_event, last_event_date, data_refresh_date, use_mock,
                    raw_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(plot_id),
                    _today().isoformat(),
                    response.get("crop_type"),
                    response.get("crop_confidence"),
                    response.get("field_size_acres"),
                    response.get("sowing_date"),
                    response.get("harvest_date_predicted"),
                    response.get("growth_stage"),
                    response.get("growth_stage_confidence"),
                    1 if response.get("irrigation_detected") else 0,
                    response.get("last_event"),
                    response.get("last_event_date"),
                    response.get("data_refresh_date"),
                    1 if response.get("use_mock", True) else 0,
                    json.dumps(dict(response), default=str),
                ),
            )
            conn.commit()
        except sqlite3.Error:
            # Treat write errors as a soft failure so the pipeline still
            # completes — the in-memory response is already authoritative.
            return
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Belt-level cache (amed_belt_data)
    # ------------------------------------------------------------------

    def get_belt_cached(self, region: str) -> dict | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            if not self._table_exists(conn, "amed_belt_data"):
                return None
            cutoff = (_today() - timedelta(days=CACHE_TTL_DAYS)).isoformat()
            cur = conn.execute(
                """
                SELECT fetch_date, region, crop_type, total_fields_detected,
                       total_area_acres, harvest_week_start, harvest_week_end,
                       fields_harvesting, estimated_volume_mt, health_pct_good,
                       health_pct_moderate, health_pct_stressed, health_pct_critical,
                       data_refresh_date, raw_response
                FROM amed_belt_data
                WHERE region = ?
                  AND fetch_date >= ?
                ORDER BY fetch_date DESC, created_at DESC
                LIMIT 1
                """,
                (str(region), cutoff),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_belt_dict(row)
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def put_belt(self, region: str, response: Mapping[str, Any]) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            if not self._table_exists(conn, "amed_belt_data"):
                return
            forecast = response.get("harvest_forecast") or []
            week = forecast[0] if forecast else {}
            health = response.get("health_distribution") or {}
            conn.execute(
                """
                INSERT INTO amed_belt_data (
                    region, fetch_date, crop_type, total_fields_detected,
                    total_area_acres, harvest_week_start, harvest_week_end,
                    fields_harvesting, estimated_volume_mt,
                    health_pct_good, health_pct_moderate,
                    health_pct_stressed, health_pct_critical,
                    data_refresh_date, raw_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(region),
                    _today().isoformat(),
                    response.get("crop_type"),
                    response.get("total_fields_detected"),
                    response.get("total_area_acres"),
                    week.get("week_start"),
                    week.get("week_end"),
                    week.get("fields_harvesting"),
                    week.get("estimated_volume_mt"),
                    health.get("good"),
                    health.get("moderate"),
                    health.get("stressed"),
                    health.get("critical"),
                    response.get("data_refresh_date"),
                    json.dumps(dict(response), default=str),
                ),
            )
            conn.commit()
        except sqlite3.Error:
            return
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def _row_to_field_dict(row: sqlite3.Row) -> dict:
    """Rebuild a field response dict from a cached ``amed_readings`` row.

    Prefers the original ``raw_response`` JSON when present so downstream
    code sees exactly the same payload as a live fetch.
    """
    raw = row["raw_response"]
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("cached", True)
                data.setdefault("fetch_date", row["fetch_date"])
                return data
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "cached": True,
        "fetch_date": row["fetch_date"],
        "crop_type": row["crop_type_detected"],
        "crop_confidence": row["crop_type_confidence"],
        "field_size_acres": row["field_size_acres_amed"],
        "sowing_date": row["sowing_date"],
        "harvest_date_predicted": row["harvest_date_predicted"],
        "growth_stage": row["growth_stage"],
        "growth_stage_confidence": row["growth_stage_confidence"],
        "irrigation_detected": bool(row["irrigation_detected"]),
        "last_event": row["last_event"],
        "last_event_date": row["last_event_date"],
        "data_refresh_date": row["data_refresh_date"],
        "use_mock": bool(row["use_mock"]),
        "source": "AMED",
    }


def _row_to_belt_dict(row: sqlite3.Row) -> dict:
    raw = row["raw_response"]
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("cached", True)
                data.setdefault("fetch_date", row["fetch_date"])
                return data
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "cached": True,
        "fetch_date": row["fetch_date"],
        "region": row["region"],
        "crop_type": row["crop_type"],
        "total_fields_detected": row["total_fields_detected"],
        "total_area_acres": row["total_area_acres"],
        "harvest_forecast": [
            {
                "week_start": row["harvest_week_start"],
                "week_end": row["harvest_week_end"],
                "fields_harvesting": row["fields_harvesting"],
                "estimated_volume_mt": row["estimated_volume_mt"],
            }
        ],
        "health_distribution": {
            "good": row["health_pct_good"],
            "moderate": row["health_pct_moderate"],
            "stressed": row["health_pct_stressed"],
            "critical": row["health_pct_critical"],
        },
        "data_refresh_date": row["data_refresh_date"],
        "source": "AMED",
    }


__all__ = ["AmedCache", "CACHE_TTL_DAYS", "DEFAULT_DB_PATH"]
