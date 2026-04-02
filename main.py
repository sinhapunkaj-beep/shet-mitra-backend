// ===== START: TASK 3.1 =====

<script>
// ===============================
// 🌦️ WEATHER SERVICE (SAFE MODE)
// ===============================

const WEATHER_CONFIG = {
    latitude: 17.3850,
    longitude: 78.4867,
    timezone: "auto",
    cacheKey: "weather_cache_v1",
    cacheDuration: 3 * 60 * 60 * 1000 // 3 hours
};

async function fetchWeatherSafe() {
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${WEATHER_CONFIG.latitude}&longitude=${WEATHER_CONFIG.longitude}&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&current_weather=true&timezone=${WEATHER_CONFIG.timezone}`;

    try {
        const res = await fetch(url);
        const data = await res.json();

        localStorage.setItem(WEATHER_CONFIG.cacheKey, JSON.stringify({
            data: data,
            time: Date.now()
        }));

        console.log("✅ Live weather fetched");
        return data;

    } catch (error) {
        console.warn("⚠️ API failed, switching to cache");

        const cache = JSON.parse(localStorage.getItem(WEATHER_CONFIG.cacheKey));

        if (cache && (Date.now() - cache.time < WEATHER_CONFIG.cacheDuration)) {
            console.log("✅ Using cached weather");
            return cache.data;
        }

        console.error("❌ No cache available");
        return null;
    }
}
</script>

// ===== END: TASK 3.1 =====

// ===== START: TASK 3.2 =====

<script>
// ===============================
// 🧠 WEATHER → AGRI LOGIC
// ===============================

function processWeatherSafe(data) {
    if (!data) return null;

    const current = data.current_weather;
    const daily = data.daily;

    const todayMax = daily.temperature_2m_max[0];
    const rainProb = daily.precipitation_probability_max[0];

    let alerts = [];

    if (todayMax > 40) {
        alerts.push("🔥 Heat Stress Risk");
    }

    if (rainProb < 10) {
        alerts.push("💧 Irrigation Required");
    }

    if (rainProb > 40) {
        alerts.push("⛈️ Spray Not Recommended");
    }

    return {
        temp: current.temperature,
        wind: current.windspeed,
        alerts: alerts,
        forecast: daily
    };
}
</script>

// ===== END: TASK 3.2 =====

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

            /* HEADER */
            .header {
                background: #1b5e20;
                color: white;
                padding: 12px;
            }

            .date {
                text-align: center;
                font-size: 12px;
                font-weight: bold;
                margin-bottom: 6px;
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
                font-weight: bold;
                font-size: 11px;
                padding: 6px 10px;
                border-radius: 20px;
                text-align: center;
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

            /* GRID */
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

            /* COLORS */
            .green { color:#2e7d32; font-weight:bold; }
            .yellow { color:#f9a825; font-weight:bold; }
            .orange { color:#ef6c00; font-weight:bold; }
            .red { color:#c62828; font-weight:bold; }

            /* WEATHER */
            .weather {
                background:#bbdefb;
                padding:12px;
                border-left:6px solid #1565c0;
                border-radius:10px;
                font-size:13px;
            }

            /* MARKET */
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
                        <td>Sunny</td><td>Cloudy</td><td>Rain</td><td>Rain</td><td>Cloudy</td>
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

        <!-- MARKET -->
        <div class="section">
            <div class="market">

                <b>Pomegranate (Sangli APMC)</b>

                <div style="font-size:12px; margin:6px 0 10px;">
                    <b>Updated:</b> 30 Mar 2026, 11:30 AM
                </div>

                <table>
                    <tr><th></th><th>Min</th><th>Modal</th><th>Max</th></tr>
                    <tr><td><b>Price</b></td><td>₹5000</td><td>₹7000</td><td>₹8500</td></tr>
                    <tr><td><b>Arrival</b></td><td>80</td><td>120</td><td>60</td></tr>
                </table>

                <table>
                    <tr><th></th><th>A</th><th>B</th><th>C</th></tr>
                    <tr><td><b>Price</b></td><td>₹8500</td><td>₹7000</td><td>₹5000</td></tr>
                </table>

                <div style="margin-top:6px;">
                    <b>Advice:</b> Hold premium, sell mid-grade
                </div>

            </div>
        </div>

        <div class="footer">
            Shet Mitra 🌿
        </div>

    </div>
    </body>
    </html>
    """

// ===== START: TASK 3.3 =====

<script>
// ===============================
// 🔗 BIND DATA TO UI
// ===============================

function updateWeatherUI(weather) {
    if (!weather) return;

    // SAFE MODE: only update if elements exist
    const tempEl = document.getElementById("weather-temp");
    const windEl = document.getElementById("weather-wind");
    const alertEl = document.getElementById("weather-alerts");

    if (tempEl) tempEl.innerText = `${weather.temp}°C`;
    if (windEl) windEl.innerText = `${weather.wind} km/h`;

    if (alertEl) {
        alertEl.innerHTML = weather.alerts
            .map(a => `<div class="alert-badge">${a}</div>`)
            .join("");
    }
}
</script>

// ===== END: TASK 3.3 =====

// ===== START: TASK 3.4 =====

<script>
// ===============================
// 🚀 INIT (SAFE MODE)
// ===============================

(async function initWeather() {
    try {
        const raw = await fetchWeatherSafe();
        const processed = processWeatherSafe(raw);

        console.log("🌦️ Weather Processed:", processed);

        updateWeatherUI(processed);

    } catch (err) {
        console.error("❌ Weather init failed:", err);
    }
})();
</script>

// ===== END: TASK 3.4 =====