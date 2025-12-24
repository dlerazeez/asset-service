import os
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.factory import create_app
from app.routers import cash

app = create_app()

app.include_router(cash.router, prefix="/api/cash", tags=["cash"])

# Resolve frontend directory robustly:
# - if ./frontend exists next to this file, use it
# - else if ../frontend exists, use it
HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [
    os.path.join(HERE, "frontend"),
    os.path.join(os.path.dirname(HERE), "frontend"),
]
FRONTEND_DIR = next((p for p in CANDIDATES if os.path.isdir(p)), None)

if not FRONTEND_DIR:
    raise RuntimeError(f"Frontend directory not found. Checked: {CANDIDATES}")

# Mount /static to frontend folder (so /static/style.css works)
if not any(getattr(r, "path", None) == "/static" for r in app.routes):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(FRONTEND_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()
