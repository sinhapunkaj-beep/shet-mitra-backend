import requests

AGMARKNET_API = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"

API_KEY = "579b464db66ec23bdd0000013ec80652d42346866c68bff4a50fd487"


def get_mandi_prices(state=None, commodity=None, district=None):
    try:
        all_records = []

        # Fetch data
        for offset in range(0, 1000, 100):
            params = {
                "api-key": API_KEY,
                "format": "json",
                "limit": 100,
                "offset": offset
            }

            response = requests.get(AGMARKNET_API, params=params)

            if response.status_code != 200:
                continue

            data = response.json()
            records = data.get("records", [])

            if not records:
                break

            all_records.extend(records)

        formatted = []

        for item in all_records:
            record = {
                "mandi": item.get("market"),
                "state": item.get("state"),
                "district": item.get("district"),
                "commodity": item.get("commodity"),
                "variety": item.get("variety"),
                "grade": item.get("grade"),
                "arrival_date": item.get("arrival_date"),
                "modal_price": item.get("modal_price")
            }

            # 🔥 STRICT FILTERING
            if commodity:
                if not record["commodity"] or commodity.lower() not in record["commodity"].lower():
                    continue

            if district:
                if not record["district"] or record["district"].lower() != district.lower():
                    continue

            if state:
                if not record["state"] or record["state"].lower() != state.lower():
                    continue

            formatted.append(record)

        # ❌ NO FALLBACK HERE
        return formatted

    except Exception as e:
        return {"error": str(e)}