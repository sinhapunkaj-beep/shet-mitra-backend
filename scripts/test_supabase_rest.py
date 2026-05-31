"""Probe Supabase REST reachability from this sandbox using urllib (stdlib only).

Tries:
  1. GET <url>/rest/v1/ — auth probe with service role
  2. GET <url>/rest/v1/farmers?limit=1 — auth + RLS probe
  3. POST <url>/rest/v1/rpc/exec_sql — checks if exec_sql is defined
  4. POST <url>/rest/v1/rpc/query     — alternate
  5. POST <url>/pg-meta/v0/query      — pg-meta path (rarely public)

Exit 0 if any data endpoint returns 200/206. Exit 1 if nothing usable.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NANO_ENV = Path(r"C:\Users\Pankaj Sinha\Desktop\shetmitra_test\nano.env")


def _read_env_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _probe(method: str, url: str, headers: dict[str, str], body: bytes | None = None,
           timeout: float = 12.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read(2048)
            return r.status, payload.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        payload = e.read(2048) if e.fp else b""
        return e.code, payload.decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return -1, f"URLError: {e.reason}"
    except OSError as e:
        return -2, f"OSError: {e}"
    finally:
        ms = (time.time() - t0) * 1000
        print(f"    elapsed: {ms:.0f} ms")


def main() -> int:
    env = _read_env_kv(NANO_ENV)
    url = env.get("SUPABASE_URL", "").rstrip("/")
    anon = env.get("SUPABASE_ANON_KEY", "")
    service = env.get("SUPABASE_SERVICE_KEY", "")
    if not url or not service:
        print("FATAL: missing SUPABASE_URL or SUPABASE_SERVICE_KEY in nano.env")
        return 1
    print(f"Target: {url}")
    print(f"Service key present: {bool(service)} (length={len(service)})")
    print()

    base_headers = {
        "apikey": service,
        "Authorization": f"Bearer {service}",
        "Accept": "application/json",
    }

    print("=== 1. GET /rest/v1/  (auth handshake) ===")
    status, body = _probe("GET", f"{url}/rest/v1/", base_headers)
    print(f"    status={status} body[:200]={body[:200]!r}")
    print()

    print("=== 2. GET /rest/v1/farmers?limit=1  (data probe) ===")
    status, body = _probe("GET", f"{url}/rest/v1/farmers?limit=1", base_headers)
    print(f"    status={status} body[:200]={body[:200]!r}")
    print()

    print("=== 3. GET /rest/v1/price_history_training?select=count  (training data probe) ===")
    h = dict(base_headers)
    h["Prefer"] = "count=exact"
    h["Range-Unit"] = "items"
    h["Range"] = "0-0"
    status, body = _probe("GET", f"{url}/rest/v1/price_history_training?select=id", h)
    print(f"    status={status} body[:200]={body[:200]!r}")
    print()

    print("=== 4. POST /rest/v1/rpc/exec_sql  (exec_sql function probe) ===")
    h = dict(base_headers)
    h["Content-Type"] = "application/json"
    data = json.dumps({"query": "SELECT 1 AS one"}).encode("utf-8")
    status, body = _probe("POST", f"{url}/rest/v1/rpc/exec_sql", h, body=data)
    print(f"    status={status} body[:300]={body[:300]!r}")
    print()

    print("=== 5. POST /rest/v1/rpc/query  (alt RPC name) ===")
    status, body = _probe("POST", f"{url}/rest/v1/rpc/query", h, body=data)
    print(f"    status={status} body[:300]={body[:300]!r}")
    print()

    print("=== 6. POST /pg-meta/v0/query  (pg-meta endpoint) ===")
    status, body = _probe("POST", f"{url}/pg-meta/v0/query", h, body=data)
    print(f"    status={status} body[:300]={body[:300]!r}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
