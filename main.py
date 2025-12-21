from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import requests
import os
import time
from typing import Optional, List, Dict, Any


# -------------------------------------------------
# Load environment variables
# -------------------------------------------------
load_dotenv()

ZOHO_ORG_ID = "868880872"
ZOHO_BASE = "https://www.zohoapis.com/books/v3"
ZOHO_AUTH_URL = "https://accounts.zoho.com/oauth/v2/token"

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")

if not all([ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN]):
    raise RuntimeError("Missing Zoho OAuth environment variables (ZOHO_CLIENT_ID/SECRET/REFRESH_TOKEN)")


# -------------------------------------------------
# OAuth token cache (in-memory)
# -------------------------------------------------
_access_token: Optional[str] = None
_token_expiry: float = 0


def get_access_token() -> str:
    global _access_token, _token_expiry

    if _access_token and time.time() < _token_expiry:
        return _access_token

    resp = requests.post(
        ZOHO_AUTH_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": ZOHO_CLIENT_ID,
            "client_secret": ZOHO_CLIENT_SECRET,
            "refresh_token": ZOHO_REFRESH_TOKEN,
        },
        timeout=20,
    )

    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Failed to refresh Zoho token: {data}")

    _access_token = data["access_token"]
    _token_expiry = time.time() + int(data.get("expires_in", 3600)) - 60
    return _access_token


def zoho_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {"Authorization": f"Zoho-oauthtoken {get_access_token()}"}
    if extra:
        h.update(extra)
    return h


def zoho_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
):
    p = params.copy() if params else {}
    p["organization_id"] = ZOHO_ORG_ID

    url = f"{ZOHO_BASE}{path}"
    hdrs = zoho_headers(headers)

    return requests.request(
        method=method.upper(),
        url=url,
        params=p,
        json=json,
        data=data,
        files=files,
        headers=hdrs,
        timeout=timeout,
    )


def zoho_json_or_text(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


# -------------------------------------------------
# FastAPI app
# -------------------------------------------------
app = FastAPI(title="Laveen Assets & Expenses Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend folder as /static (contains style.css)
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/favicon.ico")
def favicon():
    # Avoid noisy 404 logs
    return Response(status_code=204)


# -------------------------------------------------
# Fixed Asset mapping (LOCKED)
# -------------------------------------------------
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


# -------------------------------------------------
# Create Fixed Asset (Draft)
# -------------------------------------------------
@app.post("/assets/create")
def create_asset(payload: dict):
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
        "POST",
        "/fixedassets",
        json=zoho_payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    data = zoho_json_or_text(resp)
    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("code") not in (0, None)):
        raise HTTPException(status_code=400, detail=data)

    fa = data.get("fixed_asset", {})
    return {"ok": True, "fixed_asset_id": fa.get("fixed_asset_id"), "asset_number": fa.get("asset_number"), "status": fa.get("status")}


# -------------------------------------------------
# Retrieve ALL Fixed Assets (Draft + Active + Others)
# -------------------------------------------------
@app.get("/assets/all")
def list_all_assets():
    page = 1
    per_page = 200
    all_assets = []

    while True:
        resp = zoho_request(
            "GET",
            "/fixedassets",
            params={"filter_by": "Status.All", "page": page, "per_page": per_page},
            timeout=30,
        )
        data = zoho_json_or_text(resp)

        if resp.status_code >= 400 or (isinstance(data, dict) and data.get("code") != 0):
            raise HTTPException(status_code=400, detail=data)

        all_assets.extend(data.get("fixed_assets", []))
        page_context = data.get("page_context", {})
        if not page_context.get("has_more_page"):
            break
        page += 1

    return {"ok": True, "count": len(all_assets), "assets": all_assets}


@app.get("/assets/by-id/{asset_id}")
def get_asset_by_id(asset_id: str):
    resp = zoho_request("GET", f"/fixedassets/{asset_id}", timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=data)
    return data


# =================================================
# EXPENSES (Full API proxy + UI-friendly aliases)
# =================================================

# --- UI-friendly endpoints (your frontend uses these) ---

@app.post("/expenses/create")
def create_expense_ui(payload: dict):
    required = ["date", "account_id", "amount", "paid_through_account_id"]
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(status_code=400, detail={"error": "Missing fields", "missing": missing})

    for k in ["account_id", "paid_through_account_id"]:
        if str(payload.get(k, "")).strip() == "":
            raise HTTPException(status_code=400, detail={"error": f"{k} is empty"})

    resp = zoho_request(
        "POST",
        "/expenses",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    data = zoho_json_or_text(resp)
    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("code") != 0):
        raise HTTPException(status_code=400, detail=data)

    return {"ok": True, "data": data}


@app.get("/expenses/list")
def list_expenses_ui(
    page: int = 1,
    per_page: int = 50,
    filter_by: str = "Status.All",
    search_text: str = "",
):
    params = {"page": page, "per_page": per_page, "filter_by": filter_by}
    if search_text.strip():
        params["search_text"] = search_text.strip()

    resp = zoho_request("GET", "/expenses", params=params, timeout=30)
    data = zoho_json_or_text(resp)

    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("code") != 0):
        raise HTTPException(status_code=400, detail=data)

    return {"ok": True, "data": data}


@app.get("/expenses/by-id/{expense_id}")
def get_expense_by_id_ui(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}", timeout=30)
    data = zoho_json_or_text(resp)

    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=data)

    return data


# --- OpenAPI-spec-aligned endpoints from your expenses.yml ---

@app.get("/expenses")
def list_expenses(
    page: int = 1,
    per_page: int = 50,
    filter_by: str = "Status.All",
    search_text: str = "",
    sort_column: str = "",
    sort_order: str = "",
):
    params = {"page": page, "per_page": per_page, "filter_by": filter_by}
    if search_text.strip():
        params["search_text"] = search_text.strip()
    if sort_column.strip():
        params["sort_column"] = sort_column.strip()
    if sort_order.strip():
        params["sort_order"] = sort_order.strip()

    resp = zoho_request("GET", "/expenses", params=params, timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.post("/expenses")
def create_expense(payload: dict):
    resp = zoho_request("POST", "/expenses", json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.put("/expenses")
def update_expense_by_custom_field(payload: dict):
    # Zoho supports PUT /expenses for update by custom field unique value in some flows.
    resp = zoho_request("PUT", "/expenses", json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.get("/expenses/{expense_id}")
def get_expense(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}", timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.put("/expenses/{expense_id}")
def update_expense(expense_id: str, payload: dict):
    resp = zoho_request("PUT", f"/expenses/{expense_id}", json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.delete("/expenses/{expense_id}")
def delete_expense(expense_id: str):
    resp = zoho_request("DELETE", f"/expenses/{expense_id}", timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.get("/expenses/{expense_id}/comments")
def get_expense_comments(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}/comments", timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.get("/expenses/{expense_id}/receipt")
def get_expense_receipt(expense_id: str):
    # Zoho may return the receipt file stream. We pass through content.
    resp = zoho_request("GET", f"/expenses/{expense_id}/receipt", timeout=30)
    if resp.status_code >= 400:
        data = zoho_json_or_text(resp)
        return JSONResponse(status_code=resp.status_code, content=data)

    content_type = resp.headers.get("Content-Type", "application/octet-stream")
    return Response(content=resp.content, media_type=content_type)


@app.post("/expenses/{expense_id}/receipt")
def upload_expense_receipt(expense_id: str, receipt: UploadFile = File(...)):
    files = {
        "receipt": (receipt.filename, receipt.file, receipt.content_type or "application/octet-stream")
    }
    resp = zoho_request("POST", f"/expenses/{expense_id}/receipt", files=files, timeout=60)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.delete("/expenses/{expense_id}/receipt")
def delete_expense_receipt(expense_id: str):
    resp = zoho_request("DELETE", f"/expenses/{expense_id}/receipt", timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.post("/expenses/{expense_id}/attachment")
def add_expense_attachment(
    expense_id: str,
    attachment: UploadFile = File(...),
    totalFiles: int = Form(1),
    document_ids: List[str] = Form([]),
):
    files = {
        "attachment": (attachment.filename, attachment.file, attachment.content_type or "application/octet-stream")
    }
    form_data = {"totalFiles": str(totalFiles)}
    # Zoho expects repeated form keys for arrays; requests supports tuples list:
    data_items = list(form_data.items())
    for doc_id in document_ids:
        if str(doc_id).strip():
            data_items.append(("document_ids", str(doc_id).strip()))

    resp = zoho_request("POST", f"/expenses/{expense_id}/attachment", data=data_items, files=files, timeout=60)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


# =================================================
# EMPLOYEES (from your expenses.yml)
# =================================================

@app.get("/employees")
def list_employees(page: int = 1, per_page: int = 200, search_text: str = ""):
    params = {"page": page, "per_page": per_page}
    if search_text.strip():
        params["search_text"] = search_text.strip()

    resp = zoho_request("GET", "/employees", params=params, timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.post("/employees")
def create_employee(payload: dict):
    resp = zoho_request("POST", "/employees", json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.get("/employees/{employee_id}")
def get_employee(employee_id: str):
    resp = zoho_request("GET", f"/employees/{employee_id}", timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


@app.delete("/employee/{employee_id}")
def delete_employee(employee_id: str):
    # Note: spec has singular /employee/{employee_id} for delete
    resp = zoho_request("DELETE", f"/employees/{employee_id}", timeout=30)
    data = zoho_json_or_text(resp)
    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)
    return data


# =================================================
# VENDORS (dropdown support)
# =================================================

@app.get("/vendors/list")
def list_vendors(search_text: str = "", page: int = 1, per_page: int = 200):
    params: Dict[str, Any] = {"page": page, "per_page": per_page}
    if search_text.strip():
        params["search_text"] = search_text.strip()

    resp = zoho_request("GET", "/contacts", params=params, timeout=30)
    data = zoho_json_or_text(resp)

    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("code") != 0):
        raise HTTPException(status_code=400, detail=data)

    contacts = data.get("contacts", [])
    vendors = []

    for c in contacts:
        t = (c.get("contact_type") or c.get("contact_type_formatted") or "").lower()
        is_vendor_flag = bool(c.get("is_vendor"))  # sometimes present
        if is_vendor_flag or ("vendor" in t):
            vendors.append({
                "vendor_id": c.get("contact_id"),
                "vendor_name": c.get("contact_name") or c.get("company_name") or c.get("contact_id"),
            })

    return {"ok": True, "count": len(vendors), "vendors": vendors}
