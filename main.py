from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Serve logo
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
                font-family: Arial;
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
                padding: 12px;
                display: flex;
                align-items: center;
                gap: 10px;
            }

            .logo {
                width: 45px;
                height: 45px;
                border-radius: 6px;
            }

            .title {
                font-size: 18px;
                font-weight: bold;
            }

            .sub {
                font-size: 12px;
                opacity: 0.9;
            }

            .meta {
                padding: 10px;
                font-size: 12px;
                background: #e8f5e9;
            }

            .section {
                padding: 15px;
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
            .g { background: #2e7d32; }
            .lg { background: #66bb6a; }
            .y { background: #fdd835; }
            .o { background: #fb8c00; }
            .r { background: #e53935; }

            .cards {
                display: flex;
                gap: 10px;
            }

            .card {
                flex: 1;
                background: #eee;
                padding: 10px;
                border-radius: 10px;
            }

            .advisory {
                background: #ffe0b2;
                padding: 10px;
                border-left: 5px solid orange;
                border-radius: 8px;
            }

            .market {
                background: #e8f5e9;
                padding: 12px;
                border-left: 6px solid #2e7d32;
                border-radius: 10px;
            }

            .weather {
                background: #e3f2fd;
                padding: 12px;
                border-left: 6px solid #2196f3;
                border-radius: 10px;
            }

            table {
                width: 100%;
                text-align: center;
                font-size: 14px;
            }

            .highlight {
                font-weight: bold;
                color: #2e7d32;
            }

            .footer {
                text-align: center;
                font-size: 12px;
                padding: 10px;
                color: #555;
            }
        </style>
    </head>

    <body>
        <div class="container">

            <!-- HEADER -->
            <div class="header">
                <img src="/static/logo.png" class="logo">
                <div>
                    <div class="title">Shet Mitra</div>
                    <div class="sub">Sahyadri Krushi Intelligence</div>
                </div>
            </div>

            <div class="meta">
                Patil Farm, Tasgaon | 5 acres | 30 Mar 2026
            </div>

            <!-- HEATMAP -->
            <div class="section">
                <div class="grid">
                    <div class="cell g"></div>
                    <div class="cell lg"></div>
                    <div class="cell g"></div>
                    <div class="cell lg"></div>

                    <div class="cell lg"></div>
                    <div class="cell o"></div>
                    <div class="cell y"></div>
                    <div class="cell g"></div>

                    <div class="cell lg"></div>
                    <div class="cell r"></div>
                    <div class="cell o"></div>
                    <div class="cell g"></div>

                    <div class="cell g"></div>
                    <div class="cell y"></div>
                    <div class="cell lg"></div>
                    <div class="cell g"></div>
                </div>
            </div>

            <!-- SUMMARY -->
            <div class="section">
                <div class="cards">
                    <div class="card">
                        <b>Leaf health</b><br>
                        <span style="color:green;font-size:18px;">Good</span>
                    </div>
                    <div class="card">
                        <b>Water condition</b><br>
                        <span style="color:orange;font-size:18px;">Check</span>
                    </div>
                </div>
            </div>

            <!-- ADVISORY -->
            <div class="section">
                <div class="advisory">
                    <b>What to do</b>
                    <ul>
                        <li>Check irrigation in dry zones</li>
                        <li>Inspect weak patch for pests</li>
                        <li>Continue current schedule</li>
                    </ul>
                </div>
            </div>

            <!-- MARKET -->
            <div class="section">
                <div class="market">

                    <b>Pomegranate (Sangli APMC)</b><br><br>

                    <table>
                        <tr><th></th><th>Min</th><th>Modal</th><th>Max</th></tr>
                        <tr>
                            <td><b>Price</b></td>
                            <td>₹5000</td>
                            <td class="highlight">₹7000</td>
                            <td>₹8500</td>
                        </tr>
                        <tr>
                            <td><b>Arrival</b></td>
                            <td>80</td>
                            <td>120</td>
                            <td>60</td>
                        </tr>
                    </table>

                    <br>

                    <table>
                        <tr><th></th><th>A</th><th>B</th><th>C</th></tr>
                        <tr>
                            <td><b>Price</b></td>
                            <td class="highlight">₹8500</td>
                            <td>₹7000</td>
                            <td>₹5000</td>
                        </tr>
                    </table>

                    <br>
                    <b>Advice:</b> Hold premium, sell mid-grade

                </div>
            </div>

            <!-- WEATHER -->
            <div class="section">
                <div class="weather">

                    <table>
                        <tr><th></th><th>31 Mar</th><th>1 Apr</th><th>2 Apr</th><th>3 Apr</th><th>4 Apr</th></tr>
                        <tr><td><b>Weather</b></td><td>☀️</td><td>🌤️</td><td>🌧️</td><td>🌧️</td><td>🌤️</td></tr>
                        <tr><td><b>Advice</b></td>
                            <td style="color:green;">Spray OK</td>
                            <td style="color:green;">Spray OK</td>
                            <td style="color:red;">No Spray</td>
                            <td style="color:red;">No Spray</td>
                            <td style="color:orange;">Monitor</td>
                        </tr>
                    </table>

                    <div style="text-align:center; margin-top:6px; font-size:13px;">
                        Weather (5-day summary)
                    </div>

                </div>
            </div>

            <!-- FOOTER -->
            <div class="footer">
                Source: Satellite + Weather + Market<br>
                Shet Mitra 🌿
            </div>

        </div>
    </body>
    </html>
    """