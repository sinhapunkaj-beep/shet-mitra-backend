# ===== IMPORTS =====
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ===== APP INIT (MUST BE FIRST) =====
app = FastAPI()


# ===== ROOT =====
@app.get("/")
def home():
    return {"message": "Shet Mitra API running 🚀"}


# ===== REPORT UI =====
@app.get("/report-ui", response_class=HTMLResponse)
def report_ui():
    return "<h1>Report UI Working</h1>"