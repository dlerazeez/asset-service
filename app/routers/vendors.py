from fastapi import APIRouter, Request, HTTPException
from app.core.zoho import zoho_request, zoho_json

router = APIRouter()


@router.get("/vendors/list")
def list_vendors(request: Request, page: int = 1, per_page: int = 200):
    settings = request.app.state.settings
    resp = zoho_request(
        settings,
        "GET",
        "/contacts",
        params={"page": page, "per_page": per_page, "contact_type": "vendor"},
        timeout=30,
    )
    data = zoho_json(resp)
    if data.get("code") != 0:
        raise HTTPException(400, data)

    vendors = data.get("contacts", []) or []
    # Ensure a stable vendor_name field for the frontend
    for v in vendors:
        if "vendor_name" not in v:
            v["vendor_name"] = v.get("contact_name") or ""

    return {"ok": True, "vendors": vendors}
