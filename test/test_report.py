import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from services.report_generator import generate_farmer_report

data = {
    "grid": [
        ["G","LG","G","LG"],
        ["LG","O","Y","G"],
        ["LG","R","O","G"],
        ["G","Y","LG","G"]
    ],

    "weather": [
        {"date":"2 Apr","spray":"OK","drying":"Perfect","condition":"sunny"},
        {"date":"3 Apr","spray":"OK","drying":"Slow","condition":"partly_cloudy"},
        {"date":"4 Apr","spray":"No","drying":"No","condition":"heavy_rain"},
        {"date":"5 Apr","spray":"No","drying":"No","condition":"heavy_rain"},
        {"date":"6 Apr","spray":"Monitor","drying":"No","condition":"partly_cloudy"}
    ],

    "rain_alert":"4 Apr → 5 Apr<br>STOP spraying<br>Resume after rain",

    "crop":"Pomegranate",
    "mandi":"Sangli APMC",
    "updated_at":"2 Apr 2026, 11:30 AM",

    # ✅ ADD THIS BLOCK (THIS IS THE MISSING PIECE)
    "grade_prices":[
        {"grade":"Premium","price":8500},
        {"grade":"Medium","price":7000},
        {"grade":"Low","price":5000}
    ],

    "price":{"min":5000,"modal":7000,"max":8500},
    "arrival":"80|120|60",

    "advice":"Hold premium, sell mid-grade"
}

html = generate_farmer_report(data)

with open("report.html","w",encoding="utf-8") as f:
    f.write(html)

print("✅ FINAL LOCKED REPORT GENERATED")