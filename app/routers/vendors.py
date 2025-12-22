from fastapi import APIRouter
from app.core.zoho import zoho

router = APIRouter(prefix="/api/vendors")

@router.get("")
def list_vendors():
    return {"vendors":zoho.list_vendors()}
