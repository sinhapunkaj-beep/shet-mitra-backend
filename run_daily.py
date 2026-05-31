import requests
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# 🔹 API
API_URL = "http://127.0.0.1:8000/recommendation?crop=pomegranate&farmer_mandi=Sangli"

# 🔹 Fetch data
response = requests.get(API_URL)
data = response.json()

# 🔹 Create PDF
file_name = f"farmer_report_{datetime.now().date()}.pdf"

doc = SimpleDocTemplate(file_name)
styles = getSampleStyleSheet()

content = []

content.append(Paragraph("Shet Mitra Report", styles["Title"]))
content.append(Spacer(1, 10))

content.append(Paragraph(f"Date: {datetime.now()}", styles["Normal"]))
content.append(Spacer(1, 10))

content.append(Paragraph(f"Verdict: {data.get('verdict')}", styles["Normal"]))
content.append(Paragraph(f"Best Mandi: {data.get('best_mandi')}", styles["Normal"]))
content.append(Paragraph(f"Expected Price: ₹{data.get('expected_price')}", styles["Normal"]))
content.append(Paragraph(f"Reason: {data.get('reason')}", styles["Normal"]))

doc.build(content)

print("✅ Report Generated:", file_name)