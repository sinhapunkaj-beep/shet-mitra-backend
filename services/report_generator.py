from datetime import datetime

def generate_farmer_report(data):

    date_str = datetime.now().strftime("%d %b %Y")

    color_map = {
        "G": "green",
        "LG": "lightgreen",
        "Y": "yellow",
        "O": "orange",
        "R": "red"
    }

    # GRID
    grid_html = ""
    for row in data["grid"]:
        grid_html += "<tr>"
        for cell in row:
            grid_html += f"<td class='cell {color_map[cell]}'></td>"
        grid_html += "</tr>"

    # WEATHER ICONS
    def get_icon(day):
        condition = day.get("condition", "sunny")

        if condition == "sunny":
            return "☀️"
        elif condition == "partly_cloudy":
            return "⛅"
        elif condition == "heavy_rain":
            return "⛈️"
        return "☀️"

    weather_icons = "".join([f"<td>{get_icon(d)}</td>" for d in data["weather"]])

    # SPRAY COLORS
    def spray_color(val):
        if val == "OK":
            return "green"
        elif val == "Monitor":
            return "orange"
        return "red"

    spray_row = "".join([
        f"<td style='color:{spray_color(d['spray'])};font-weight:600'>{d['spray']}</td>"
        for d in data["weather"]
    ])

    # GRADE TABLE
    grade_section = ""
    if "grade_prices" in data:

        def grade_color(g):
            if g == "Premium":
                return "green"
            elif g == "Medium":
                return "orange"
            return "red"

        grade_rows = ""
        for g in data["grade_prices"]:
            grade_rows += f"""
            <tr>
                <td>{g['grade']}</td>
                <td style="color:{grade_color(g['grade'])};font-weight:600;">₹{g['price']}</td>
            </tr>
            """

        grade_section = f"""
        <div class="section-title">Grade-wise Prices</div>
        <table>
            <tr><th>Grade</th><th>Price</th></tr>
            {grade_rows}
        </table>
        """

    html = f"""
    <html>
    <head>
    <style>

    body, table, td, th {{
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        font-size:13px;
    }}

    body {{
        background:#f2f2f2;
    }}

    .container {{
        width:650px;
        margin:auto;
        background:white;
        border-radius:20px;
        border:3px solid #2e7d32;
        overflow:hidden;
    }}

    /* HEADER */
    .header {{
        background:#1b5e20;
        color:white;
        padding:10px;
    }}

    .header-top {{
        text-align:center;
        font-weight:600;
        font-size:16px;
    }}

    .header-row {{
        display:flex;
        justify-content:space-between;
        align-items:center;
    }}

    .left {{
        display:flex;
        align-items:center;
        gap:10px;
    }}

    .logo {{ width:45px; }}

    .title {{ font-size:18px; font-weight:700; }}

    .subtitle {{ font-size:12px; opacity:0.9; }}

    .badge {{
        background:#ffd54f;
        padding:6px 12px;
        border-radius:18px;
        font-size:12px;
        font-weight:600;
    }}

    table {{
        width:100%;
        border-collapse:collapse;
    }}

    td, th {{
        padding:10px;
        text-align:center;
    }}

    /* GRID */
    .grid table {{
        border-spacing:10px;
        padding:15px;
    }}

    .cell {{
        height:60px;
        border-radius:10px;
    }}

    .green {{background:#2e7d32;}}
    .lightgreen {{background:#66bb6a;}}
    .yellow {{background:#f2c94c;}}
    .orange {{background:#f57c00;}}
    .red {{background:#e53935;}}

    /* LEGEND */
    .legend {{
        background:#dcdcdc;
        margin:10px;
        padding:12px;
        border-radius:10px;
        line-height:1.8;
    }}

    /* WEATHER */
    .weather {{
        background:#a7c4dd;
        margin:10px;
        padding:12px;
        border-radius:10px;
        text-align:center;
    }}

    /* ALERT (FINAL PERFECT MATCH) */
    .alert {{
        background:#e5c2c2;
        margin:12px;
        padding:10px 14px;
        border-radius:12px;
        border-left:8px solid #d32f2f;
        text-align:center;
        line-height:1.5;
    }}

    /* PRICE */
    .price {{
        background:#9ac29a;
        margin:10px;
        padding:12px;
        border-radius:10px;
    }}

    .section-title {{
        font-weight:700;
        margin-top:10px;
        margin-bottom:5px;
    }}

    .footer {{
        text-align:center;
        padding:10px;
    }}

    </style>
    </head>

    <body>

    <div class="container">

    <!-- HEADER -->
    <div class="header">
        <div class="header-top">{date_str}</div>
        <div class="header-row">
            <div class="left">
                <img src="logo.svg" class="logo">
                <div>
                    <div class="title">Shet Mitra</div>
                    <div class="subtitle">Sahyadri Krushi Intelligence</div>
                </div>
            </div>
            <div class="badge">⭐ Upcoming Feature: Disease Identification</div>
        </div>
    </div>

    <!-- GRID -->
    <div class="grid">
        <table>{grid_html}</table>
    </div>

    <!-- LEGEND -->
    <div class="legend">
        🟢 Green → Healthy → Continue schedule<br>
        🟡 Yellow → Mild → Monitor closely<br>
        🟠 Orange → Moderate → Inspect & treat<br>
        🔴 Red → Severe → Immediate action required
    </div>

    <!-- WEATHER -->
    <div class="weather">
        <b>Weather (5-day summary)</b>
        <table>
            <tr><td></td>{"".join([f"<td><b>{d['date']}</b></td>" for d in data["weather"]])}</tr>
            <tr><td>Weather</td>{weather_icons}</tr>
            <tr><td>Spray</td>{spray_row}</tr>
            <tr><td>Drying</td>{"".join([f"<td>{d['drying']}</td>" for d in data["weather"]])}</tr>
        </table>
    </div>

    <!-- ALERT -->
    <div class="alert">
        <div style="font-weight:700;">☔ HEAVY RAIN WINDOW</div>
        <div style="margin-top:4px;">4 Apr → 5 Apr</div>
        <div>STOP spraying</div>
        <div>Resume after rain</div>
    </div>

    <!-- PRICE -->
    <div class="price">

        <b>{data["crop"]} ({data["mandi"]})</b><br>
        Updated: {data["updated_at"]}

        {grade_section}

        <div class="section-title">Market Summary</div>
        <table>
            <tr><th></th><th>Min</th><th>Modal</th><th>Max</th></tr>
            <tr>
                <td>Price</td>
                <td>₹{data["price"]["min"]}</td>
                <td>₹{data["price"]["modal"]}</td>
                <td>₹{data["price"]["max"]}</td>
            </tr>
            <tr>
                <td>Arrival</td>
                <td>{data["arrival"].split("|")[0]}</td>
                <td>{data["arrival"].split("|")[1]}</td>
                <td>{data["arrival"].split("|")[2]}</td>
            </tr>
        </table>

        <br>
        Advice: {data["advice"]}
    </div>

    <div class="footer">Shet Mitra 🌿</div>

    </div>

    </body>
    </html>
    """

    return html