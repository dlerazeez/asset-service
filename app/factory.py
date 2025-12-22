from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.routers.expenses import router as expenses_router
from app.routers.vendors import router as vendors_router

def create_app():
    app = FastAPI()
    app.include_router(expenses_router)
    app.include_router(vendors_router)
    app.mount("/static", StaticFiles(directory="frontend"), name="static")
    return app
