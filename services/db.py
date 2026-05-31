# services/db.py

import os
import requests

# Supabase project: ShetMitra TEST (euydubpywdsettjywkms).
# Real anon key should come from the SUPABASE_ANON_KEY env var (see root .env).
# The literal below is a placeholder so legacy callers do not crash if the env
# var is unset in dev; it is NOT a real key and will not authenticate.
_PLACEHOLDER_ANON_KEY = "PLACEHOLDER_ANON_KEY_SET_SUPABASE_ANON_KEY_IN_ENV"

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://euydubpywdsettjywkms.supabase.co",
)
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", _PLACEHOLDER_ANON_KEY)

# Backwards-compatible alias for older callers that import SUPABASE_KEY.
SUPABASE_KEY = SUPABASE_ANON_KEY


def fetch_market_data(crop: str):
    url = f"{SUPABASE_URL}/rest/v1/market_prices"

    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}"
    }

    params = {
        "select": "*",
        "crop": f"eq.{crop.lower()}"
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        print("ERROR:", response.text)
        return []

    return response.json()
