def generate_advisory(data, weather):
    alerts = []

    ndvi = data["metrics"]["ndvi_avg"]
    rain = weather["daily"]["precipitation_sum"][0]

    if ndvi and ndvi < 0.55:
        alerts.append("⚠️ Crop health declining")

    if rain and rain > 20:
        alerts.append("🌧️ Heavy rain expected – delay spraying")

    return {
        "field_id": data["field_id"],
        "alerts": alerts
    }