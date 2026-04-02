# =========================
# TASK 2 (STABLE UI BASE)
# =========================

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.mount("/static", StaticFiles(directory="."), name="static")


@app.get("/report-ui", response_class=HTMLResponse)
def report():
    return """
    <html>
    <head>
    <style>
    body { font-family: Arial; background:#000; display:flex; justify-content:center; }
    .container { width:420px; background:#f5f5f5; border-radius:12px; overflow:hidden; }

    .header { background:#1b5e20; color:white; padding:10px; }
    .date { text-align:center; font-size:12px; margin-bottom:6px; font-weight:bold; }

    .row { display:flex; justify-content:space-between; align-items:center; }
    .left { display:flex; gap:10px; align-items:center; }

    .logo { width:48px; height:48px; }
    .title { font-weight:bold; font-size:16px; }
    .sub { font-size:12px; }

    .star {
        background:#ffca28;
        padding:6px 10px;
        border-radius:20px;
        font-size:11px;
        font-weight:bold;
        color:#000;
    }

    .section { padding:12px; }

    .grid {
        display:grid;
        grid-template-columns:repeat(4,1fr);
        gap:4px;
        background:#ddd;
        padding:6px;
        border-radius:10px;
    }

    .cell { height:60px; border-radius:4px; }
    .g{background:#2e7d32;} .lg{background:#66bb6a;}
    .y{background:#fdd835;} .o{background:#fb8c00;} .r{background:#e53935;}

    .card { background:#eee; padding:12px; border-radius:10px; font-size:13px; }

    .green{color:#2e7d32;font-weight:bold;}
    .yellow{color:#f9a825;font-weight:bold;}
    .orange{color:#ef6c00;font-weight:bold;}
    .red{color:#c62828;font-weight:bold;}

    .weather {
        background:#bbdefb;
        padding:12px;
        border-left:6px solid #1565c0;
        border-radius:10px;
    }

    .market {
        background:#c8e6c9;
        padding:12px;
        border-left:6px solid #1b5e20;
        border-radius:10px;
    }

    table { width:100%; text-align:center; border-collapse:collapse; }
    td, th { padding:5px; }

    .footer { text-align:center; padding:10px; font-size:12px; font-weight:bold; }
    </style>
    </head>

    <body>
    <div class="container">

        <div class="header">
            <div class="date">30 Mar 2026</div>
            <div class="row">
                <div class="left">
                    <img src="/static/logo.svg" class="logo">
                    <div>
                        <div class="title">Shet Mitra</div>
                        <div class="sub">Sahyadri Krushi Intelligence</div>
                    </div>
                </div>

                <div class="star">
                    ⭐ Upcoming Feature<br>
                    Disease Identification
                </div>
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

        <!-- LEGEND -->
        <div class="section">
            <div class="card">
                <div class="green">🟢 Green → Healthy → Continue current schedule</div>
                <div class="yellow">🟡 Yellow → Mild → Monitor closely</div>
                <div class="orange">🟠 Orange → Moderate → Inspect & treat</div>
                <div class="red">🔴 Red → Severe → Immediate action required</div>
            </div>
        </div>

        <!-- WEATHER (STATIC) -->
        <div class="section">
            <div class="weather">
                <div style="text-align:center;font-weight:bold;">Weather (5-day summary)</div>
                <table>
                    <tr><th></th><th>31 Mar</th><th>1 Apr</th><th>2 Apr</th><th>3 Apr</th><th>4 Apr</th></tr>
                    <tr><td>Weather</td><td>☀️</td><td>🌤️</td><td>🌧️</td><td>🌧️</td><td>🌤️</td></tr>
                    <tr><td></td><td>Sunny</td><td>Cloudy</td><td>Expected Rain</td><td>Expected Rain</td><td>Cloudy</td></tr>
                    <tr><td>Spray</td><td class="green">OK</td><td class="green">OK</td><td class="red">No</td><td class="red">No</td><td class="orange">Monitor</td></tr>
                    <tr><td>Drying</td><td class="green">Perfect</td><td class="orange">Slow</td><td class="orange">No Drying</td><td class="orange">No Drying</td><td class="orange">Slow</td></tr>
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