from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import requests
import os
import time
from typing import Optional, Dict, Any

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
    raise RuntimeError("Missing Zoho OAuth environment variables")

# -------------------------------------------------
# OAuth token cache (in-memory)
# -------------------------------------------------
_access_token: Optional[str] = None
_token_expiry: float = 0.0


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
        timeout=15,
    )

    data = resp.json()

    if "access_token" not in data:
        raise RuntimeError(f"Failed to refresh Zoho token: {data}")

    _access_token = data["access_token"]
    _token_expiry = time.time() + int(data.get("expires_in", 3600)) - 60

    return _access_token


def zoho_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> requests.Response:
    token = get_access_token()

    base_headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    if headers:
        base_headers.update(headers)

    q = {"organization_id": ZOHO_ORG_ID}
    if params:
        # Do not allow overriding org_id from caller
        params = dict(params)
        params.pop("organization_id", None)
        q.update(params)

    url = f"{ZOHO_BASE}{path}"
    return requests.request(
        method=method,
        url=url,
        params=q,
        json=json,
        files=files,
        headers=base_headers,
        timeout=timeout,
    )


def zoho_json_or_file(resp: requests.Response):
    """
    Some Zoho endpoints return JSON; others may return a file (receipt).
    This helper returns JSON when possible, otherwise returns raw file bytes.
    """
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type or "text/json" in content_type:
        return resp.json()
    return Response(content=resp.content, media_type=resp.headers.get("Content-Type", "application/octet-stream"))


# -------------------------------------------------
# FastAPI app
# -------------------------------------------------
app = FastAPI(title="Fixed Asset & Expense Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()


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
# Assets APIs
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
    )

    data = resp.json()

    if data.get("code") != 0:
        raise HTTPException(400, data)

    fa = data["fixed_asset"]

    return {
        "ok": True,
        "fixed_asset_id": fa["fixed_asset_id"],
        "asset_number": fa["asset_number"],
        "status": fa["status"],
    }


@app.get("/assets/all")
def list_all_assets():
    page = 1
    per_page = 200
    all_assets = []

    while True:
        resp = zoho_request(
            "GET",
            "/fixedassets",
            params={
                "filter_by": "Status.All",
                "page": page,
                "per_page": per_page,
            },
        )

        data = resp.json()

        if data.get("code") != 0:
            raise HTTPException(400, data)

        all_assets.extend(data.get("fixed_assets", []))

        page_context = data.get("page_context", {})
        if not page_context.get("has_more_page"):
            break

        page += 1

    return {"ok": True, "count": len(all_assets), "assets": all_assets}


@app.get("/assets/by-id/{asset_id}")
def get_asset_by_id(asset_id: str):
    resp = zoho_request("GET", f"/fixedassets/{asset_id}")
    return resp.json()


# -------------------------------------------------
# Expenses APIs (based on expenses.yml)
# -------------------------------------------------

# Create Expense (required fields: date, account_id, amount, paid_through_account_id)
@app.post("/expenses/create")
def create_expense(payload: dict):
    required = ["date", "account_id", "amount", "paid_through_account_id"]
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(status_code=400, detail={"error": "Missing fields", "missing": missing})

    # Guard against empty strings coming from the UI
    for k in ["account_id", "paid_through_account_id"]:
        if str(payload.get(k, "")).strip() == "":
            raise HTTPException(status_code=400, detail={"error": f"{k} is empty"})

    resp = zoho_request(
        "POST",
        "/expenses",
        json=payload,
        headers={"Content-Type": "application/json"},
    )

    # Always try to capture Zoho payload
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    # Log full detail in Render logs
    print("ZOHO /expenses status:", resp.status_code)
    print("ZOHO /expenses response:", data)

    # If Zoho returns non-200
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail={"zoho_http_status": resp.status_code, "zoho": data},
        )

    # If Zoho returns code != 0
    if isinstance(data, dict) and data.get("code") != 0:
        raise HTTPException(
            status_code=400,
            detail={"zoho_http_status": resp.status_code, "zoho": data},
        )

    return {"ok": True, "data": data}



# List Expenses (pass-through all query params you send from UI)
@app.get("/expenses/list")
def list_expenses(request: Request):
    # Forward whatever filters you provide (page/per_page/search_text/filter_by/sort_column/etc.)
    params = dict(request.query_params)

    resp = zoho_request("GET", "/expenses", params=params)
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)

    return {"ok": True, "data": data}


# Get Expense by ID
@app.get("/expenses/by-id/{expense_id}")
def get_expense_by_id(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}")
    return resp.json()


# Update Expense by ID (supports delete_receipt query param)
@app.put("/expenses/update/{expense_id}")
def update_expense(expense_id: str, payload: dict, delete_receipt: bool = False):
    resp = zoho_request(
        "PUT",
        f"/expenses/{expense_id}",
        params={"delete_receipt": str(delete_receipt).lower()},
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


# Delete Expense by ID
@app.delete("/expenses/delete/{expense_id}")
def delete_expense(expense_id: str):
    resp = zoho_request("DELETE", f"/expenses/{expense_id}")
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


# Comments/History
@app.get("/expenses/{expense_id}/comments")
def list_expense_comments(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}/comments")
    return resp.json()


# Receipt endpoints
@app.get("/expenses/{expense_id}/receipt")
def get_expense_receipt(expense_id: str, preview: bool = False):
    resp = zoho_request(
        "GET",
        f"/expenses/{expense_id}/receipt",
        params={"preview": str(preview).lower()},
    )
    return zoho_json_or_file(resp)


@app.post("/expenses/{expense_id}/receipt")
def add_expense_receipt(expense_id: str, receipt: UploadFile = File(...)):
    files = {"receipt": (receipt.filename, receipt.file, receipt.content_type)}
    resp = zoho_request("POST", f"/expenses/{expense_id}/receipt", files=files)
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


@app.delete("/expenses/{expense_id}/receipt")
def delete_expense_receipt(expense_id: str):
    resp = zoho_request("DELETE", f"/expenses/{expense_id}/receipt")
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


# Attachments (multipart)
@app.post("/expenses/{expense_id}/attachment")
def add_expense_attachment(expense_id: str, attachment: UploadFile = File(...)):
    files = {"attachment": (attachment.filename, attachment.file, attachment.content_type)}
    resp = zoho_request("POST", f"/expenses/{expense_id}/attachment", files=files)
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


# Update Expense by unique custom field (Zoho: PUT /expenses with unique headers)
@app.put("/expenses/update-by-unique")
def update_expense_by_unique(
    payload: dict,
    x_unique_identifier_key: str = Header(..., alias="X-Unique-Identifier-Key"),
    x_unique_identifier_value: str = Header(..., alias="X-Unique-Identifier-Value"),
    x_upsert: Optional[bool] = Header(None, alias="X-Upsert"),
):
    headers = {
        "X-Unique-Identifier-Key": x_unique_identifier_key,
        "X-Unique-Identifier-Value": x_unique_identifier_value,
        "Content-Type": "application/json",
    }
    if x_upsert is not None:
        headers["X-Upsert"] = "true" if x_upsert else "false"

    resp = zoho_request("PUT", "/expenses", json=payload, headers=headers)
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


# -------------------------------------------------
# Employees APIs (for mileage workflows)
# -------------------------------------------------
@app.get("/employees/list")
def list_employees(request: Request):
    params = dict(request.query_params)
    resp = zoho_request("GET", "/employees", params=params)
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


@app.post("/employees/create")
def create_employee(payload: dict):
    # Zoho expects JSON body with employee fields
    resp = zoho_request(
        "POST",
        "/employees",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


@app.get("/employees/by-id/{employee_id}")
def get_employee(employee_id: str):
    resp = zoho_request("GET", f"/employees/{employee_id}")
    return resp.json()


@app.delete("/employees/delete/{employee_id}")
def delete_employee(employee_id: str):
    # NOTE: Zoho endpoint is /employee/{employee_id} (singular)
    resp = zoho_request("DELETE", f"/employee/{employee_id}")
    data = resp.json()
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}
