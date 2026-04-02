from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import requests

app = FastAPI()
app.mount("/static", StaticFiles(directory="."), name="static")


# 🌦 WEATHER FUNCTION
def get_weather():
    url = "https://api.open-meteo.com/v1/forecast?latitude=17.1&longitude=74.6&daily=weathercode,temperature_2m_max&timezone=auto"
    data = requests.get(url).json()

    days = []
    for i in range(5):
        code = data["daily"]["weathercode"][i]
        temp = data["daily"]["temperature_2m_max"][i]
        date = data["daily"]["time"][i][5:]

        if code == 0:
            icon = "☀️"
            label = "Sunny"
            rain = False
        elif code in [1,2,3]:
            icon = "🌤️"
            label = "Cloudy"
            rain = False
        else:
            icon = "🌧️"
            label = "Expected Rain"
            rain = True

        # 🌾 Drying logic
        if rain:
            drying = "No Drying"
            drying_class = "orange"
        elif temp >= 35:
            drying = "Perfect"
            drying_class = "green"
        else:
            drying = "Slow"
            drying_class = "orange"

        # 💊 Spray logic
        spray = "No" if rain else "OK"
        spray_class = "red" if rain else "green"

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


@app.get("/report-ui", response_class=HTMLResponse)
def report():
    weather = get_weather()

    dates = "".join([f"<th>{d['date']}</th>" for d in weather])
    icons = "".join([f"<td>{d['icon']}</td>" for d in weather])
    labels = "".join([f"<td>{d['label']}</td>" for d in weather])
    spray = "".join([f"<td class='{d['spray_class']}'>{d['spray']}</td>" for d in weather])
    drying = "".join([f"<td class='{d['drying_class']}'>{d['drying']}</td>" for d in weather])

    return f"""
    <html>
    <head>
    <style>
    body {{ font-family: Arial; background:#000; display:flex; justify-content:center; }}
    .container {{ width:420px; background:#f5f5f5; border-radius:12px; overflow:hidden; }}

    .header {{ background:#1b5e20; color:white; padding:10px; text-align:center; }}

    .section {{ padding:12px; }}

    .grid {{
        display:grid;
        grid-template-columns:repeat(4,1fr);
        gap:4px;
        background:#ddd;
        padding:6px;
        border-radius:10px;
    }}

    .cell {{ height:60px; border-radius:4px; }}
    .g{{background:#2e7d32;}} .lg{{background:#66bb6a;}}
    .y{{background:#fdd835;}} .o{{background:#fb8c00;}} .r{{background:#e53935;}}

    .card {{ background:#eee; padding:12px; border-radius:10px; font-size:13px; }}

    .weather {{
        background:#bbdefb;
        padding:12px;
        border-left:6px solid #1565c0;
        border-radius:10px;
        font-size:13px;
    }}

    .market {{
        background:#c8e6c9;
        padding:12px;
        border-left:6px solid #1b5e20;
        border-radius:10px;
        font-size:13px;
    }}

    table {{ width:100%; text-align:center; border-collapse:collapse; }}
    td, th {{ padding:5px; }}

    .green{{color:green;font-weight:bold;}}
    .orange{{color:orange;font-weight:bold;}}
    .red{{color:red;font-weight:bold;}}

    .footer {{ text-align:center; padding:10px; font-size:12px; }}

    </style>
    </head>

    <body>
    <div class="container">

        <div class="header">Shet Mitra Report</div>

        <!-- HEATMAP -->
        <div class="section">
            <div class="grid">
                <div class="cell g"></div><div class="cell lg"></div><div class="cell g"></div><div class="cell lg"></div>
                <div class="cell lg"></div><div class="cell o"></div><div class="cell y"></div><div class="cell g"></div>
                <div class="cell lg"></div><div class="cell r"></div><div class="cell o"></div><div class="cell g"></div>
                <div class="cell g"></div><div class="cell y"></div><div class="cell lg"></div><div class="cell g"></div>
            </div>
        </div>

        <!-- WEATHER -->
        <div class="section">
            <div class="weather">
                <b>Weather (5-day summary)</b>

                <table>
                    <tr><th></th>{dates}</tr>
                    <tr><td>Weather</td>{icons}</tr>
                    <tr><td></td>{labels}</tr>
                    <tr><td>Spray</td>{spray}</tr>
                    <tr><td>Drying</td>{drying}</tr>
                </table>
            </div>
        </div>

        <!-- MARKET -->
        <div class="section">
            <div class="market">
                <b>Pomegranate (Sangli APMC)</b><br><br>
                Min ₹5000 | Modal ₹7000 | Max ₹8500
            </div>
        </div>

        <div class="footer">Shet Mitra 🌿</div>

    </div>
    </body>
    </html>
    """