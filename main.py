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
            body {
                font-family: Arial, sans-serif;
                background: #000;
                display: flex;
                justify-content: center;
            }

            .container {
                width: 420px;
                background: #f5f5f5;
                border-radius: 12px;
                overflow: hidden;
            }

            .header {
                background: #1b5e20;
                color: white;
                padding: 10px 12px 14px;
            }

            .date {
                text-align: center;
                font-size: 12px;
                margin-bottom: 8px;
                font-weight: bold;
            }

            .header-row {
                display: flex;
                justify-content: space-between;
                align-items: center;
            }

            .left {
                display: flex;
                gap: 10px;
                align-items: center;
            }

            .logo { width: 48px; height: 48px; }

            .title { font-size: 16px; font-weight: bold; }
            .sub { font-size: 12px; }

            .star {
                background: #ffca28;
                color: #000;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: bold;
                border-radius: 20px;
                text-align: center;
                max-width: 140px;
                line-height: 1.2;
            }

            .section {
                padding: 12px;
                margin-bottom: 8px;
            }

            .card {
                background: #eee;
                padding: 12px;
                border-radius: 10px;
                font-size: 13px;
            }

            .grid {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 4px;
                background: #ddd;
                padding: 6px;
                border-radius: 10px;
            }

            .cell { height: 60px; border-radius: 4px; }
            .g { background:#2e7d32; }
            .lg { background:#66bb6a; }
            .y { background:#fdd835; }
            .o { background:#fb8c00; }
            .r { background:#e53935; }

            .green { color:#2e7d32; font-weight:bold; }
            .yellow { color:#f9a825; font-weight:bold; }
            .orange { color:#ef6c00; font-weight:bold; }
            .red { color:#c62828; font-weight:bold; }

            .weather {
                background:#bbdefb;
                padding:12px;
                border-left:6px solid #1565c0;
                border-radius:10px;
                font-size:13px;
            }

            .market {
                background:#c8e6c9;
                padding:12px;
                border-left:6px solid #1b5e20;
                border-radius:10px;
                font-size:13px;
            }

            table {
                width:100%;
                text-align:center;
                font-size:13px;
                border-collapse:collapse;
                margin-bottom:10px;
            }

            td, th {
                padding:5px;
            }

            .footer {
                text-align:center;
                font-size:12px;
                padding:10px;
                font-weight:bold;
            }

        </style>
    </head>

    <body>
    <div class="container">

        <!-- HEADER -->
        <div class="header">

            <div class="date">30 Mar 2026</div>

            <div class="header-row">
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

        <!-- WEATHER -->
        <div class="section">
            <div class="weather">

                <div style="text-align:center; font-weight:bold; margin-bottom:8px;">
                    Weather (5-day summary)
                </div>

                <table>
                    <tr>
                        <th></th><th>31 Mar</th><th>1 Apr</th><th>2 Apr</th><th>3 Apr</th><th>4 Apr</th>
                    </tr>

                    <tr>
                        <td><b>Weather</b></td>
                        <td>☀️</td><td>🌤️</td><td>🌧️</td><td>🌧️</td><td>🌤️</td>
                    </tr>

                    <tr>
                        <td></td>
                        <td>Sunny</td>
                        <td>Cloudy</td>
                        <td>Expected Rain</td>
                        <td>Expected Rain</td>
                        <td>Cloudy</td>
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

            </div>
        </div>

        <div class="footer">
            Shet Mitra 🌿
        </div>

    </div>
    </body>
    </html>
    """