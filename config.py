import os

# SUPABASE_DB_PASSWORD lives in nano.env / env vars only — never in source.
DB_CONFIG = {
    "host": "aws-1-ap-northeast-2.pooler.supabase.com",
    "database": "postgres",
    "user": "postgres.euydubpywdsettjywkms",
    "password": os.environ.get("SUPABASE_DB_PASSWORD", ""),
    "port": 6543
}

API_KEY = "YOUR_DATA_GOV_API_KEY"
