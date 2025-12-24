from __future__ import annotations

import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .routers.assets import router as assets_router
from .routers.expenses import router as expenses_router
from .routers.pending import router as pending_router
from .routers.coa import router as coa_router
from .routers.vendors import router as vendors_router

from .routers.auth import router as auth_router
from .routers.receipts import router as receipts_router
from .routers.accrued import router as accrued_router
from .routers.cash import router as cash_router  # ✅ ADDED


def create_app() -> FastAPI:
    app = FastAPI(title="Asset Service", version="1.0.0")

    # API routers
    app.include_router(assets_router, prefix="/api/assets", tags=["assets"])
    app.include_router(expenses_router, prefix="/api/expenses", tags=["expenses"])
    app.include_router(pending_router, prefix="/api/pending", tags=["pending"])
    app.include_router(coa_router, prefix="/api/coa", tags=["coa"])
    app.include_router(vendors_router, prefix="/api/vendors", tags=["vendors"])

    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    app.include_router(receipts_router, prefix="/api/receipts", tags=["receipts"])
    app.include_router(accrued_router, prefix="/api/accrued", tags=["accrued"])

    app.include_router(cash_router, prefix="/api", tags=["cash"])  # ✅ ADDED

    # Static UI
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.join(base_dir, "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Uploads (receipts)
    os.makedirs(settings.uploads_dir, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")

    return app
