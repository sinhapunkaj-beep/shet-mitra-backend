from fastapi import FastAPI
from services.agremo_mock import get_mock_data
from services.weather import get_weather
from engine.advisory import generate_advisory

app = FastAPI()

@app.get("/")
def home():
    return {"message": "Shet Mitra API running 🚀"}

@app.get("/run")
def run_pipeline():
    data = get_mock_data()
    weather = get_weather()
    advisory = generate_advisory(data, weather)

    return {
        "data": data,
        "weather": weather,
        "advisory": advisory
    }