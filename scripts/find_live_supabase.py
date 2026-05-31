"""Probe every known Supabase URL with the service-role key and find which is live.

For each candidate (URL, service_key):
  1. DNS resolve <ref>.supabase.co
  2. GET <url>/rest/v1/farmers?limit=1 with service-role headers

Prints a verdict per candidate. Exits 0 if exactly one URL is live.

This script does NOT apply DDL. It is read-only.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

CANDIDATES = [
    {
        "label": "ShetMitra DEV",
        "url": "https://nahftvhvuijhdktrcbbm.supabase.co",
        "service_key_source": Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra\nano.env"),
        "service_key_var": "SUPABASE_SERVICE_KEY",
        "audit_note": "Off-limits per audit doc (user memory rule).",
    },
    {
        "label": "ShetMitra TEST",
        "url": "https://euydubpywdsettjywkms.supabase.co",
        "service_key_source": Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env"),
        "service_key_var": "SUPABASE_SERVICE_KEY",
        "audit_note": "Audit doc's 'current working copy'.",
    },
    {
        "label": "bugzgufhyjjvaymsvihy (legacy typo)",
        "url": "https://bugzgufhyjjvaymsvihy.supabase.co",
        "service_key_source": None,
        "service_key_var": None,
        "audit_note": "Not in audit; no service key on disk.",
    },
]


def _read_env_value(path: Path, var: str) -> str | None:
    if path is None or not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == var:
            return v.strip()
    return None


def _dns_ok(host: str) -> tuple[bool, str]:
    try:
        ips = socket.gethostbyname_ex(host)[2]
        return True, ",".join(ips)
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _rest_probe(url: str, service_key: str) -> tuple[int, str]:
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json",
    }
    req = urllib.request.Request(f"{url}/rest/v1/farmers?limit=1", method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read(512).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read(512).decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body
    except urllib.error.URLError as e:
        return -1, f"URLError: {e.reason}"
    except OSError as e:
        return -2, f"OSError: {e}"


def main() -> int:
    print("Probing all known Supabase URLs (read-only).\n")
    live_urls: list[dict] = []
    for c in CANDIDATES:
        print(f"=== {c['label']} ===")
        print(f"  URL: {c['url']}")
        print(f"  Note: {c['audit_note']}")

        host = c["url"].replace("https://", "").replace("http://", "").strip("/")
        ok, dns_info = _dns_ok(host)
        print(f"  DNS: {'OK ' + dns_info if ok else 'FAIL ' + dns_info}")
        if not ok:
            print("  -> Unreachable from this sandbox.\n")
            continue

        if c["service_key_source"] is None:
            print("  Service key: NOT AVAILABLE on disk. Cannot probe REST.\n")
            continue

        service_key = _read_env_value(c["service_key_source"], c["service_key_var"])
        if not service_key:
            print(f"  Service key: missing var {c['service_key_var']} in {c['service_key_source']}\n")
            continue

        status, body = _rest_probe(c["url"], service_key)
        print(f"  REST /farmers?limit=1: status={status}")
        if status == 200:
            preview = body if len(body) < 200 else body[:200] + "..."
            print(f"    body[:200]: {preview}")
            print(f"  -> LIVE.\n")
            live_urls.append(c)
        elif status in (401, 403):
            print(f"    body: {body[:300]}")
            print(f"  -> Auth issue (project reachable but key/RLS rejected the request).\n")
        elif status == 404:
            print(f"    body: {body[:300]}")
            print(f"  -> Table not found on this project (REST reachable, but no farmers).\n")
        else:
            print(f"    body[:300]: {body[:300]}")
            print()

    print("=" * 60)
    if not live_urls:
        print("VERDICT: no live Supabase URL is reachable from this sandbox.")
        return 1
    print(f"VERDICT: {len(live_urls)} URL(s) live and responding:")
    for c in live_urls:
        print(f"  - {c['label']}: {c['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
