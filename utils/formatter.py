def generate_farmer_message(data, weather, advisory):
    ndvi = data["metrics"]["ndvi_avg"]
    rain = weather["daily"]["precipitation_sum"][0]

    health = "Good" if ndvi and ndvi > 0.65 else "Moderate" if ndvi > 0.5 else "Poor"

    message = f"""
🌾 Shet Mitra Advisory

📍 Field: {data['field_id']}
🌱 Crop: {data['crop']}

🟢 Health: {health} (NDVI: {ndvi})
🌧️ Rain: {rain} mm expected

⚠️ Advisory:
"""

    for alert in advisory["alerts"]:
        message += f"• {alert}\n"

    message += "\n📅 Stay updated with Shet Mitra"

    return message