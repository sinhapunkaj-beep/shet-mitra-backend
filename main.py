from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

# Static files
app.mount("/static", StaticFiles(directory="."), name="static")

# Templates
templates = Jinja2Templates(directory="templates")


@app.get("/")
def home():
    return {"message": "Shet Mitra API running 🚀"}


@app.get("/report-ui", response_class=HTMLResponse)
def report_ui(request: Request):

    # ✅ SIMPLE SAFE GRID (no complex objects)
    grid = [
        ["green","lightgreen","green","lightgreen"],
        ["lightgreen","orange","yellow","green"],
        ["lightgreen","red","orange","green"],
        ["green","yellow","lightgreen","green"]
    ]

    context = {
        "request": request,
        "grid": grid,
        "date": "2 Apr 2026"
    }

    return templates.TemplateResponse("report.html", context)