def generate_farmer_message(data, weather, advisory):
    ndvi = data["metrics"]["ndvi_avg"]
    rain = weather["daily"]["precipitation_sum"][0]

    if ndvi is None:
        health = "Unknown"
    elif ndvi > 0.65:
        health = "Good"
    elif ndvi > 0.5:
        health = "Moderate"
    else:
        health = "Poor"

    message = f"""
🌾 Shet Mitra Advisory

📍 Field: {data['field_id']}
🌱 Crop: {data['crop']}

🟢 Health: {health} (NDVI: {ndvi})
🌧️ Rain: {rain} mm expected

⚠️ Advisory:
"""

    if advisory["alerts"]:
        for alert in advisory["alerts"]:
            message += f"• {alert}\n"
    else:
        message += "• No major alerts\n"

    message += "\n📅 Stay updated with Shet Mitra"

    return message