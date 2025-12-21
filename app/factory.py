from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import os

from app.core.config import Settings
from app.services.coa_store import COAStore
from app.services.pending_store import PendingStore

from app.routers.assets import router as assets_router
from app.routers.coa import router as coa_router
from app.routers.expenses import router as expenses_router
from app.routers.vendors import router as vendors_router
from app.routers.pending import router as pending_router


def create_app(base_dir: str | None = None) -> FastAPI:
    if not base_dir:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.dirname(base_dir)  # app/ -> project root

    settings = Settings(base_dir=base_dir)

    app = FastAPI(title="Assets & Expenses Service")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static frontend
    app.mount("/static", StaticFiles(directory=settings.frontend_dir), name="static")

    # Shared state
    app.state.settings = settings
    app.state.coa_store = COAStore(csv_path=settings.coa_csv_path)
    app.state.pending_store = PendingStore(db_path=settings.pending_db_path, storage_dir=settings.storage_dir)

    @app.get("/", response_class=HTMLResponse)
    def serve_frontend():
        index_path = os.path.join(settings.frontend_dir, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "coa_loaded": app.state.coa_store.load_error is None,
            "coa_error": app.state.coa_store.load_error,
            "pending_db": settings.pending_db_path,
        }

    # Routers
    app.include_router(assets_router, tags=["assets"])
    app.include_router(coa_router, tags=["coa"])
    app.include_router(expenses_router, tags=["expenses"])
    app.include_router(vendors_router, tags=["vendors"])
    app.include_router(pending_router, tags=["pending"])

    return app
