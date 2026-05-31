"""Tests for scripts.load_amed_history (Agent 4 — Historical Data Agent).

These tests exercise the full Agent-4 pipeline end-to-end against a
temporary SQLite DB so we never touch the real ``data/test.db``:

* ``test_all_three_seasons_loaded`` — calling ``main()`` with synthesised
  arguments inserts exactly the three Tasgaon/Sangli Grapes seasons with the
  expected volume totals (11420, 11680, 11890 MT).
* ``test_backfill_covers_2022_2026`` — the backfilled CSV spans at least
  January 2022 through April 2026 and has AMED features populated for rows
  that fall inside a season window.
* ``test_weekly_distribution_sums_to_season_total`` — the bell-curve weekly
  volumes (over the full harvest window) sum to within 1% of each season's
  ``estimated_total_volume_mt``.
* ``test_synthetic_2025_price_spike_present`` — the synthetic CSV reproduces
  the 2025 Dry_Grapes spike so Agent 5's retraining target is well defined.
"""

from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

# Make the repo root importable so ``scripts.*`` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import build_synthetic_price_history as builder  # noqa: E402
from scripts import load_amed_history as loader  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Build the synthetic CSV once, then run the loader once. Reused by all tests."""
    tmpdir = tmp_path_factory.mktemp("agent4")
    input_csv = tmpdir / "price_history_training_synthetic.csv"
    output_csv = tmpdir / "price_history_training_backfilled.csv"
    db_path = tmpdir / "test.db"

    # Step 1 — generate synthetic CSV using the builder's CLI entrypoint.
    rc_build = builder.main(["--output", str(input_csv), "--seed", "42"])
    assert rc_build == 0, "builder.main returned non-zero"
    assert input_csv.exists(), "synthetic CSV was not written"

    # Step 2 — run the Agent-4 loader (skipping the Postgres DDL print noise).
    rc_load = loader.main([
        "--input", str(input_csv),
        "--output", str(output_csv),
        "--db", str(db_path),
        "--skip-postgres-hint",
    ])
    assert rc_load == 0, "loader.main returned non-zero"
    assert output_csv.exists(), "backfilled CSV was not written"
    assert db_path.exists(), "sqlite DB was not written"

    return {
        "input_csv": str(input_csv),
        "output_csv": str(output_csv),
        "db_path": str(db_path),
    }


# ---------------------------------------------------------------------------
# Test 1 — all three Grapes seasons loaded with expected volume totals
# ---------------------------------------------------------------------------

def test_all_three_seasons_loaded(workspace: dict[str, str]) -> None:
    db_path = workspace["db_path"]
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("""
            SELECT season_label, estimated_total_volume_mt
            FROM amed_history
            WHERE region = ? AND crop_type = ?
            ORDER BY season_label
        """, (loader.REGION, "Grapes"))
        rows = {label: volume for label, volume in cur.fetchall()}
    finally:
        conn.close()

    assert set(rows.keys()) == {"2022-23", "2023-24", "2024-25"}, (
        f"Expected the three SDD-seeded seasons, got: {sorted(rows.keys())}")
    assert rows["2022-23"] == pytest.approx(11420.0)
    assert rows["2023-24"] == pytest.approx(11680.0)
    assert rows["2024-25"] == pytest.approx(11890.0)


# ---------------------------------------------------------------------------
# Test 2 — backfilled CSV covers Jan 2022 -> Apr 2026
# ---------------------------------------------------------------------------

def test_backfill_covers_2022_2026(workspace: dict[str, str]) -> None:
    df = pd.read_csv(workspace["output_csv"])

    # Date coverage on Dry_Grapes alone is sufficient — the CSV has the same
    # weekly anchor for both commodities.
    df["arrival_date"] = pd.to_datetime(df["arrival_date"])

    min_date = df["arrival_date"].min()
    max_date = df["arrival_date"].max()
    assert min_date <= pd.Timestamp("2022-01-31"), (
        f"Backfilled CSV does not reach back to Jan 2022 (min={min_date})")
    assert max_date >= pd.Timestamp("2026-04-27"), (
        f"Backfilled CSV does not reach Apr 2026 (max={max_date})")

    # AMED features must be populated for rows that fall inside a season.
    in_season = df[df["amed_season_week"].fillna(0).astype(int) > 0]
    assert len(in_season) > 0, "No in-season rows had AMED features set"
    for col in ("amed_belt_volume_mt", "amed_fields_harvesting",
                "amed_health_pct_good", "amed_season_week"):
        assert in_season[col].notna().all(), (
            f"In-season rows have NaN in column {col}")

    # Spot-check: at least one in-season row exists for each season window.
    expected_windows = [
        ("Dry_Grapes", "2023-04-01", "2023-05-15"),
        ("Dry_Grapes", "2024-04-05", "2024-05-10"),
        ("Dry_Grapes", "2025-03-28", "2025-05-08"),
        ("Pomegranate", "2023-01-15", "2023-03-05"),
        ("Pomegranate", "2024-01-14", "2024-03-04"),
        ("Pomegranate", "2025-01-13", "2025-03-03"),
    ]
    for commodity, start, end in expected_windows:
        mask = ((df["commodity"] == commodity)
                & (df["arrival_date"] >= pd.Timestamp(start))
                & (df["arrival_date"] <= pd.Timestamp(end))
                & (df["amed_belt_volume_mt"] > 0))
        assert mask.sum() > 0, (
            f"No backfilled rows in window {commodity} {start}..{end}")


# ---------------------------------------------------------------------------
# Test 3 — weekly bell-curve distribution sums to within 1% of season total
# ---------------------------------------------------------------------------

def test_weekly_distribution_sums_to_season_total(
    workspace: dict[str, str],
) -> None:
    # Read history out of the DB the loader actually populated, then build
    # the per-week distribution exactly as the loader does. We assert against
    # the SUM of the bell-curve weights (which by construction is 1) and the
    # SUM of generated weekly volumes (which by construction equals the
    # season total, modulo float roundoff). 1% tolerance per the SDD.
    rows = loader.load_history_from_db(workspace["db_path"])
    assert rows, "No amed_history rows found in DB"

    for row in rows:
        weekly_pairs = loader._distribute_season_volume(row)
        assert weekly_pairs, f"Empty distribution for {row.season_label}"
        weekly_sum = sum(volume for _, volume in weekly_pairs)
        # Within 1% of the seeded season total.
        assert abs(weekly_sum - row.estimated_total_volume_mt) <= (
            0.01 * row.estimated_total_volume_mt), (
            f"{row.crop_type} {row.season_label}: weekly sum {weekly_sum:.1f} "
            f"vs total {row.estimated_total_volume_mt}")

    # Also check that the per-(commodity, mandi) volume_index sums match the
    # season totals (this is what actually ends up in the CSV).
    volume_index = loader.build_weekly_volume_index(rows)
    by_crop_total: dict[str, float] = {}
    for (commodity, _mandi), bucket in volume_index.items():
        by_crop_total[commodity] = sum(
            payload["amed_belt_volume_mt"] for payload in bucket.values()
        )

    expected_grapes_total = sum(
        r.estimated_total_volume_mt for r in rows if r.crop_type == "Grapes"
    )
    expected_pom_total = sum(
        r.estimated_total_volume_mt for r in rows
        if r.crop_type == "Pomegranate"
    )
    assert abs(by_crop_total.get("Dry_Grapes", 0)
               - expected_grapes_total) <= 0.01 * expected_grapes_total
    assert abs(by_crop_total.get("Pomegranate", 0)
               - expected_pom_total) <= 0.01 * expected_pom_total


# ---------------------------------------------------------------------------
# Test 4 — 2025 Dry Grapes price spike is present in the synthetic CSV
# ---------------------------------------------------------------------------

def test_synthetic_2025_price_spike_present(
    workspace: dict[str, str],
) -> None:
    df = pd.read_csv(workspace["input_csv"])
    df["arrival_date"] = pd.to_datetime(df["arrival_date"])

    apr_2025 = df[(df["commodity"] == "Dry_Grapes")
                  & (df["arrival_date"] >= "2025-04-01")
                  & (df["arrival_date"] <= "2025-04-30")]
    assert not apr_2025.empty, "No April 2025 Dry_Grapes rows in synthetic CSV"
    mean_price = float(apr_2025["price_modal_kg"].mean())
    assert mean_price > 200, (
        f"April 2025 Dry_Grapes mean price expected > 200, got {mean_price:.2f}")

    # Sanity: the spike should be much higher than a non-spike year.
    apr_2023 = df[(df["commodity"] == "Dry_Grapes")
                  & (df["arrival_date"] >= "2023-04-01")
                  & (df["arrival_date"] <= "2023-04-30")]
    assert not apr_2023.empty
    assert mean_price > float(apr_2023["price_modal_kg"].mean()) + 100, (
        "2025 spike should be at least Rs.100/kg above 2023 baseline")
