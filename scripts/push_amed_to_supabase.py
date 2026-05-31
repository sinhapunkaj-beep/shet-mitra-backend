"""Backfill AMED belt data + history + price_history_training AMED features
into the live ShetMitra TEST Supabase. Idempotent on re-run.

Three steps:
  1. INSERT Pomegranate seasons into amed_history (3 rows) if missing.
  2. INSERT weekly amed_belt_data rows for Tasgaon/Sangli Grapes and
     Solapur/Nashik Pomegranate belts (2 years of weekly forecasts).
  3. UPDATE price_history_training with bell-curve weekly distribution
     of amed_belt_volume_mt / amed_fields_harvesting / amed_health_pct_good
     / amed_season_week, derived from amed_history season totals.
"""

from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import dotenv_values

NANO = Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env")


GRAPE_BELT = "Tasgaon_Sangli_belt"
POM_BELT = "Solapur_Nashik_belt"

# Pomegranate seasons to ensure exist (Grapes already seeded by migration 003).
POMEGRANATE_SEASONS = [
    {
        "season_label": "2022-23", "season_year_start": 2022,
        "harvest_start": date(2022, 8, 1),
        "harvest_peak": date(2022, 11, 15),
        "harvest_end": date(2023, 2, 15),
        "total_area_acres": 5980, "total_volume_mt": 5980,
        "avg_price": 48.50,
    },
    {
        "season_label": "2023-24", "season_year_start": 2023,
        "harvest_start": date(2023, 8, 1),
        "harvest_peak": date(2023, 11, 15),
        "harvest_end": date(2024, 2, 15),
        "total_area_acres": 6120, "total_volume_mt": 6120,
        "avg_price": 52.20,
    },
    {
        "season_label": "2024-25", "season_year_start": 2024,
        "harvest_start": date(2024, 8, 1),
        "harvest_peak": date(2024, 11, 15),
        "harvest_end": date(2025, 2, 15),
        "total_area_acres": 6240, "total_volume_mt": 6240,
        "avg_price": 58.40,
    },
]

GRAPE_SEASONS = [
    {
        "season_label": "2022-23",
        "harvest_start": date(2023, 4, 1),
        "harvest_peak": date(2023, 4, 21),
        "harvest_end": date(2023, 5, 15),
        "total_volume_mt": 11420,
    },
    {
        "season_label": "2023-24",
        "harvest_start": date(2024, 4, 5),
        "harvest_peak": date(2024, 4, 19),
        "harvest_end": date(2024, 5, 10),
        "total_volume_mt": 11680,
    },
    {
        "season_label": "2024-25",
        "harvest_start": date(2025, 3, 28),
        "harvest_peak": date(2025, 4, 14),
        "harvest_end": date(2025, 5, 8),
        "total_volume_mt": 11890,
    },
]

# Fields-per-belt estimate for converting MT -> field counts.
FIELDS_PER_BELT = {"Grapes": 2847, "Pomegranate": 1820}
AVG_HEALTH_GOOD = 0.63


def _bell_weights(harvest_start: date, harvest_peak: date, harvest_end: date) -> list[float]:
    """Normalized weekly weights peaking at harvest_peak."""

    harvest_weeks = max((harvest_end - harvest_start).days // 7 + 1, 1)
    peak_offset = max((harvest_peak - harvest_start).days // 7, 0)
    sigma = max(harvest_weeks / 4.0, 0.5)
    raw = [math.exp(-((i - peak_offset) ** 2) / (2 * sigma * sigma))
           for i in range(harvest_weeks)]
    s = sum(raw) or 1.0
    return [r / s for r in raw]


def _ensure_pomegranate_history(cur) -> int:
    inserted = 0
    for s in POMEGRANATE_SEASONS:
        cur.execute(
            """
            INSERT INTO amed_history
              (region, season_label, season_year_start, crop_type,
               total_area_acres, harvest_start_date, harvest_peak_date,
               harvest_end_date, estimated_total_volume_mt, avg_price_modal_kg)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (region, season_label, crop_type) DO NOTHING
            """,
            (POM_BELT, s["season_label"], s["season_year_start"], "Pomegranate",
             s["total_area_acres"], s["harvest_start"], s["harvest_peak"],
             s["harvest_end"], s["total_volume_mt"], s["avg_price"]),
        )
        inserted += cur.rowcount
    return inserted


def _weekly_belt_rows(belt: str, crop: str, seasons: list[dict]) -> list[tuple]:
    rows: list[tuple] = []
    for s in seasons:
        weights = _bell_weights(s["harvest_start"], s["harvest_peak"], s["harvest_end"])
        season_total = s["total_volume_mt"]
        n_fields = FIELDS_PER_BELT[crop]
        for i, w in enumerate(weights):
            week_start = s["harvest_start"] + timedelta(days=7 * i)
            week_end = week_start + timedelta(days=6)
            volume_mt = round(season_total * w, 2)
            avg_volume_per_field = season_total / n_fields if n_fields else 1
            fields_harvesting = int(round(volume_mt / max(avg_volume_per_field, 0.01)))
            rows.append((
                belt, week_start, crop,
                n_fields, FIELDS_PER_BELT[crop] * 2.9,  # ~2.9 acres avg / field
                week_start, week_end, fields_harvesting, volume_mt,
                AVG_HEALTH_GOOD, 0.24, 0.10, 0.03, week_start,
            ))
    return rows


def _insert_belt_rows(cur, rows: list[tuple]) -> int:
    if not rows:
        return 0
    # idempotency: dedupe on (region, crop_type, harvest_week_start)
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO amed_belt_data
          (region, fetch_date, crop_type, total_fields_detected,
           total_area_acres, harvest_week_start, harvest_week_end,
           fields_harvesting, estimated_volume_mt, health_pct_good,
           health_pct_moderate, health_pct_stressed, health_pct_critical,
           data_refresh_date)
        VALUES %s
        """,
        rows,
    )
    return len(rows)


def _backfill_price_history(cur, commodity_label: str, crop: str,
                            seasons: list[dict]) -> int:
    """For each row in price_history_training, assign AMED features.

    In-season rows get bell-curve-derived volume + week index. Off-season rows
    in the same year span are zeroed (volume=0, fields=0, week=0) so the model
    sees a continuous 0 -> peak -> 0 trajectory rather than NULL gaps.
    """

    n_fields = FIELDS_PER_BELT[crop]
    updates = 0
    if not seasons:
        return 0

    # Determine the full year span we are filling.
    full_start = min(s["harvest_start"] for s in seasons) - timedelta(days=180)
    full_end = max(s["harvest_end"] for s in seasons) + timedelta(days=180)

    # First, zero everything in the span — then overwrite the in-season weeks.
    cur.execute(
        """
        UPDATE price_history_training
           SET amed_belt_volume_mt   = 0,
               amed_fields_harvesting = 0,
               amed_health_pct_good   = %s,
               amed_season_week       = 0
         WHERE commodity = %s
           AND date BETWEEN %s AND %s
        """,
        (AVG_HEALTH_GOOD, commodity_label, full_start, full_end),
    )
    updates += cur.rowcount

    for s in seasons:
        weights = _bell_weights(s["harvest_start"], s["harvest_peak"], s["harvest_end"])
        season_total = s["total_volume_mt"]
        for i, w in enumerate(weights):
            week_start = s["harvest_start"] + timedelta(days=7 * i)
            week_end = week_start + timedelta(days=6)
            volume_mt = season_total * w
            avg_volume_per_field = season_total / n_fields if n_fields else 1
            fields_harvesting = int(round(volume_mt / max(avg_volume_per_field, 0.01)))
            cur.execute(
                """
                UPDATE price_history_training
                   SET amed_belt_volume_mt   = %s,
                       amed_fields_harvesting = %s,
                       amed_health_pct_good   = %s,
                       amed_season_week       = %s
                 WHERE commodity = %s
                   AND date BETWEEN %s AND %s
                """,
                (round(volume_mt, 2), fields_harvesting, AVG_HEALTH_GOOD, i + 1,
                 commodity_label, week_start, week_end),
            )
    return updates


def main() -> int:
    creds = dotenv_values(NANO)
    pw = creds.get("SUPABASE_DB_PASSWORD")
    if not pw:
        sys.exit("FATAL: SUPABASE_DB_PASSWORD missing from nano.env")

    print("[connect] live Supabase TEST")
    conn = psycopg2.connect(
        host="db.euydubpywdsettjywkms.supabase.co",
        port=5432, dbname="postgres", user="postgres", password=pw,
        sslmode="require", connect_timeout=20,
    )
    conn.autocommit = False
    cur = conn.cursor()

    print("[step 1] amed_history: ensure Pomegranate seasons present")
    hist_added = _ensure_pomegranate_history(cur)
    print(f"  inserted: {hist_added}")

    print("[step 2] amed_belt_data: weekly grape + pomegranate forecasts")
    grape_rows = _weekly_belt_rows(GRAPE_BELT, "Grapes", GRAPE_SEASONS)
    pom_rows = _weekly_belt_rows(POM_BELT, "Pomegranate", POMEGRANATE_SEASONS)
    # Clear any pre-existing rows for these belts/crops in the date range so
    # the re-run does not duplicate (cheaper than per-row ON CONFLICT here).
    cur.execute(
        "DELETE FROM amed_belt_data "
        "WHERE region IN (%s,%s) AND crop_type IN ('Grapes','Pomegranate')",
        (GRAPE_BELT, POM_BELT),
    )
    belt_inserted = _insert_belt_rows(cur, grape_rows + pom_rows)
    print(f"  inserted: {belt_inserted}")

    print("[step 3] price_history_training: backfill AMED features")
    g_updates = _backfill_price_history(cur, "Dry Grapes", "Grapes", GRAPE_SEASONS)
    p_updates = _backfill_price_history(cur, "Pomegranate", "Pomegranate", POMEGRANATE_SEASONS)
    print(f"  Dry Grapes rows updated: {g_updates}")
    print(f"  Pomegranate rows updated: {p_updates}")

    conn.commit()

    # Verification
    cur.execute("SELECT COUNT(*) FROM amed_history")
    print(f"\nVERIFY amed_history total: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM amed_belt_data")
    print(f"VERIFY amed_belt_data total: {cur.fetchone()[0]}")
    cur.execute(
        "SELECT commodity, COUNT(*) FILTER (WHERE amed_belt_volume_mt IS NOT NULL) "
        "FROM price_history_training GROUP BY commodity ORDER BY commodity"
    )
    print("VERIFY price_history_training rows with AMED features:")
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1]}")

    cur.close()
    conn.close()
    print("\n[ok] AMED backfill committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
