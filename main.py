from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from fastapi import FastAPI
from services.agremo_mock import get_mock_data
from services.weather import get_weather
from engine.advisory import generate_advisory
from utils.formatter import generate_farmer_message

app = FastAPI()
templates = Jinja2Templates(directory="templates")

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

@app.get("/report", response_class=HTMLResponse)
def get_report(request: Request):
    data = get_mock_data()
    weather = get_weather()
    advisory = generate_advisory(data, weather)

    grid = [
        ["green", "green", "green", "lightgreen"],
        ["green", "orange", "yellow", "green"],
        ["lightgreen", "red", "orange", "green"],
        ["green", "yellow", "lightgreen", "green"]
    ]

    return templates.TemplateResponse("report.html", {
        "request": request,
        "field_id": data["field_id"],
        "date": data["date"],
        "health": "Good",
        "water": "Check",
        "advisory": advisory["alerts"] or ["No major alerts"],
        "rain": weather["daily"]["precipitation_sum"],
        "grid": grid
    })