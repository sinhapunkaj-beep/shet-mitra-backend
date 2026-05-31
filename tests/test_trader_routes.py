"""Integration tests for the trader-intelligence FastAPI routes (Agent 5).

These tests exercise ``routes/trader.py`` end-to-end through ``main.app``.
Each test runs against a fresh seeded SQLite mirror created by
``scripts/seed_local_sqlite.build_local_db`` so the trader seed rows from
SDD Section 10 Agent 1 are guaranteed to be present.

The MockSender outbox is rebuilt per test so any WhatsApp dispatch can
be asserted on without bleed between cases.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import trader_db, whatsapp_db, whatsapp_sender  # noqa: E402
from scripts import seed_local_sqlite  # noqa: E402


SEED_TRADER_BASIC_ID = seed_local_sqlite.SEED_TRADER_BASIC_ID
SEED_TRADER_STANDARD_ID = seed_local_sqlite.SEED_TRADER_STANDARD_ID
SEED_TRADER_PREMIUM_ID = seed_local_sqlite.SEED_TRADER_PREMIUM_ID
SEED_TRADER_TRIAL_STANDARD_ID = seed_local_sqlite.SEED_TRADER_TRIAL_STANDARD_ID
SEED_TRADER_TRIAL_BASIC_ID = seed_local_sqlite.SEED_TRADER_TRIAL_BASIC_ID


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a fresh seeded SQLite DB and point all consumers at it."""
    db_path = tmp_path / "trader_test.db"
    seed_local_sqlite.build_local_db(db_path)
    monkeypatch.setenv("SHETMITRA_DB_PATH", str(db_path))
    whatsapp_db.set_db_path(db_path)
    trader_db.reset_missing_table_warning()
    yield db_path
    whatsapp_db.reset_db_path()


@pytest.fixture()
def mock_sender(tmp_path: Path) -> whatsapp_sender.MockSender:
    outbox = tmp_path / "outbox.jsonl"
    sender = whatsapp_sender.MockSender(outbox_path=outbox)
    whatsapp_sender.set_sender(sender)
    yield sender
    whatsapp_sender.reset_sender()


@pytest.fixture()
def client(seeded_db: Path, mock_sender: whatsapp_sender.MockSender) -> TestClient:
    """Return a TestClient pointed at the live ``main.app``.

    main.app is imported lazily so SHETMITRA_DB_PATH is already set when
    routes.trader runs its first connect.
    """
    from main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_register_trader_success(client: TestClient, seeded_db: Path) -> None:
    body = {
        "full_name": "Test Trader",
        "mobile": "9991110001",
        "business_name": "Test Co",
        "location": "Pune",
        "district": "Pune",
        "commodities": ["Dry Grapes"],
        "tier": "BASIC",
    }
    resp = client.post("/traders/register", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mobile"] == "9991110001"
    assert data["full_name"] == "Test Trader"
    assert data["id"]

    # Round-trip via DB to be sure.
    row = trader_db.get_trader_by_mobile("9991110001")
    assert row is not None
    assert row["full_name"] == "Test Trader"


def test_register_trader_duplicate_mobile_409(client: TestClient) -> None:
    body = {"full_name": "Dup A", "mobile": "9992220001"}
    r1 = client.post("/traders/register", json=body)
    assert r1.status_code == 200, r1.text

    body2 = {"full_name": "Dup B", "mobile": "9992220001"}
    r2 = client.post("/traders/register", json=body2)
    assert r2.status_code == 409


def test_get_trader_by_id_includes_days_until_trial_end_for_trial(
    client: TestClient,
) -> None:
    resp = client.get(f"/traders/{SEED_TRADER_TRIAL_STANDARD_ID}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == SEED_TRADER_TRIAL_STANDARD_ID
    assert data["subscription_status"] == "TRIAL"
    assert "days_until_trial_end" in data
    assert data["days_until_trial_end"] is not None
    assert 0 <= int(data["days_until_trial_end"]) <= 28


def test_subscribe_returns_razorpay_link(client: TestClient) -> None:
    resp = client.post(
        f"/traders/{SEED_TRADER_BASIC_ID}/subscribe",
        json={"tier": "STANDARD"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tier"] == "STANDARD"
    link = data.get("link") or ""
    assert link.startswith("https://rzp.io/"), link
    assert data.get("subscription_id")
    assert data.get("amount") in (7000, 7000.0)


def test_generate_weekly_creates_report_no_send(
    client: TestClient, seeded_db: Path
) -> None:
    resp = client.post(
        "/intelligence/generate-weekly",
        json={"commodity": "Dry Grapes", "region": "Tasgaon"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["signal"] in {"BUY", "SELL", "HOLD"}
    assert data["recipients_count"] == 0
    assert data["delivered_count"] == 0
    assert data["content_preview"]
    report_id = data["report_id"]

    # Verify row in DB.
    with sqlite3.connect(str(seeded_db)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM intelligence_reports WHERE id = ?", (report_id,)
        )
        row = cur.fetchone()
        assert row is not None
        assert row["signal"] in {"BUY", "SELL", "HOLD"}
        assert row["content_english"]
        assert row["recipients_count"] == 0


def test_generate_weekly_with_send_dispatches_to_all_active(
    client: TestClient, seeded_db: Path
) -> None:
    resp = client.post(
        "/intelligence/generate-weekly",
        json={"commodity": "Dry Grapes", "region": "Tasgaon", "send": True},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["recipients_count"] > 0
    assert data["delivered_count"] > 0
    report_id = data["report_id"]

    # Check report_deliveries rows.
    with sqlite3.connect(str(seeded_db)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT trader_id FROM report_deliveries WHERE report_id = ?",
            (report_id,),
        )
        delivery_trader_ids = {row["trader_id"] for row in cur.fetchall()}

    # Every ACTIVE seed trader should have a delivery row.
    active_ids = {
        SEED_TRADER_BASIC_ID,
        SEED_TRADER_STANDARD_ID,
        SEED_TRADER_PREMIUM_ID,
    }
    assert delivery_trader_ids & active_ids, (
        f"Expected at least one ACTIVE trader in deliveries, got "
        f"{delivery_trader_ids}"
    )


def test_generate_flash_enforces_weekly_limit_429(
    client: TestClient, seeded_db: Path
) -> None:
    # Pre-insert 3 flash_alert_triggers rows for this week.
    now = datetime.now(timezone.utc)
    monday_iso = (
        now - timedelta(days=now.weekday())
    ).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
    with sqlite3.connect(str(seeded_db)) as conn:
        for _ in range(3):
            conn.execute(
                """
                INSERT INTO flash_alert_triggers (
                    id, commodity, trigger_type, alert_sent, detected_at
                ) VALUES (?, ?, 'PRICE_DROP', 0, ?)
                """,
                (str(uuid.uuid4()), "Dry Grapes", monday_iso),
            )
        conn.commit()

    resp = client.post(
        "/intelligence/generate-flash",
        json={
            "commodity": "Dry Grapes",
            "trigger_type": "PRICE_DROP",
            "signal": "IMMEDIATE_BUY",
            "current_price": 100.0,
            "fair_value": 120.0,
        },
    )
    assert resp.status_code == 429, resp.text
    assert "limit" in resp.json()["detail"].lower()


def test_intelligence_reports_list_pagination(
    client: TestClient, seeded_db: Path
) -> None:
    # Generate 3 weekly reports.
    for _ in range(3):
        r = client.post(
            "/intelligence/generate-weekly",
            json={"commodity": "Dry Grapes", "region": "Tasgaon"},
        )
        assert r.status_code == 200, r.text

    resp = client.get("/intelligence/reports?limit=2&offset=0")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2


def test_premium_query_only_for_premium_tier(client: TestClient) -> None:
    resp = client.post(
        f"/traders/{SEED_TRADER_BASIC_ID}/query",
        json={"query_text": "When should I sell?"},
    )
    assert resp.status_code == 403, resp.text


def test_premium_query_persists_and_responds(
    client: TestClient, seeded_db: Path, mock_sender: whatsapp_sender.MockSender
) -> None:
    resp = client.post(
        f"/traders/{SEED_TRADER_PREMIUM_ID}/query",
        json={"query_text": "Should I hold Alphonso through next week?"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["query_id"]
    assert data["response_text"]
    assert data["sent_to_mobile"]

    # trader_queries row inserted.
    with sqlite3.connect(str(seeded_db)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM trader_queries WHERE trader_id = ?",
            (SEED_TRADER_PREMIUM_ID,),
        )
        rows = cur.fetchall()
        assert rows, "Expected a trader_queries row"
        assert any(
            (r["response_text"] or "").strip() for r in rows
        ), "Expected at least one row with a response_text"

    # MockSender outbox should contain the response.
    messages = mock_sender.read_messages()
    assert messages, "Expected at least one outbox message"
    assert any(
        data["response_text"] and data["response_text"][:40] in m["body"]
        for m in messages
    ), f"Expected response to be sent via WhatsApp. Outbox={messages}"


def test_traders_analytics_shape(client: TestClient) -> None:
    resp = client.get("/traders/analytics")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    expected_keys = {
        "total_traders",
        "active_subscribers",
        "trial_users",
        "paused_users",
        "cancelled_users",
        "mrr",
        "this_month_revenue",
        "last_month_revenue",
        "by_tier",
        "trial_conversion_rate_pct",
        "avg_query_count_premium",
    }
    assert expected_keys <= set(data.keys()), (
        f"missing keys: {expected_keys - set(data.keys())}"
    )

    assert isinstance(data["total_traders"], int)
    assert isinstance(data["active_subscribers"], int)
    assert isinstance(data["trial_users"], int)
    assert isinstance(data["paused_users"], int)
    assert isinstance(data["cancelled_users"], int)
    assert isinstance(data["mrr"], (int, float))
    assert isinstance(data["this_month_revenue"], (int, float))
    assert isinstance(data["last_month_revenue"], (int, float))
    assert isinstance(data["by_tier"], dict)
    assert {"basic", "standard", "premium"} <= set(data["by_tier"].keys())
    assert all(isinstance(v, int) for v in data["by_tier"].values())
    # Nullable fields: float or None.
    assert data["trial_conversion_rate_pct"] is None or isinstance(
        data["trial_conversion_rate_pct"], (int, float)
    )
    assert data["avg_query_count_premium"] is None or isinstance(
        data["avg_query_count_premium"], (int, float)
    )

    # Sanity: 3 ACTIVE seed traders + 2 TRIAL seed traders.
    assert data["total_traders"] >= 5
    assert data["active_subscribers"] >= 3
    assert data["trial_users"] >= 2


def test_main_app_mounts_trader_router() -> None:
    from main import app

    paths = [getattr(r, "path", "") for r in app.routes]
    assert any(p.startswith("/traders/") for p in paths), paths
    assert any(p.startswith("/intelligence/") for p in paths), paths
