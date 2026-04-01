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
    # Step 1: Get data
    data = get_mock_data()

    # Step 2: Get weather
    weather = get_weather()

    # Step 3: Generate advisory
    advisory = generate_advisory(data, weather)

    # Step 4: Generate farmer-friendly message
    message = generate_farmer_message(data, weather, advisory)

    # Step 5: Return everything
    return {
        "data": data,
        "weather": weather,
        "advisory": advisory,
        "farmer_message": message
    }