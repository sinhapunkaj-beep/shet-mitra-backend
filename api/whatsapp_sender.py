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


# --------------------------------------------------------------------------- #
# Region-aware sender name + footer (SDD §2.3)
# --------------------------------------------------------------------------- #

#: Default sender + region code applied when no region row is found.
DEFAULT_SENDER_NAME = "ShetMitra"
DEFAULT_REGION_CODE = "MH"

#: Static safety-net so unit tests / offline environments still resolve a
#: sensible sender name even when the regions table is missing.
_REGION_FALLBACK_SENDERS: dict[str, str] = {
    "MH": "ShetMitra",
    "JH": "Bagaan Sathi",
}

# Small in-memory caches so we never hit SQLite twice for the same farmer
# within a process lifetime. The values are immutable strings so a plain
# dict guarded by a lock is enough.
_FARMER_REGION_CACHE: dict[str, str] = {}
_REGION_SENDER_CACHE: dict[str, str] = {}
_REGION_CACHE_LOCK = threading.Lock()


def reset_region_cache() -> None:
    """Test hook: drop cached farmer→region and region→sender entries."""
    with _REGION_CACHE_LOCK:
        _FARMER_REGION_CACHE.clear()
        _REGION_SENDER_CACHE.clear()


def _lookup_region_code_for_farmer(farmer_id: str) -> Optional[str]:
    """Resolve ``farmer_id`` → ``region_code`` via ``api.whatsapp_db``.

    Returns ``None`` on any error (missing table, missing farmer, missing
    column) so callers can apply the default. We never raise — sender
    resolution must not crash the message-send path.
    """
    try:
        # Local import keeps this module importable in environments where
        # SQLite or the mirror DB isn't available (e.g. some CI runs).
        from api import whatsapp_db  # type: ignore
    except Exception as exc:  # noqa: BLE001
        LOG.debug("region lookup: whatsapp_db import failed: %s", exc)
        return None
    try:
        farmer = whatsapp_db.get_farmer_by_id(farmer_id)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("region lookup: get_farmer_by_id(%s) failed: %s", farmer_id, exc)
        return None
    if not isinstance(farmer, dict):
        return None
    code = farmer.get("region_code")
    if isinstance(code, str) and code.strip():
        return code.strip().upper()
    return None


def _lookup_sender_for_region(region_code: str) -> Optional[str]:
    """Resolve ``region_code`` → ``whatsapp_sender_name`` from ``regions``.

    Falls back to a small static table when the regions table is absent
    on the local SQLite mirror. Returns ``None`` only when both the DB
    and the static fallback have no entry for ``region_code``.
    """
    try:
        from api import whatsapp_db  # type: ignore
    except Exception as exc:  # noqa: BLE001
        LOG.debug("sender lookup: whatsapp_db import failed: %s", exc)
        return _REGION_FALLBACK_SENDERS.get(region_code)

    try:
        with whatsapp_db._connect() as conn:  # noqa: SLF001 - reuse helper
            if not whatsapp_db._table_exists(conn, "regions"):  # noqa: SLF001
                return _REGION_FALLBACK_SENDERS.get(region_code)
            cur = conn.execute(
                "SELECT whatsapp_sender_name FROM regions "
                "WHERE region_code = ? LIMIT 1",
                (region_code,),
            )
            row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        LOG.debug("sender lookup: query failed for %s: %s", region_code, exc)
        return _REGION_FALLBACK_SENDERS.get(region_code)

    if row is None:
        return _REGION_FALLBACK_SENDERS.get(region_code)
    try:
        name = row["whatsapp_sender_name"]
    except (KeyError, IndexError, TypeError):
        name = None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return _REGION_FALLBACK_SENDERS.get(region_code)


def get_sender_name(farmer_id: Optional[str]) -> str:
    """Return the WhatsApp sender name for ``farmer_id``'s region.

    Resolution order:
      1. ``_FARMER_REGION_CACHE`` → ``_REGION_SENDER_CACHE`` (in-memory).
      2. ``farmers.region_code`` via ``whatsapp_db.get_farmer_by_id``.
      3. ``regions.whatsapp_sender_name`` for that region code.
      4. Static fallback ``MH`` → ``"ShetMitra"`` / ``JH`` → ``"Bagaan Sathi"``.
      5. :data:`DEFAULT_SENDER_NAME` (``"ShetMitra"``).

    Never raises — any error path collapses to the default sender so a
    transient DB hiccup cannot block a WhatsApp send.
    """
    if not farmer_id:
        return DEFAULT_SENDER_NAME

    with _REGION_CACHE_LOCK:
        region_code = _FARMER_REGION_CACHE.get(farmer_id)
        if region_code is not None:
            cached_sender = _REGION_SENDER_CACHE.get(region_code)
            if cached_sender is not None:
                return cached_sender

    if region_code is None:
        region_code = _lookup_region_code_for_farmer(farmer_id) or DEFAULT_REGION_CODE
        with _REGION_CACHE_LOCK:
            _FARMER_REGION_CACHE[farmer_id] = region_code

    with _REGION_CACHE_LOCK:
        cached_sender = _REGION_SENDER_CACHE.get(region_code)
    if cached_sender is not None:
        return cached_sender

    sender = _lookup_sender_for_region(region_code) or DEFAULT_SENDER_NAME
    with _REGION_CACHE_LOCK:
        _REGION_SENDER_CACHE[region_code] = sender
    return sender


def get_region_code(farmer_id: Optional[str]) -> str:
    """Return the region_code (e.g. ``'MH'``/``'JH'``) for ``farmer_id``.

    Uses the same cache as :func:`get_sender_name`. Defaults to ``'MH'``
    when the lookup fails.
    """
    if not farmer_id:
        return DEFAULT_REGION_CODE
    with _REGION_CACHE_LOCK:
        cached = _FARMER_REGION_CACHE.get(farmer_id)
    if cached is not None:
        return cached
    region_code = _lookup_region_code_for_farmer(farmer_id) or DEFAULT_REGION_CODE
    with _REGION_CACHE_LOCK:
        _FARMER_REGION_CACHE[farmer_id] = region_code
    return region_code


def get_report_footer(farmer_id: Optional[str]) -> str:
    """Return the SDD §2.3 region-aware footer.

    ::

        — {sender}
           Sahyadri Krushi Intelligence

    Used by all daily reports, advisory messages, booking confirmations
    and market alerts. Trader-facing messages call this with the
    trader's ``farmer_id`` (or ``None`` to force the ``ShetMitra``
    default).
    """
    sender = get_sender_name(farmer_id)
    return f"— {sender}\n   Sahyadri Krushi Intelligence"
