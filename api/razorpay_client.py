"""Razorpay client abstraction for the Trader Intelligence platform.

Exposes:
    * :class:`RazorpayClient` — Protocol describing the operations the
      payment system depends on.
    * :class:`MockRazorpayClient` — deterministic in-process client used
      everywhere by default. Returns plausible-looking IDs derived from a
      stable hash of inputs and never touches the network. Webhook
      signature verification returns ``True`` only for the literal
      string ``"MOCK_OK"``.
    * :class:`LiveRazorpayClient` — production client that POSTs to
      ``https://api.razorpay.com/v1/...`` with HTTP Basic auth derived
      from ``RAZORPAY_KEY_ID`` and ``RAZORPAY_KEY_SECRET``. Webhook
      signature verification uses HMAC-SHA256 over the raw request body
      with ``RAZORPAY_WEBHOOK_SECRET``, compared with ``hmac.compare_digest``.
    * :func:`get_razorpay_client` — factory honouring ``RAZORPAY_MODE``
      (defaults to ``mock``). Falls back to :class:`MockRazorpayClient`
      whenever live mode is requested but the required keys are missing.

This module never makes a live Razorpay call from inside the test suite.
Tests inject :class:`MockRazorpayClient` directly via the ``client=``
kwarg on every :mod:`api.trader_payments` entry point.

Standard library + ``httpx`` only. ``httpx`` is already a dependency
brought in by the AiSensy sender; the live client imports it lazily so
the mock path runs even on machines without ``httpx`` installed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from typing import Optional, Protocol, runtime_checkable

LOG = logging.getLogger(__name__)


# Env var names — module level so callers and tests can monkeypatch them.
ENV_RAZORPAY_MODE = "RAZORPAY_MODE"
ENV_RAZORPAY_KEY_ID = "RAZORPAY_KEY_ID"
ENV_RAZORPAY_KEY_SECRET = "RAZORPAY_KEY_SECRET"
ENV_RAZORPAY_WEBHOOK_SECRET = "RAZORPAY_WEBHOOK_SECRET"

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
RAZORPAY_TIMEOUT = 15.0
MOCK_WEBHOOK_TOKEN = "MOCK_OK"


@runtime_checkable
class RazorpayClient(Protocol):
    """Minimal Razorpay surface used by the Trader Intelligence platform."""

    def create_customer(
        self,
        full_name: str,
        mobile: str,
        email: Optional[str] = None,
    ) -> dict:  # pragma: no cover - protocol
        ...

    def create_subscription(
        self,
        customer_id: str,
        plan_id: str,
        *,
        total_count: int = 12,
    ) -> dict:  # pragma: no cover - protocol
        ...

    def fetch_subscription(self, subscription_id: str) -> dict:  # pragma: no cover
        ...

    def cancel_subscription(
        self,
        subscription_id: str,
        *,
        cancel_at_cycle_end: bool = True,
    ) -> dict:  # pragma: no cover - protocol
        ...

    def pause_subscription(
        self,
        subscription_id: str,
        *,
        pause_at: str = "now",
    ) -> dict:  # pragma: no cover - protocol
        ...

    def resume_subscription(
        self,
        subscription_id: str,
        *,
        resume_at: str = "now",
    ) -> dict:  # pragma: no cover - protocol
        ...

    def verify_webhook_signature(
        self,
        body: bytes,
        signature: str,
    ) -> bool:  # pragma: no cover - protocol
        ...


def _short_hash(*parts: str) -> str:
    """Stable 12-char hex digest used to build deterministic mock IDs.

    The output only depends on the inputs, so the same trader + plan
    combination always resolves to the same mock customer / subscription
    ID across test runs.
    """
    raw = "|".join(part or "" for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


class MockRazorpayClient:
    """Deterministic in-process client used by tests and dev mode.

    No network calls. IDs are derived from a stable hash so repeated
    invocations with the same inputs produce the same IDs.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def _record(self, method: str, payload: dict) -> None:
        self.calls.append({"method": method, "payload": payload})

    def create_customer(
        self,
        full_name: str,
        mobile: str,
        email: Optional[str] = None,
    ) -> dict:
        cust_id = f"cust_mock_{_short_hash(full_name, mobile, email or '')}"
        self._record(
            "create_customer",
            {"full_name": full_name, "mobile": mobile, "email": email},
        )
        return {
            "id": cust_id,
            "entity": "customer",
            "name": full_name,
            "contact": mobile,
            "email": email or "",
            "created_at": 0,
        }

    def create_subscription(
        self,
        customer_id: str,
        plan_id: str,
        *,
        total_count: int = 12,
    ) -> dict:
        sub_id = f"sub_mock_{_short_hash(customer_id, plan_id, str(total_count))}"
        self._record(
            "create_subscription",
            {
                "customer_id": customer_id,
                "plan_id": plan_id,
                "total_count": total_count,
            },
        )
        return {
            "id": sub_id,
            "entity": "subscription",
            "plan_id": plan_id,
            "customer_id": customer_id,
            "total_count": total_count,
            "status": "created",
            "short_url": f"https://rzp.io/i/{sub_id}",
        }

    def fetch_subscription(self, subscription_id: str) -> dict:
        self._record("fetch_subscription", {"subscription_id": subscription_id})
        return {
            "id": subscription_id,
            "entity": "subscription",
            "status": "active",
        }

    def cancel_subscription(
        self,
        subscription_id: str,
        *,
        cancel_at_cycle_end: bool = True,
    ) -> dict:
        self._record(
            "cancel_subscription",
            {
                "subscription_id": subscription_id,
                "cancel_at_cycle_end": cancel_at_cycle_end,
            },
        )
        return {
            "id": subscription_id,
            "entity": "subscription",
            "status": "cancelled",
            "cancel_at_cycle_end": bool(cancel_at_cycle_end),
        }

    def pause_subscription(
        self,
        subscription_id: str,
        *,
        pause_at: str = "now",
    ) -> dict:
        self._record(
            "pause_subscription",
            {"subscription_id": subscription_id, "pause_at": pause_at},
        )
        return {
            "id": subscription_id,
            "entity": "subscription",
            "status": "paused",
            "pause_at": pause_at,
        }

    def resume_subscription(
        self,
        subscription_id: str,
        *,
        resume_at: str = "now",
    ) -> dict:
        self._record(
            "resume_subscription",
            {"subscription_id": subscription_id, "resume_at": resume_at},
        )
        return {
            "id": subscription_id,
            "entity": "subscription",
            "status": "active",
            "resume_at": resume_at,
        }

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        return signature == MOCK_WEBHOOK_TOKEN


class LiveRazorpayClient:
    """Production Razorpay client.

    Lazy-imports ``httpx`` so the mock path keeps working on machines
    without it installed. All methods catch transport errors and return
    a structured error dict instead of raising — callers compare on the
    ``status`` key.
    """

    def __init__(
        self,
        key_id: Optional[str] = None,
        key_secret: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> None:
        self.key_id = key_id or os.getenv(ENV_RAZORPAY_KEY_ID, "")
        self.key_secret = key_secret or os.getenv(ENV_RAZORPAY_KEY_SECRET, "")
        self.webhook_secret = (
            webhook_secret or os.getenv(ENV_RAZORPAY_WEBHOOK_SECRET, "")
        )

    def _auth_header(self) -> str:
        token = f"{self.key_id}:{self.key_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("ascii")

    def _headers(self) -> dict:
        return {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict:
        return self._request("POST", path, payload=payload)

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
    ) -> dict:
        url = f"{RAZORPAY_API_BASE}{path}"
        try:
            import httpx  # type: ignore

            with httpx.Client(timeout=RAZORPAY_TIMEOUT) as client:
                resp = client.request(
                    method,
                    url,
                    json=payload,
                    headers=self._headers(),
                )
            return self._parse_response(resp.status_code, resp.text)
        except ImportError:
            return {
                "status": "error",
                "code": 0,
                "body": "httpx not installed; live Razorpay calls disabled",
            }
        except Exception as exc:  # noqa: BLE001 - never raise to caller
            LOG.warning("Razorpay %s %s failed: %s", method, path, exc)
            return {
                "status": "error",
                "code": 0,
                "body": f"transport error: {exc}",
            }

    @staticmethod
    def _parse_response(status_code: int, text: str) -> dict:
        try:
            parsed = json.loads(text) if text else {}
        except (ValueError, TypeError):
            parsed = {"raw": text[:500]}
        if 200 <= status_code < 300:
            return parsed if isinstance(parsed, dict) else {"data": parsed}
        return {
            "status": "error",
            "code": status_code,
            "body": parsed if isinstance(parsed, dict) else {"raw": str(parsed)[:500]},
        }

    # -------- Public API (matches Protocol) ---------------------------------

    def create_customer(
        self,
        full_name: str,
        mobile: str,
        email: Optional[str] = None,
    ) -> dict:
        return self._post(
            "/customers",
            {
                "name": full_name,
                "contact": mobile,
                "email": email or "",
                "fail_existing": "0",
            },
        )

    def create_subscription(
        self,
        customer_id: str,
        plan_id: str,
        *,
        total_count: int = 12,
    ) -> dict:
        return self._post(
            "/subscriptions",
            {
                "plan_id": plan_id,
                "customer_notify": 1,
                "total_count": total_count,
                "notes": {"customer_id": customer_id},
            },
        )

    def fetch_subscription(self, subscription_id: str) -> dict:
        return self._get(f"/subscriptions/{subscription_id}")

    def cancel_subscription(
        self,
        subscription_id: str,
        *,
        cancel_at_cycle_end: bool = True,
    ) -> dict:
        return self._post(
            f"/subscriptions/{subscription_id}/cancel",
            {"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0},
        )

    def pause_subscription(
        self,
        subscription_id: str,
        *,
        pause_at: str = "now",
    ) -> dict:
        return self._post(
            f"/subscriptions/{subscription_id}/pause",
            {"pause_at": pause_at},
        )

    def resume_subscription(
        self,
        subscription_id: str,
        *,
        resume_at: str = "now",
    ) -> dict:
        return self._post(
            f"/subscriptions/{subscription_id}/resume",
            {"resume_at": resume_at},
        )

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        if not self.webhook_secret or not signature:
            return False
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            msg=body,
            digestmod=hashlib.sha256,
        ).hexdigest()
        try:
            return hmac.compare_digest(expected, signature)
        except Exception:  # noqa: BLE001
            return False


_missing_keys_warned = False


def get_razorpay_client() -> RazorpayClient:
    """Return the active Razorpay client.

    Resolves the mode from ``RAZORPAY_MODE`` (default ``mock``). Falls back
    to :class:`MockRazorpayClient` whenever live mode is requested but
    ``RAZORPAY_KEY_ID`` / ``RAZORPAY_KEY_SECRET`` are missing — and warns
    once per process so the operator notices.
    """
    global _missing_keys_warned
    mode = (os.getenv(ENV_RAZORPAY_MODE, "mock") or "mock").strip().lower()
    if mode in ("live", "production", "razorpay"):
        key_id = (os.getenv(ENV_RAZORPAY_KEY_ID, "") or "").strip()
        key_secret = (os.getenv(ENV_RAZORPAY_KEY_SECRET, "") or "").strip()
        if key_id and key_secret:
            return LiveRazorpayClient(
                key_id=key_id,
                key_secret=key_secret,
            )
        if not _missing_keys_warned:
            LOG.warning(
                "RAZORPAY_MODE=%s but RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET "
                "are empty; falling back to MockRazorpayClient.",
                mode,
            )
            _missing_keys_warned = True
    return MockRazorpayClient()


__all__ = [
    "RazorpayClient",
    "MockRazorpayClient",
    "LiveRazorpayClient",
    "get_razorpay_client",
    "MOCK_WEBHOOK_TOKEN",
    "ENV_RAZORPAY_MODE",
    "ENV_RAZORPAY_KEY_ID",
    "ENV_RAZORPAY_KEY_SECRET",
    "ENV_RAZORPAY_WEBHOOK_SECRET",
]
