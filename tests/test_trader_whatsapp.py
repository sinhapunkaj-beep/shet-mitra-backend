"""End-to-end tests for the trader-intelligence WhatsApp flow (Agent 3)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import (  # noqa: E402
    trader_db,
    trader_whatsapp,
    webhooks_aisensy,
    webhooks_trader,
    whatsapp_db,
    whatsapp_sender,
)
from scripts import seed_local_sqlite  # noqa: E402


SEED_BASIC_ID = seed_local_sqlite.SEED_TRADER_BASIC_ID
SEED_STANDARD_ID = seed_local_sqlite.SEED_TRADER_STANDARD_ID
SEED_PREMIUM_ID = seed_local_sqlite.SEED_TRADER_PREMIUM_ID
SEED_TRIAL_STANDARD_ID = seed_local_sqlite.SEED_TRADER_TRIAL_STANDARD_ID
SEED_TRIAL_BASIC_ID = seed_local_sqlite.SEED_TRADER_TRIAL_BASIC_ID

SEED_BASIC_MOBILE = "9870000001"
SEED_STANDARD_MOBILE = "9870000002"
SEED_PREMIUM_MOBILE = "9870000003"
SEED_TRIAL_STANDARD_MOBILE = "9870000004"
SEED_TRIAL_BASIC_MOBILE = "9870000005"


def _build_seeded_db(db_path: Path) -> None:
    """Create the local mirror, then add the trader-intelligence schema +
    seed rows on top.

    Agent 1's ``build_local_db`` doesn't wire in the trader schema yet, so
    we call ``ensure_trader_intelligence_schema`` + ``_seed_trader_intelligence``
    directly to make this test self-sufficient regardless of Agent 1's
    progress.

    We also need to rebuild ``whatsapp_sessions`` so its ``collection_flow``
    CHECK accepts the new 'trader' value. Migration 006 on the live Postgres
    will widen this constraint; until Agent 1 updates the SQLite seed we
    patch it here in the test fixture.
    """
    seed_local_sqlite.build_local_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        seed_local_sqlite.ensure_trader_intelligence_schema(conn)
        seed_local_sqlite._seed_trader_intelligence(conn)

        # Replace whatsapp_sessions with a version whose CHECK accepts
        # 'trader' as a valid collection_flow. Preserve existing rows.
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id, mobile_number, farmer_id, current_step, "
            "collection_flow, session_data, created_at, updated_at, "
            "expires_at FROM whatsapp_sessions"
        ).fetchall()
        cur.execute("DROP TABLE whatsapp_sessions")
        cur.execute(
            """
            CREATE TABLE whatsapp_sessions (
                id TEXT PRIMARY KEY,
                mobile_number TEXT NOT NULL UNIQUE,
                farmer_id TEXT REFERENCES farmers(id),
                current_step TEXT,
                collection_flow TEXT DEFAULT 'booking'
                    CHECK (collection_flow IN (
                        'booking',
                        'variety_collection',
                        'registration',
                        'harvest_actuals',
                        'trader'
                    )),
                session_data TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT
            )
            """
        )
        cur.executemany(
            """
            INSERT INTO whatsapp_sessions
                (id, mobile_number, farmer_id, current_step,
                 collection_flow, session_data,
                 created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_whatsapp_sessions_mobile "
            "ON whatsapp_sessions(mobile_number)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_whatsapp_sessions_farmer "
            "ON whatsapp_sessions(farmer_id)"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def seeded_db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _build_seeded_db(db_path)
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


def _post_incoming(mobile: str, body: str):
    """Hit the AISensy webhook the same way AiSensy would in production."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(webhooks_aisensy.router)
    app.include_router(webhooks_trader.router)
    client = TestClient(app)
    return client.post(
        "/webhooks/aisensy/incoming",
        json={
            "contacts": [{"wa_id": "91" + mobile}],
            "messages": [
                {
                    "id": f"wamid.test-{mobile}-{body[:5]}",
                    "from": "91" + mobile,
                    "text": {"body": body},
                }
            ],
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_new_mobile_creates_trader_and_sends_welcome(
    seeded_db, mock_sender
):
    unknown_mobile = "9799999999"
    # Pre-condition — really not in the DB.
    assert trader_db.get_trader_by_mobile(unknown_mobile) is None

    response = _post_incoming(unknown_mobile, "Hello there")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in ("routed", "onboarded")
    assert payload["flow"] == "trader"

    trader = trader_db.get_trader_by_mobile(unknown_mobile)
    assert trader is not None
    assert trader["subscription_status"] == "TRIAL"
    assert trader["subscription_tier"] == "BASIC"

    messages = mock_sender.read_messages()
    assert len(messages) == 1
    body = messages[0]["body"]
    assert "Welcome to ShetMitra Trader Intelligence" in body
    assert "✅" in body
    assert "Sahyadri Krushi Intelligence" in body
    assert "FREE 4-week trial" in body


def test_upgrade_command_sends_tier_menu(seeded_db, mock_sender):
    response = _post_incoming(SEED_BASIC_MOBILE, "UPGRADE")
    assert response.status_code == 200, response.text
    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert bodies, "expected at least one outbound message"
    tier_msg = bodies[-1]
    assert "BASIC" in tier_msg
    assert "STANDARD" in tier_msg
    assert "PREMIUM" in tier_msg
    assert "Rs 3,000" in tier_msg
    assert "Rs 7,000" in tier_msg
    assert "Rs 15,000" in tier_msg

    session = whatsapp_db.get_session_by_mobile(SEED_BASIC_MOBILE)
    assert session is not None
    assert session["current_step"] == webhooks_trader.Step.AWAITING_TIER_CHOICE
    assert session["collection_flow"] == "trader"


def test_select_standard_triggers_payment_link(seeded_db, mock_sender):
    # First put them in AWAITING_TIER_CHOICE...
    _post_incoming(SEED_BASIC_MOBILE, "UPGRADE")
    # ...then they reply S for Standard.
    response = _post_incoming(SEED_BASIC_MOBILE, "S")
    assert response.status_code == 200, response.text

    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert bodies, "expected at least one outbound message"
    pay_msg = bodies[-1]
    assert (
        "https://" in pay_msg or "rzp.io" in pay_msg
    ), f"expected payment link, got: {pay_msg!r}"
    assert "STANDARD" in pay_msg

    session = whatsapp_db.get_session_by_mobile(SEED_BASIC_MOBILE)
    assert session["current_step"] == webhooks_trader.Step.AWAITING_PAYMENT


def test_cancel_command_sets_status_cancelled(seeded_db, mock_sender):
    response = _post_incoming(SEED_STANDARD_MOBILE, "CANCEL")
    assert response.status_code == 200, response.text

    trader = trader_db.get_trader_by_mobile(SEED_STANDARD_MOBILE)
    assert trader["subscription_status"] == "CANCELLED"

    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert any("Subscription cancelled" in b for b in bodies)
    session = whatsapp_db.get_session_by_mobile(SEED_STANDARD_MOBILE)
    assert session["current_step"] == webhooks_trader.Step.CANCELLED


def test_premium_query_routed(seeded_db, mock_sender):
    # PREMIUM trader sends a market question — should NOT match any command
    # tokens, so it's routed to _handle_premium_query.
    response = _post_incoming(
        SEED_PREMIUM_MOBILE, "What is Alphonso price next week?"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "query_handled"
    assert payload["query_id"], "expected a trader_queries row id"

    queries = trader_db.list_queries_for_trader(SEED_PREMIUM_ID)
    assert len(queries) == 1
    assert "Alphonso" in queries[0]["query_text"]
    assert queries[0]["response_text"], "response should have been persisted"

    messages = mock_sender.read_messages()
    assert messages, "expected response to be sent over WhatsApp"
    # Counter should have ticked up.
    trader = trader_db.get_trader_by_mobile(SEED_PREMIUM_MOBILE)
    assert (trader["query_count_this_month"] or 0) >= 1


def test_subscription_reminder_template_exact():
    text = webhooks_trader.build_subscription_reminder("Ramesh", 3)
    assert "Hello Ramesh" in text
    assert "3 days" in text
    assert "Rs 3,000" in text
    assert "Rs 7,000" in text
    assert "Rs 15,000" in text


def _insert_stub_report(db_path: Path, report_id: str) -> None:
    """Insert a minimal intelligence_reports row so the FK on
    report_deliveries.report_id is satisfied.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO intelligence_reports
                (id, report_type, commodity, report_date, content_english)
            VALUES (?, 'FLASH', 'Dry Grapes', '2026-05-31', 'stub')
            """,
            (report_id,),
        )
        conn.commit()
    finally:
        conn.close()


def test_flash_alert_only_to_standard_and_premium(seeded_db, mock_sender):
    # All five seeded traders.
    trader_ids = [
        SEED_BASIC_ID,            # BASIC ACTIVE  -> filtered (not eligible)
        SEED_STANDARD_ID,         # STANDARD ACTIVE -> sent
        SEED_PREMIUM_ID,          # PREMIUM ACTIVE -> sent
        SEED_TRIAL_STANDARD_ID,   # STANDARD TRIAL -> sent (trial gets STANDARD content)
        SEED_TRIAL_BASIC_ID,      # BASIC TRIAL -> filtered
    ]
    report_id = "report-flash-test"
    _insert_stub_report(seeded_db, report_id)
    results = trader_whatsapp.send_flash_alert(
        trader_ids, report_id, "FLASH ALERT — Dry Grapes"
    )

    sent = [r for r in results if r["status"] == "sent"]
    skipped = [r for r in results if r["status"] == "skipped"]
    assert len(sent) == 3, f"expected STANDARD+PREMIUM+TRIAL_STANDARD, got {sent}"
    assert len(skipped) == 2, (
        f"expected 2 BASIC-tier traders filtered out, got {skipped}"
    )

    deliveries = trader_db.list_report_deliveries(report_id)
    assert len(deliveries) == 3
    assert all(d["delivery_status"] == "SENT" for d in deliveries)


def test_daily_update_only_to_premium(seeded_db, mock_sender, caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="api.trader_whatsapp")
    result = trader_whatsapp.send_daily_update(
        SEED_BASIC_ID, "Daily price update content"
    )
    assert result["status"] == "skipped"
    assert "tier not eligible" in result["reason"]
    assert any(
        "not eligible" in rec.message for rec in caplog.records
    )
    # No delivery row should have been written.
    deliveries = trader_db.list_report_deliveries("")
    assert deliveries == []


def test_existing_farmer_not_treated_as_trader(seeded_db, mock_sender):
    """Seeded farmer mobile 9876543210 has a 'booking' session in the
    seed. The AISensy router should route to webhooks_booking, NOT to the
    trader onboarding flow.
    """
    response = _post_incoming("9876543210", "Hello")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["flow"] != "trader", f"unexpectedly routed to trader: {payload}"
    # No trader row should have been created for this farmer mobile.
    assert trader_db.get_trader_by_mobile("9876543210") is None
    # No welcome message should have been queued.
    bodies = [m["body"] for m in mock_sender.read_messages()]
    assert not any(
        "Welcome to ShetMitra Trader Intelligence" in b for b in bodies
    ), "farmer should not have been welcomed as a trader"
