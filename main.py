from fastapi import FastAPI
from services.agremo_mock import get_mock_data
from services.weather import get_weather
from engine.advisory import generate_advisory
from utils.formatter import generate_farmer_message

app = FastAPI()


@app.get("/")
def home():
    return {"message": "Shet Mitra API running 🚀"}


@app.get("/run")
def run_pipeline():
    data = get_mock_data()
    weather = get_weather()
    advisory = generate_advisory(data, weather)

    message = generate_farmer_message(data, weather, advisory)

    return {
        "data": data,
        "weather": weather,
        "advisory": advisory,
        "farmer_message": message
    }


@app.get("/report")
def get_report():
    data = get_mock_data()
    weather = get_weather()
    advisory = generate_advisory(data, weather)

    return {
        "title": "Sahyadri Krushi Report",
        "field": data["field_id"],
        "crop": data["crop"],
        "ndvi": data["metrics"]["ndvi_avg"],
        "plant_count": data["metrics"]["plant_count"],
        "stress": data["metrics"]["stress_zones_percent"],
        "health": "Good" if data["metrics"]["ndvi_avg"] > 0.65 else "Moderate",
        "water": "Check",
        "advisory": advisory["alerts"] or ["No major alerts"],
        "rain_forecast": weather["daily"]["precipitation_sum"]
    }