"""WhatsApp sender abstraction.

Provides:
    * ``WhatsAppSender`` protocol — anything with ``send(to, body)``.
    * ``MockSender`` — default sender. Appends one JSON line per message
      to ``data/whatsapp_outbox.jsonl`` and returns a queued response.
      Tests inject a fresh MockSender pointed at a temporary outbox.
    * ``AisensySender`` — production sender that POSTs to the AiSensy
      v2 Campaign API.
    * ``get_sender()`` — factory honouring ``AISENSY_MODE`` (defaults
      to ``mock``). Falls back to MockSender if ``AISENSY_API_KEY`` is
      missing even when mode is ``aisensy``.
    * ``set_sender()`` / ``reset_sender()`` — test hooks so the rest of
      the package can resolve "the current sender" through one call.

Stdlib only, except for an optional ``httpx`` import used by
``AisensySender`` (falls back to ``urllib.request`` if httpx is absent).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTBOX = REPO_ROOT / "data" / "whatsapp_outbox.jsonl"

# Env var names (module-level so callers / tests can monkeypatch them).
ENV_AISENSY_API_KEY = "AISENSY_API_KEY"
ENV_AISENSY_CAMPAIGN_NAME = "AISENSY_CAMPAIGN_NAME"
ENV_AISENSY_USERNAME = "AISENSY_USERNAME"
ENV_AISENSY_MODE = "AISENSY_MODE"

DEFAULT_CAMPAIGN_NAME = "shet_mitra_variety_collection"
DEFAULT_USERNAME = "ShetMitra"
AISENSY_ENDPOINT = "https://backend.aisensy.com/campaign/t1/api/v2"
AISENSY_TIMEOUT = 15.0


def _format_destination(to: str) -> str:
    """Normalise a phone to ``91XXXXXXXXXX`` form.

    Strips any leading ``+`` or ``91`` country code, then re-prepends ``91``.
    Returns the original digits prefixed with ``91`` if no clean strip is
    possible — never raises.
    """
    if not to:
        return ""
    raw = str(to).strip()
    if raw.startswith("+"):
        raw = raw[1:]
    # Strip leading 91 only if the remainder looks like a 10-digit Indian number.
    if raw.startswith("91") and len(raw) >= 12:
        raw = raw[2:]
    return "91" + raw


@runtime_checkable
class WhatsAppSender(Protocol):
    """Anything that can deliver a WhatsApp text message."""

    def send(self, to: str, body: str) -> dict:  # pragma: no cover - protocol
        ...


class MockSender:
    """Sender that writes to a JSONL outbox instead of calling AiSensy."""

    def __init__(self, outbox_path: Optional[Path | str] = None) -> None:
        self.outbox_path = Path(outbox_path) if outbox_path else DEFAULT_OUTBOX
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def send(self, to: str, body: str) -> dict:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "to": to,
            "destination": _format_destination(to),
            "body": body,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.outbox_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return {"status": "queued", "mode": "mock"}

    def read_messages(self) -> list[dict]:
        """Helper for tests: read all queued messages from the outbox."""
        if not self.outbox_path.exists():
            return []
        with self.outbox_path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


class AisensySender:
    """Production AiSensy sender — POSTs to the v2 Campaign API.

    Calling code treats sender failures as recoverable, so non-2xx
    responses and network errors are caught here and returned as
    ``{"status": "error", ...}`` dicts. No exceptions escape ``send()``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        campaign_name: Optional[str] = None,
        username: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.getenv(ENV_AISENSY_API_KEY, "")
        self.campaign_name = (
            campaign_name
            or os.getenv(ENV_AISENSY_CAMPAIGN_NAME)
            or DEFAULT_CAMPAIGN_NAME
        )
        self.username = (
            username or os.getenv(ENV_AISENSY_USERNAME) or DEFAULT_USERNAME
        )

    def _build_payload(self, to: str, body: str) -> dict:
        return {
            "apiKey": self.api_key,
            "campaignName": self.campaign_name,
            "destination": _format_destination(to),
            "userName": self.username,
            "source": "shetmitra-variety-flow",
            "media": {},
            "templateParams": [body],
            "tags": ["variety_collection"],
        }

    def send(self, to: str, body: str) -> dict:
        if not self.api_key:
            return {
                "status": "error",
                "mode": "aisensy",
                "code": 0,
                "body": "AISENSY_API_KEY missing",
            }

        payload = self._build_payload(to, body)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            return self._post(payload, headers)
        except Exception as exc:  # noqa: BLE001 - sender errors must not raise
            LOG.warning("AiSensy send failed: %s", exc)
            return {
                "status": "error",
                "mode": "aisensy",
                "code": 0,
                "body": f"transport error: {exc}",
            }

    def _post(self, payload: dict, headers: dict) -> dict:
        """POST to AiSensy with httpx if available, else urllib."""
        try:
            import httpx  # type: ignore

            with httpx.Client(timeout=AISENSY_TIMEOUT) as client:
                resp = client.post(
                    AISENSY_ENDPOINT, json=payload, headers=headers
                )
            return self._parse_response(resp.status_code, resp.text)
        except ImportError:
            return self._post_urllib(payload, headers)

    def _post_urllib(self, payload: dict, headers: dict) -> dict:
        import urllib.error
        import urllib.request

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            AISENSY_ENDPOINT, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=AISENSY_TIMEOUT) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                return self._parse_response(resp.status, text)
        except urllib.error.HTTPError as exc:
            text = ""
            try:
                text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return self._parse_response(exc.code, text)

    def _parse_response(self, status_code: int, text: str) -> dict:
        if 200 <= status_code < 300:
            message_id: Optional[str] = None
            try:
                parsed = json.loads(text) if text else {}
                if isinstance(parsed, dict):
                    message_id = (
                        parsed.get("messageId")
                        or parsed.get("message_id")
                        or parsed.get("id")
                    )
            except (ValueError, TypeError):
                pass
            return {
                "status": "sent",
                "mode": "aisensy",
                "message_id": message_id,
                "raw_status": status_code,
            }
        return {
            "status": "error",
            "mode": "aisensy",
            "code": status_code,
            "body": (text or "")[:500],
        }


# Module-level singleton, swappable by tests through set_sender().
_current_sender: Optional[WhatsAppSender] = None
_sender_lock = threading.Lock()
_missing_key_warned = False


def get_sender() -> WhatsAppSender:
    """Return the active sender, honouring ``AISENSY_MODE`` (default mock).

    Returns :class:`AisensySender` only when ``AISENSY_MODE=aisensy`` AND
    ``AISENSY_API_KEY`` is set and non-empty. Otherwise returns
    :class:`MockSender`. Warns exactly once if mode is set but the key
    is missing.
    """
    global _current_sender, _missing_key_warned
    with _sender_lock:
        if _current_sender is not None:
            return _current_sender
        mode = os.getenv(ENV_AISENSY_MODE, "mock").strip().lower()
        api_key = os.getenv(ENV_AISENSY_API_KEY, "").strip()
        if mode in ("aisensy", "live", "production"):
            if api_key:
                _current_sender = AisensySender(api_key=api_key)
            else:
                if not _missing_key_warned:
                    logging.warning(
                        "AISENSY_MODE=%s but AISENSY_API_KEY is empty; "
                        "falling back to MockSender.",
                        mode,
                    )
                    _missing_key_warned = True
                _current_sender = MockSender()
        else:
            _current_sender = MockSender()
        return _current_sender


def set_sender(sender: WhatsAppSender) -> None:
    """Test hook: override the active sender."""
    global _current_sender
    with _sender_lock:
        _current_sender = sender


def reset_sender() -> None:
    """Test hook: clear the cached sender so get_sender re-reads env."""
    global _current_sender, _missing_key_warned
    with _sender_lock:
        _current_sender = None
        _missing_key_warned = False
