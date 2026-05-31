"""Tests for the Mango Agent-2 market data importers.

These tests exercise the synthetic mango + forex generators end-to-end
against a temporary SQLite DB so the project's real ``data/test.db`` is
never touched.

* ``test_synthetic_csv_has_all_varieties``  - all 5 varieties present.
* ``test_only_mango_season_months``         - no out-of-season rows.
* ``test_bearing_year_alternates``          - 2024 OFF, 2025 ON.
* ``test_alphonso_2024_export_spike``       - 2024 Alphonso mean > Rs.500.
* ``test_totapuri_more_stable_than_alphonso``
                                            - CV(Totapuri) < CV(Alphonso).
* ``test_forex_csv_monotonic_trend_roughly``
                                            - 2026 mean USD/INR > 2015 + 10.
* ``test_load_to_sqlite_inserts_rows``      - loader inserts mango rows
                                              when Agent 1 schema is in place;
                                              skipped otherwise.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import import_mango_market_data as mango_importer  # noqa: E402
from scripts import import_usd_inr_forex as forex_importer  # noqa: E402
from scripts import load_mango_data_to_sqlite as loader  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures - generate once, reuse across tests for speed.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mango_df() -> pd.DataFrame:
    return mango_importer.build_synthetic_dataframe(
        year_start=2015, year_end=2026, seed=42
    )


@pytest.fixture(scope="module")
def forex_df() -> pd.DataFrame:
    return forex_importer.build_synthetic_dataframe(
        year_start=2015, year_end=2026, seed=42
    )


@pytest.fixture(scope="module")
def workspace(
    tmp_path_factory: pytest.TempPathFactory,
    mango_df: pd.DataFrame,
    forex_df: pd.DataFrame,
) -> dict[str, str]:
    """Write the two CSVs to a tmpdir and return paths for downstream tests."""
    tmpdir = tmp_path_factory.mktemp("mango_agent2")
    mango_csv = tmpdir / "price_history_mango_synthetic.csv"
    forex_csv = tmpdir / "forex_rates_synthetic.csv"
    db_path = tmpdir / "test.db"

    mango_df.to_csv(mango_csv, index=False)
    forex_df.to_csv(forex_csv, index=False)

    return {
        "mango_csv": str(mango_csv),
        "forex_csv": str(forex_csv),
        "db_path": str(db_path),
    }


# ---------------------------------------------------------------------------
# Mango synthetic dataset assertions
# ---------------------------------------------------------------------------

def test_synthetic_csv_has_all_varieties(mango_df: pd.DataFrame) -> None:
    expected = {"Alphonso", "Kesar", "Dasheri", "Totapuri", "Banganapalli"}
    assert set(mango_df["variety"].unique()) == expected, (
        f"varieties present: {sorted(set(mango_df['variety'].unique()))}"
    )


def test_only_mango_season_months(mango_df: pd.DataFrame) -> None:
    out_of_season = mango_df[~mango_df["month"].isin([3, 4, 5, 6, 7])]
    assert out_of_season.empty, (
        f"found {len(out_of_season)} rows outside months 3..7"
    )


def test_bearing_year_alternates(mango_df: pd.DataFrame) -> None:
    flag_by_year = (
        mango_df.groupby("year")["bearing_year_flag"].first().to_dict()
    )
    assert flag_by_year.get(2024) == 0, (
        f"expected 2024 to be OFF (0), got {flag_by_year.get(2024)}"
    )
    assert flag_by_year.get(2025) == 1, (
        f"expected 2025 to be ON (1), got {flag_by_year.get(2025)}"
    )


def test_alphonso_2024_export_spike(mango_df: pd.DataFrame) -> None:
    alphonso_2024 = mango_df[
        (mango_df["variety"] == "Alphonso") & (mango_df["year"] == 2024)
    ]
    assert not alphonso_2024.empty, "no 2024 Alphonso rows present"
    mean_price = float(alphonso_2024["price_modal_kg"].mean())
    assert mean_price > 500.0, (
        f"expected 2024 Alphonso mean > Rs.500 (export spike), "
        f"got Rs.{mean_price:.2f}"
    )


def test_totapuri_more_stable_than_alphonso(mango_df: pd.DataFrame) -> None:
    """CV (std/mean) for Totapuri should be lower than for Alphonso."""

    def cv(sub: pd.DataFrame) -> float:
        mean = float(sub["price_modal_kg"].mean())
        std = float(sub["price_modal_kg"].std())
        if mean == 0:
            return float("inf")
        return std / mean

    cv_toto = cv(mango_df[mango_df["variety"] == "Totapuri"])
    cv_alph = cv(mango_df[mango_df["variety"] == "Alphonso"])
    assert cv_toto < cv_alph, (
        f"expected Totapuri CV < Alphonso CV, "
        f"got Totapuri={cv_toto:.4f} Alphonso={cv_alph:.4f}"
    )


# ---------------------------------------------------------------------------
# Forex synthetic dataset assertion
# ---------------------------------------------------------------------------

def test_forex_csv_monotonic_trend_roughly(forex_df: pd.DataFrame) -> None:
    df = forex_df.copy()
    df["year"] = df["date"].str.slice(0, 4).astype(int)
    mean_2015 = float(df[df["year"] == 2015]["usd_inr_rate"].mean())
    mean_2026 = float(df[df["year"] == 2026]["usd_inr_rate"].mean())
    diff = mean_2026 - mean_2015
    assert diff >= 10.0, (
        f"expected 2026 mean USD/INR to be >= 10 above 2015 mean, "
        f"got 2015={mean_2015:.3f} 2026={mean_2026:.3f} diff={diff:.3f}"
    )


# ---------------------------------------------------------------------------
# SQLite loader
# ---------------------------------------------------------------------------

def test_load_to_sqlite_inserts_rows(workspace: dict[str, str]) -> None:
    """Run the loader with --bootstrap-schema so we exercise the full path
    even though Agent 1's SQLite seeder may not have run on the real
    data/test.db. We use a tmp DB here so production data is untouched.
    """
    rc = loader.main([
        "--mango-csv", workspace["mango_csv"],
        "--forex-csv", workspace["forex_csv"],
        "--db", workspace["db_path"],
        "--bootstrap-schema",
    ])
    assert rc == 0, "loader.main returned non-zero"

    conn = sqlite3.connect(workspace["db_path"])
    try:
        # If the bootstrap path created the table, we expect rows. If it was
        # somehow skipped, the test should explicitly note the degradation
        # rather than silently pass.
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='price_history_training';"
        )
        if cur.fetchone() is None:
            pytest.skip(
                "price_history_training table not present - "
                "Agent 1 migration 007 has not been mirrored locally."
            )

        cur = conn.execute(
            "SELECT COUNT(*) FROM price_history_training "
            "WHERE commodity = 'Mango';"
        )
        mango_count = cur.fetchone()[0]
        assert mango_count > 0, (
            "expected at least one Mango row inserted "
            "into price_history_training"
        )

        cur = conn.execute("SELECT COUNT(*) FROM forex_rates;")
        forex_count = cur.fetchone()[0]
        assert forex_count > 0, "expected forex_rates to have rows"
    finally:
        conn.close()
