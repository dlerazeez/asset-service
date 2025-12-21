from fastapi import APIRouter, HTTPException, Request
from app.core.utils import zoho_json

router = APIRouter()


@router.get("/vendors/list")
def list_vendors(request: Request, page: int = 1, per_page: int = 200):
    zoho = request.app.state.zoho
    resp = zoho.request(
        "GET",
        "/contacts",
        params={"page": page, "per_page": per_page, "contact_type": "vendor"},
        timeout=30,
    )
    data = zoho_json(resp)
    if data.get("code") != 0:
        raise HTTPException(400, data)

    return {"ok": True, "vendors": data.get("contacts", [])}
