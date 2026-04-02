from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import requests

app = FastAPI()

app.mount("/static", StaticFiles(directory="."), name="static")


# 🌦 WEATHER FETCH
def get_weather():
    url = "https://api.open-meteo.com/v1/forecast?latitude=17.1&longitude=74.6&daily=weathercode,temperature_2m_max,relativehumidity_2m_max&timezone=auto"
    res = requests.get(url)
    data = res.json()

    days = []

    for i in range(5):
        code = data["daily"]["weathercode"][i]
        temp = data["daily"]["temperature_2m_max"][i]
        humidity = data["daily"]["relativehumidity_2m_max"][i]
        date = data["daily"]["time"][i]

        # 🌦 ICON + LABEL
        if code == 0:
            icon = "☀️"
            label = "Sunny"
            rain_expected = False
        elif code in [1, 2, 3]:
            icon = "🌤️"
            label = "Cloudy"
            rain_expected = False
        else:
            icon = "🌧️"
            label = "Expected Rain"
            rain_expected = True

        # 🌾 DRYING LOGIC
        if rain_expected:
            drying = "No Drying"
            drying_class = "orange"
        elif humidity >= 60:
            drying = "Mold Risk"
            drying_class = "red"
        elif 35 <= temp <= 45 and humidity < 20:
            drying = "Perfect"
            drying_class = "green"
        else:
            drying = "Slow"
            drying_class = "orange"

        # 💊 SPRAY LOGIC
        if rain_expected:
            spray = "No"
            spray_class = "red"
        else:
            spray = "OK"
            spray_class = "green"

        days.append({
            "date": date,
            "icon": icon,
            "label": label,
            "drying": drying,
            "drying_class": drying_class,
            "spray": spray,
            "spray_class": spray_class
        })

    return days


@app.get("/")
def home():
    return {"message": "Shet Mitra API running 🚀"}


@app.get("/report-ui", response_class=HTMLResponse)
def report_ui():
    weather = get_weather()

    dates = "".join([f"<th>{d['date'][5:]}</th>" for d in weather])
    icons = "".join([f"<td>{d['icon']}</td>" for d in weather])
    labels = "".join([f"<td>{d['label']}</td>" for d in weather])
    spray = "".join([f"<td class='{d['spray_class']}'>{d['spray']}</td>" for d in weather])
    drying = "".join([f"<td class='{d['drying_class']}'>{d['drying']}</td>" for d in weather])

    return f"""
    <html>
    <head>
    <style>
    body {{ font-family: Arial; background:#000; display:flex; justify-content:center; }}
    .container {{ width:420px; background:#f5f5f5; border-radius:12px; }}

    .header {{ background:#1b5e20; color:white; padding:10px; text-align:center; }}

    .weather {{
        background:#bbdefb;
        padding:12px;
        border-left:6px solid #1565c0;
        border-radius:10px;
        margin:10px;
    }}

    table {{ width:100%; text-align:center; border-collapse:collapse; }}
    td, th {{ padding:5px; }}

    .green {{ color:green; font-weight:bold; }}
    .orange {{ color:orange; font-weight:bold; }}
    .red {{ color:red; font-weight:bold; }}

    </style>
    </head>

    <body>
    <div class="container">

        <div class="header">
            Shet Mitra - Live Weather Report
        </div>

        <div class="weather">

            <b>Weather (Live)</b>

            <table>
                <tr><th></th>{dates}</tr>
                <tr><td>Weather</td>{icons}</tr>
                <tr><td></td>{labels}</tr>
                <tr><td>Spray</td>{spray}</tr>
                <tr><td>Drying</td>{drying}</tr>
            </table>

        </div>

    </div>
    </body>
    </html>
    """