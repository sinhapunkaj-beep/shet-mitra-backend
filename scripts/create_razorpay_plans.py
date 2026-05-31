"""
Create Razorpay subscription plans for ShetMitra Trader Intelligence.

Reads the canonical plan list from ``data/razorpay_plans.json`` and either
prints what would be sent (dry-run, the default) or actually POSTs each plan
to ``https://api.razorpay.com/v1/plans`` via HTTP Basic auth using the
``RAZORPAY_KEY_ID`` / ``RAZORPAY_KEY_SECRET`` environment variables.

Usage:
    python scripts/create_razorpay_plans.py            # dry-run
    python scripts/create_razorpay_plans.py --apply    # actually call API

When run with ``--apply`` and a 2xx response is received, the Razorpay
``plan_id`` from the response is appended to ``data/razorpay_plans_applied.json``.

This script is intentionally offline by default. DO NOT run it with
``--apply`` from automation; the live POSTs should be triggered by the
operator from their own workstation when they are ready to provision plans
in the Razorpay dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
PLANS_PATH = REPO_ROOT / "data" / "razorpay_plans.json"
APPLIED_PATH = REPO_ROOT / "data" / "razorpay_plans_applied.json"
RAZORPAY_PLANS_URL = "https://api.razorpay.com/v1/plans"


def _load_plans(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        print(f"[error] Plan file not found: {path}", file=sys.stderr)
        sys.exit(1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    plans = payload.get("plans") or []
    if not plans:
        print(f"[error] No plans in {path}", file=sys.stderr)
        sys.exit(1)
    return plans


def _build_payload(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Map our local plan record to Razorpay's plan-create API shape."""
    return {
        "period": plan["interval"],
        "interval": 1,
        "item": {
            "name": plan["description"],
            "amount": plan["amount_paise"],
            "currency": "INR",
            "description": plan["description"],
        },
        "notes": {
            "local_id": plan["local_id"],
            "source": "shetmitra_trader_intelligence",
        },
    }


def _print_dry_run(plans: List[Dict[str, Any]]) -> None:
    print("[dry-run] Would POST the following plans to", RAZORPAY_PLANS_URL)
    print("[dry-run] (no network call - re-run with --apply to actually fire)\n")
    for plan in plans:
        body = _build_payload(plan)
        print(f"  local_id={plan['local_id']}")
        print(f"    amount_paise = {plan['amount_paise']} (INR {plan['amount_paise'] / 100:.2f})")
        print(f"    interval     = {plan['interval']}")
        print(f"    description  = {plan['description']}")
        print(f"    json_body    = {json.dumps(body)}")
        print()


def _append_applied(plan_local_id: str, plan_id: str) -> None:
    """Append a successful plan creation to data/razorpay_plans_applied.json."""
    record = {
        "local_id": plan_local_id,
        "razorpay_plan_id": plan_id,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    if APPLIED_PATH.exists():
        data = json.loads(APPLIED_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "applied" not in data:
            data = {"applied": []}
    else:
        data = {"applied": []}
    data["applied"].append(record)
    APPLIED_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _apply(plans: List[Dict[str, Any]], key_id: str, key_secret: str) -> int:
    """Actually POST each plan. Returns process exit code."""
    try:
        import httpx
    except ImportError:
        print(
            "[error] httpx is required for --apply. Install with: pip install httpx",
            file=sys.stderr,
        )
        return 1

    failures = 0
    for plan in plans:
        body = _build_payload(plan)
        try:
            response = httpx.post(
                RAZORPAY_PLANS_URL,
                json=body,
                auth=(key_id, key_secret),
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            print(
                f"[error] {plan['local_id']}: HTTP failure ({type(exc).__name__}): {exc}",
                file=sys.stderr,
            )
            failures += 1
            continue

        if response.status_code in (200, 201):
            try:
                plan_id = response.json().get("id", "<no-id-in-response>")
            except json.JSONDecodeError:
                plan_id = "<non-json-response>"
            print(f"[ok]   {plan['local_id']} -> {plan_id}")
            _append_applied(plan["local_id"], plan_id)
        else:
            failures += 1
            print(
                f"[error] {plan['local_id']}: status {response.status_code} body={response.text}",
                file=sys.stderr,
            )

    return 0 if failures == 0 else 1


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually POST to Razorpay (uses RAZORPAY_KEY_ID/SECRET env vars). "
            "Default is dry-run."
        ),
    )
    args = parser.parse_args(argv)

    plans = _load_plans(PLANS_PATH)

    if not args.apply:
        _print_dry_run(plans)
        return 0

    key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        print(
            "[error] RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET environment "
            "variables must be set to use --apply.",
            file=sys.stderr,
        )
        return 1

    return _apply(plans, key_id, key_secret)


if __name__ == "__main__":
    raise SystemExit(main())
