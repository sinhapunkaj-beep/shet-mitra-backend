from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

# Static files (logo, etc.)
app.mount("/static", StaticFiles(directory="."), name="static")

# Templates folder
templates = Jinja2Templates(directory="templates")


@app.get("/")
def home():
    return {"message": "Shet Mitra API running 🚀"}


# ✅ Proper template rendering (BEST WAY)
@app.get("/report-ui", response_class=HTMLResponse)
def report_ui(request: Request):

    # Dummy grid (your existing UI needs this)
    grid = [
        ["green", "lightgreen", "green", "lightgreen"],
        ["lightgreen", "orange", "yellow", "green"],
        ["lightgreen", "red", "orange", "green"],
        ["green", "yellow", "lightgreen", "green"]
    ]

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "date": "30 Mar 2026",
            "grid": grid
        }
    )