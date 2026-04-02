from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import requests

app = FastAPI()

app.mount("/static", StaticFiles(directory="."), name="static")


# 🌦 SAFE WEATHER FUNCTION (NO CRASH)
def get_weather():
    url = "https://api.open-meteo.com/v1/forecast?latitude=17.1&longitude=74.6&daily=weathercode,temperature_2m_max&timezone=auto"

    res = requests.get(url)
    data = res.json()

    days = []

    for i in range(5):
        code = data["daily"]["weathercode"][i]
        temp = data["daily"]["temperature_2m_max"][i]
        date = data["daily"]["time"][i]

        # ICON + LABEL
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

        # 🌾 DRYING LOGIC (SAFE VERSION)
        if rain_expected:
            drying = "No Drying"
            drying_class = "orange"
        elif temp >= 35:
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
        <title>Shet Mitra Report</title>

        <style>
            body {{
                font-family: Arial;
                background:#000;
                display:flex;
                justify-content:center;
            }}

            .container {{
                width:420px;
                background:#f5f5f5;
                border-radius:12px;
                overflow:hidden;
            }}

            .header {{
                background:#1b5e20;
                color:white;
                padding:10px;
                text-align:center;
                font-weight:bold;
            }}

            .weather {{
                background:#bbdefb;
                padding:12px;
                border-left:6px solid #1565c0;
                border-radius:10px;
                margin:10px;
                font-size:13px;
            }}

            table {{
                width:100%;
                text-align:center;
                border-collapse:collapse;
            }}

            td, th {{
                padding:6px;
            }}

            .green {{ color:#2e7d32; font-weight:bold; }}
            .orange {{ color:#ef6c00; font-weight:bold; }}
            .red {{ color:#c62828; font-weight:bold; }}

        </style>
    </head>

    <body>
        <div class="container">

            <div class="header">
                Shet Mitra - Live Weather & Drying Report
            </div>

            <div class="weather">

                <div style="text-align:center; font-weight:bold; margin-bottom:8px;">
                    Weather (5-day summary)
                </div>

                <table>
                    <tr><th></th>{dates}</tr>
                    <tr><td><b>Weather</b></td>{icons}</tr>
                    <tr><td></td>{labels}</tr>
                    <tr><td><b>Spray</b></td>{spray}</tr>
                    <tr><td><b>Drying</b></td>{drying}</tr>
                </table>

            </div>

        </div>
    </body>
    </html>
    """