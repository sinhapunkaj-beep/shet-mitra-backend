from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Static files
app.mount("/static", StaticFiles(directory="."), name="static")


@app.get("/")
def home():
    return {"message": "Shet Mitra API running 🚀"}


# ✅ SAFE HTML LOADING (NO JINJA2)
@app.get("/report-ui", response_class=HTMLResponse)
def report_ui():
    with open("templates/report.html", "r", encoding="utf-8") as f:
        return f.read()