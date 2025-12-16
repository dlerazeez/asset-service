from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import requests
import os
import time

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
# Token cache (in-memory)
# -------------------------------------------------
_access_token = None
_token_expiry = 0


def get_access_token():
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


# -------------------------------------------------
# FastAPI app
# -------------------------------------------------
app = FastAPI(title="Fixed Asset Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()


# -------------------------------------------------
# Fixed Asset mapping (locked)
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

    token = get_access_token()

    resp = requests.post(
        f"{ZOHO_BASE}/fixedassets",
        params={"organization_id": ZOHO_ORG_ID},
        json=zoho_payload,
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json",
        },
        timeout=30,
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


# -------------------------------------------------
# Retrieve ALL Fixed Assets
# -------------------------------------------------
@app.get("/assets/all")
def list_all_assets():
    token = get_access_token()

    page = 1
    per_page = 200
    all_assets = []

    while True:
        resp = requests.get(
            f"{ZOHO_BASE}/fixedassets",
            params={
                "organization_id": ZOHO_ORG_ID,
                "page": page,
                "per_page": per_page,
            },
            headers={"Authorization": f"Zoho-oauthtoken {token}"},
            timeout=30,
        )

        data = resp.json()

        if data.get("code") != 0:
            raise HTTPException(400, data)

        all_assets.extend(data.get("fixed_assets", []))

        page_context = data.get("page_context", {})
        if not page_context.get("has_more_page"):
            break

        page += 1

    return {
        "ok": True,
        "count": len(all_assets),
        "assets": all_assets,
    }
