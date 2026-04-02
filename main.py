from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.mount("/static", StaticFiles(directory="."), name="static")


@app.get("/")
def home():
    return {"message": "Shet Mitra API running 🚀"}


@app.get("/report-ui", response_class=HTMLResponse)
def report_ui():
    return """
    <html>
    <head>
    <title>Shet Mitra Report</title>

    <style>
    body { font-family: Arial; background:#000; display:flex; justify-content:center;}
    .container { width:420px; background:#f5f5f5; border-radius:12px; overflow:hidden;}

    .header {
        background:#1b5e20; color:white; padding:12px;
        display:flex; justify-content:space-between; align-items:center;
    }

    .logo { width:48px; height:48px; }
    .title { font-size:16px; font-weight:bold; }
    .sub { font-size:12px; }

    .right { font-size:12px; text-align:right; }

    .section { padding:14px; }

    .grid {
        display:grid; grid-template-columns:repeat(4,1fr);
        gap:4px; padding:6px; background:#ddd; border-radius:10px;
    }

    .cell { height:60px; border-radius:4px; }
    .g{background:#2e7d32;} .lg{background:#66bb6a;}
    .y{background:#fdd835;} .o{background:#fb8c00;} .r{background:#e53935;}

    .card { background:#eee; padding:10px; border-radius:10px; font-size:13px;}

    .legend span { font-size:12px; font-weight:bold; display:block; margin:2px 0;}

    .bar { display:flex; height:10px; border-radius:5px; overflow:hidden; margin-top:6px;}
    .bar div { height:100%; }

    .weather {
        background:#bbdefb; padding:12px;
        border-left:6px solid #1565c0; border-radius:10px;
    }

    .market {
        background:#c8e6c9; padding:12px;
        border-left:6px solid #1b5e20; border-radius:10px;
    }

    table { width:100%; text-align:center; font-size:13px; }

    .green{color:green; font-weight:bold;}
    .red{color:red; font-weight:bold;}
    .orange{color:orange; font-weight:bold;}

    .footer { text-align:center; font-size:12px; padding:10px; font-weight:bold;}

    </style>
    </head>

    <body>
    <div class="container">

    <!-- HEADER -->
    <div class="header">
        <div style="display:flex; gap:10px; align-items:center;">
            <img src="/static/logo.svg" class="logo">
            <div>
                <div class="title">Shet Mitra</div>
                <div class="sub">Sahyadri Krushi Intelligence</div>
            </div>
        </div>

        <div class="right">
            30 Mar 2026<br>
            <b>Upcoming Feature</b><br>
            Disease Identification
        </div>
    </div>

    <!-- HEATMAP -->
    <div class="section">
        <div class="grid">
            <div class="cell g"></div><div class="cell lg"></div><div class="cell g"></div><div class="cell lg"></div>
            <div class="cell lg"></div><div class="cell o"></div><div class="cell y"></div><div class="cell g"></div>
            <div class="cell lg"></div><div class="cell r"></div><div class="cell o"></div><div class="cell g"></div>
            <div class="cell g"></div><div class="cell y"></div><div class="cell lg"></div><div class="cell g"></div>
        </div>
    </div>

    <!-- LEGEND + ACTION -->
    <div class="section card">
        <div class="legend">
            <span class="green">🟢 Green → Healthy → Continue current schedule</span>
            <span>🟡 Yellow → Mild → Monitor closely</span>
            <span>🟠 Orange → Moderate → Inspect & treat</span>
            <span class="red">🔴 Red → Severe → Immediate action required</span>
        </div>
    </div>

    <!-- DISTRIBUTION -->
    <div class="section card">
        <b>Farm Condition Breakdown</b>

        <div class="bar">
            <div style="width:56%; background:#2e7d32;"></div>
            <div style="width:19%; background:#fdd835;"></div>
            <div style="width:19%; background:#fb8c00;"></div>
            <div style="width:6%; background:#e53935;"></div>
        </div>

        <div style="font-size:12px; margin-top:6px;">
            56% Healthy | 19% Mild | 19% Moderate | 6% Severe
        </div>

        <div style="margin-top:6px;">
            <b>Overall Status:</b> Moderate (Localized Stress)
        </div>
    </div>

    <!-- WEATHER -->
    <div class="section">
        <div class="weather">

            <table>
                <tr>
                    <th></th><th>31 Mar</th><th>1 Apr</th><th>2 Apr</th><th>3 Apr</th><th>4 Apr</th>
                </tr>

                <tr>
                    <td><b>Weather</b></td>
                    <td>☀️</td><td>🌤️</td><td>🌧️</td><td>🌧️</td><td>🌤️</td>
                </tr>

                <tr>
                    <td><b>Spray</b></td>
                    <td class="green">OK</td>
                    <td class="green">OK</td>
                    <td class="red">No</td>
                    <td class="red">No</td>
                    <td class="orange">Monitor</td>
                </tr>

                <tr>
                    <td><b>Drying</b></td>
                    <td class="green">Perfect</td>
                    <td class="orange">Slow</td>
                    <td class="red">Mold Risk</td>
                    <td class="red">Mold Risk</td>
                    <td>No Drying</td>
                </tr>
            </table>

            <div style="text-align:center; font-size:12px; font-weight:bold; margin-top:5px;">
                Weather (5-day summary)
            </div>

        </div>
    </div>

    <!-- MARKET BELOW WEATHER -->
    <div class="section">
        <div class="market">

            <b>Pomegranate (Sangli APMC)</b><br>
            <span style="font-size:12px;"><b>Updated:</b> 30 Mar 2026, 11:30 AM</span>

            <table>
                <tr><th></th><th>Min</th><th>Modal</th><th>Max</th></tr>
                <tr><td><b>Price</b></td><td>₹5000</td><td>₹7000</td><td>₹8500</td></tr>
                <tr><td><b>Arrival</b></td><td>80</td><td>120</td><td>60</td></tr>
            </table>

            <br>

            <table>
                <tr><th></th><th>A</th><th>B</th><th>C</th></tr>
                <tr><td><b>Price</b></td><td>₹8500</td><td>₹7000</td><td>₹5000</td></tr>
            </table>

            <br>
            <b>Advice:</b> Hold premium, sell mid-grade

        </div>
    </div>

    <div class="footer">
        Source: Satellite + Weather + Market<br>
        Shet Mitra 🌿
    </div>

    </div>
    </body>
    </html>
    """