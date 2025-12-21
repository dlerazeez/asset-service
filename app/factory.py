import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import init_settings
from app.core.zoho import ZohoClient
from app.services.coa_store import COAStore

from app.routers.coa import router as coa_router
from app.routers.assets import router as assets_router
from app.routers.expenses import router as expenses_router
from app.routers.vendors import router as vendors_router


def create_app(*, base_dir: str) -> FastAPI:
    """
    Creates the FastAPI app and wires dependencies via app.state:
      - app.state.settings
      - app.state.zoho
      - app.state.coa_store
    """
    settings = init_settings(base_dir=base_dir)

    app = FastAPI(title="Assets & Expenses Service")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static mount (unchanged semantics): /static/* -> frontend/*
    app.mount("/static", StaticFiles(directory=settings.FRONTEND_DIR), name="static")

    # Dependencies (singletons)
    app.state.settings = settings
    app.state.zoho = ZohoClient(settings=settings)

    coa_store = COAStore(settings=settings)
    coa_store.load()  # match old import-time behavior (loads once at startup)
    app.state.coa_store = coa_store

    @app.get("/", response_class=HTMLResponse)
    def serve_frontend():
        index_path = os.path.join(settings.FRONTEND_DIR, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "coa_loaded": app.state.coa_store.load_error is None,
            "coa_error": app.state.coa_store.load_error,
        }

    # Routers (endpoint paths preserved)
    app.include_router(coa_router)
    app.include_router(assets_router)
    app.include_router(expenses_router)
    app.include_router(vendors_router)

    return app
