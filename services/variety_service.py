import requests

VARIETY_API = "https://api.data.gov.in/resource/variety_dataset_id"
API_KEY = "YOUR_API_KEY_HERE"


def get_variety_prices(commodity="Tomato"):
    try:
        params = {
            "api-key": API_KEY,
            "format": "json",
            "limit": 50,
            "filters[commodity]": commodity
        }

        res = requests.get(VARIETY_API, params=params)

        if res.status_code != 200:
            return []

        data = res.json()

        formatted = []

        for item in data.get("records", []):
            formatted.append({
                "commodity": item.get("commodity"),
                "variety": item.get("variety"),
                "market": item.get("market"),
                "modal_price": item.get("modal_price")
            })

        return formatted

    except:
        return []