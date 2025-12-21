from fastapi import APIRouter, Request, HTTPException
from app.core.zoho import zoho_request, zoho_json

router = APIRouter()

FIXED_ASSET_TYPE_MAP = {
    "COMPUTERS": {
        "fixed_asset_type_id": "5571826000000132005",
        "asset_account_id": "5571826000000132052",
        "expense_account_id": "5571826000000000451",
        "depreciation_account_id": "5571826000000567220",
    },
    "FURNITURE": {
        "fixed_asset_type_id": "5571826000000132005",
        "asset_account_id": "5571826000000000367",
        "expense_account_id": "5571826000000000451",
        "depreciation_account_id": "5571826000000905582",
    },
}


@router.post("/assets/create")
def create_asset(request: Request, payload: dict):
    required = [
        "asset_name",
        "asset_category",
        "asset_cost",
        "purchase_date",
        "depreciation_start_date",
        "useful_life_months",
    ]
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(400, f"Missing fields: {', '.join(missing)}")

    category = payload["asset_category"]
    if category not in FIXED_ASSET_TYPE_MAP:
        raise HTTPException(400, "Invalid asset_category")

    m = FIXED_ASSET_TYPE_MAP[category]
    settings = request.app.state.settings

    zoho_payload = {
        "asset_name": payload["asset_name"],
        "fixed_asset_type_id": m["fixed_asset_type_id"],
        "asset_account_id": m["asset_account_id"],
        "expense_account_id": m["expense_account_id"],
        "depreciation_account_id": m["depreciation_account_id"],
        "asset_cost": payload["asset_cost"],
        "asset_purchase_date": payload["purchase_date"],
        "depreciation_start_date": payload["depreciation_start_date"],
        "total_life": payload["useful_life_months"],
        "salvage_value": payload.get("salvage_value", 0),
        "dep_start_value": payload["asset_cost"],
        "depreciation_method": "straight_line",
        "depreciation_frequency": "monthly",
        "computation_type": "prorata_basis",
    }

    resp = zoho_request(
        settings,
        "POST",
        "/fixedassets",
        json=zoho_payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    data = zoho_json(resp)

    if data.get("code") != 0:
        raise HTTPException(400, data)

    fa = data["fixed_asset"]
    return {"ok": True, "fixed_asset_id": fa["fixed_asset_id"], "asset_number": fa["asset_number"], "status": fa["status"]}


@router.get("/assets/all")
def list_all_assets(request: Request):
    settings = request.app.state.settings

    page = 1
    per_page = 200
    all_assets = []

    while True:
        resp = zoho_request(
            settings,
            "GET",
            "/fixedassets",
            params={"filter_by": "Status.All", "page": page, "per_page": per_page},
            timeout=30,
        )
        data = zoho_json(resp)
        if data.get("code") != 0:
            raise HTTPException(400, data)

        all_assets.extend(data.get("fixed_assets", []))
        page_context = data.get("page_context", {})
        if not page_context.get("has_more_page"):
            break
        page += 1

    return {"ok": True, "count": len(all_assets), "assets": all_assets}


@router.get("/assets/by-id/{asset_id}")
def get_asset_by_id(request: Request, asset_id: str):
    settings = request.app.state.settings
    resp = zoho_request(settings, "GET", f"/fixedassets/{asset_id}", timeout=30)
    return zoho_json(resp)
