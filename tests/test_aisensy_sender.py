"""Tests for the AiSensy sender factory + error handling.

These tests never hit the real AiSensy endpoint. Network calls are
patched via ``unittest.mock``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import whatsapp_sender  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_sender_state(monkeypatch):
    """Ensure the module-level singleton is cleared around every test."""
    whatsapp_sender.reset_sender()
    # Strip any AiSensy env vars that may have leaked in from the shell.
    for key in (
        whatsapp_sender.ENV_AISENSY_MODE,
        whatsapp_sender.ENV_AISENSY_API_KEY,
        whatsapp_sender.ENV_AISENSY_CAMPAIGN_NAME,
        whatsapp_sender.ENV_AISENSY_USERNAME,
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    whatsapp_sender.reset_sender()


def test_get_sender_returns_mock_by_default():
    sender = whatsapp_sender.get_sender()
    assert isinstance(sender, whatsapp_sender.MockSender)


def test_get_sender_returns_aisensy_when_key_set(monkeypatch):
    monkeypatch.setenv(whatsapp_sender.ENV_AISENSY_MODE, "aisensy")
    monkeypatch.setenv(whatsapp_sender.ENV_AISENSY_API_KEY, "test123")
    sender = whatsapp_sender.get_sender()
    assert isinstance(sender, whatsapp_sender.AisensySender)
    assert sender.api_key == "test123"


def test_aisensy_sender_handles_http_error():
    sender = whatsapp_sender.AisensySender(api_key="test123")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "internal server error"

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    with patch("httpx.Client", return_value=mock_client):
        result = sender.send("9876543210", "hi")

    assert result["status"] == "error"
    assert result["code"] == 500
    assert result["mode"] == "aisensy"
    assert "internal server error" in result["body"]
