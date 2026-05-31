import requests
from datetime import datetime
import psycopg2
from config import DB_CONFIG, API_KEY


def fetch_and_store():

    conn = psycopg2.connect(
        host=DB_CONFIG["host"],
        database=DB_CONFIG["database"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"]
    )

    cur = conn.cursor()

    url = f"https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070?api-key={API_KEY}&format=json&limit=500"

    res = requests.get(url)
    records = res.json().get("records", [])

    print("Records fetched:", len(records))

    for r in records:
        try:
            arrival_date = datetime.strptime(r["arrival_date"], "%d/%m/%Y").date()

            cur.execute("""
                INSERT INTO market_prices 
                (crop, mandi, state, min_price, max_price, modal_price, date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                r["commodity"],
                r["market"],
                r["state"],
                float(r["min_price"]),
                float(r["max_price"]),
                float(r["modal_price"]),
                arrival_date
            ))

        except Exception:
            continue

    conn.commit()
    cur.close()
    conn.close()

    print("Data inserted successfully")


if __name__ == "__main__":
    fetch_and_store()