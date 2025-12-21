from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import requests
import os
import time
import sqlite3
from datetime import datetime

# -------------------------------------------------
# Load environment variables
# -------------------------------------------------
load_dotenv()

ZOHO_ORG_ID = os.getenv("ZOHO_ORG_ID", "868880872")
ZOHO_BASE = os.getenv("ZOHO_BASE", "https://www.zohoapis.com/books/v3")
ZOHO_AUTH_URL = os.getenv("ZOHO_AUTH_URL", "https://accounts.zoho.com/oauth/v2/token")

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")

# Expense report numbering / persistence
DB_PATH = os.getenv("DB_PATH", "./app.db")              # set to /var/data/app.db on Render Disk
EXPENSE_PREFIX = os.getenv("EXPENSE_PREFIX", "WS")      # no dash required
EXPENSE_CF_API_NAME = os.getenv("EXPENSE_CF_API_NAME", "cf_expense_report")

if not all([ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN]):
    raise RuntimeError("Missing Zoho OAuth environment variables: ZOHO_CLIENT_ID/ZOHO_CLIENT_SECRET/ZOHO_REFRESH_TOKEN")

# -------------------------------------------------
# OAuth token cache (in-memory)
# -------------------------------------------------
_access_token = None
_token_expiry = 0

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

def zoho_headers(extra: dict | None = None) -> dict:
    token = get_access_token()
    h = {"Authorization": f"Zoho-oauthtoken {token}"}
    if extra:
        h.update(extra)
    return h

def zoho_json_or_text(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text, "status_code": resp.status_code}

def zoho_request(method: str, path: str, *, params=None, json=None, files=None, headers=None, timeout=30):
    if not path.startswith("/"):
        path = "/" + path
    url = f"{ZOHO_BASE}{path}"
    p = params.copy() if isinstance(params, dict) else {}
    p["organization_id"] = ZOHO_ORG_ID

    h = zoho_headers(headers or {})

    return requests.request(
        method=method.upper(),
        url=url,
        params=p,
        json=json,
        files=files,
        headers=h,
        timeout=timeout,
    )

# -------------------------------------------------
# DB helpers (SQLite)
# -------------------------------------------------
def db_connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db_connect()
    cur = conn.cursor()

    # last sequence per year
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expense_seq (
            year INTEGER PRIMARY KEY,
            last_seq INTEGER NOT NULL
        )
    """)

    # mapping expense_id -> report_no
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expense_map (
            expense_id TEXT PRIMARY KEY,
            report_no TEXT NOT NULL UNIQUE,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

def generate_expense_report_no():
    """
    Format: WS + YY + MM + ### (min 3 digits)
    Example: WS2512001
    Sequence resets per year.
    """
    now = datetime.now()
    year = now.year
    month = now.month
    yy = str(year)[-2:]
    mm = f"{month:02d}"

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")  # lock to avoid duplicates

    row = cur.execute("SELECT last_seq FROM expense_seq WHERE year=?", (year,)).fetchone()
    if row:
        seq = int(row["last_seq"]) + 1
        cur.execute("UPDATE expense_seq SET last_seq=? WHERE year=?", (seq, year))
    else:
        seq = 1
        cur.execute("INSERT INTO expense_seq(year, last_seq) VALUES(?, ?)", (year, seq))

    conn.commit()
    conn.close()

    report_no = f"{EXPENSE_PREFIX}{yy}{mm}{seq:03d}"
    return report_no, year, month, seq

def save_expense_report_mapping(expense_id: str, report_no: str, year: int, month: int, seq: int):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO expense_map (expense_id, report_no, year, month, seq, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (expense_id, report_no, year, month, seq, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_report_no_for_expense(expense_id: str) -> str | None:
    conn = db_connect()
    cur = conn.cursor()
    row = cur.execute("SELECT report_no FROM expense_map WHERE expense_id=?", (expense_id,)).fetchone()
    conn.close()
    return row["report_no"] if row else None

def guess_extension(filename: str | None, content_type: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext:
        return ext
    if content_type:
        if "pdf" in content_type:
            return ".pdf"
        if "png" in content_type:
            return ".png"
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
    return ".bin"

# -------------------------------------------------
# FastAPI app
# -------------------------------------------------
app = FastAPI(title="Assets & Expenses Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.on_event("startup")
def _startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()

# -------------------------------------------------
# Fixed Asset mapping (LOCKED) - as you had it
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
# Assets endpoints
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

    resp = zoho_request("POST", "/fixedassets", json=zoho_payload, headers={"Content-Type": "application/json"}, timeout=30)
    data = zoho_json_or_text(resp)

    if isinstance(data, dict) and data.get("code") != 0:
        raise HTTPException(400, data)

    fa = data["fixed_asset"]
    return {"ok": True, "fixed_asset_id": fa["fixed_asset_id"], "asset_number": fa["asset_number"], "status": fa["status"]}

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
        if isinstance(data, dict) and data.get("code") != 0:
            raise HTTPException(400, data)

        all_assets.extend(data.get("fixed_assets", []))
        page_context = data.get("page_context", {})
        if not page_context.get("has_more_page"):
            break
        page += 1

    return {"ok": True, "count": len(all_assets), "assets": all_assets}

@app.get("/assets/by-id/{asset_id}")
def get_asset_by_id(asset_id: str):
    resp = zoho_request("GET", f"/fixedassets/{asset_id}", timeout=30)
    return zoho_json_or_text(resp)

# -------------------------------------------------
# Expenses endpoints (with report number + custom field + renamed receipt)
# -------------------------------------------------
@app.post("/expenses/create")
def create_expense(payload: dict):
    required = ["date", "account_id", "amount", "paid_through_account_id"]
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(status_code=400, detail={"error": "Missing fields", "missing": missing})

    # 1) Generate report number
    report_no, year, month, seq = generate_expense_report_no()

    # 2) Build Zoho expense payload (write report number immediately)
    zoho_payload = {
        "date": payload["date"],
        "account_id": str(payload["account_id"]).strip(),
        "paid_through_account_id": str(payload["paid_through_account_id"]).strip(),
        "amount": float(payload["amount"]),
        "reference_number": report_no,
        "custom_field_hash": {
            EXPENSE_CF_API_NAME: report_no
        }
    }

    # Optional fields
    if payload.get("description"):
        zoho_payload["description"] = payload["description"]
    if payload.get("vendor_id"):
        zoho_payload["vendor_id"] = payload["vendor_id"]
    if payload.get("reference_number") and str(payload["reference_number"]).strip():
        # if user passes reference_number, ignore it; system owns it
        pass

    resp = zoho_request("POST", "/expenses", json=zoho_payload, headers={"Content-Type": "application/json"}, timeout=30)
    data = zoho_json_or_text(resp)

    if not (isinstance(data, dict) and data.get("code") == 0):
        raise HTTPException(status_code=400, detail=data)

    expense_id = (data.get("expense") or {}).get("expense_id")
    if not expense_id:
        raise HTTPException(status_code=400, detail={"error": "Created expense but missing expense_id", "zoho": data})

    # 3) Save mapping for file rename later
    save_expense_report_mapping(expense_id, report_no, year, month, seq)

    return {"ok": True, "expense_id": expense_id, "expense_report_no": report_no, "data": data}

@app.get("/expenses/list")
def list_expenses(page: int = 1, per_page: int = 50, filter_by: str = "Status.All", search_text: str | None = None):
    params = {"page": page, "per_page": per_page, "filter_by": filter_by}
    if search_text:
        params["search_text"] = search_text

    resp = zoho_request("GET", "/expenses", params=params, timeout=30)
    data = zoho_json_or_text(resp)
    if not (isinstance(data, dict) and data.get("code") == 0):
        raise HTTPException(status_code=400, detail=data)

    return {"ok": True, "data": data}

@app.get("/expenses/by-id/{expense_id}")
def get_expense_by_id(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}", timeout=30)
    data = zoho_json_or_text(resp)
    return data

@app.post("/expenses/{expense_id}/receipt")
def upload_expense_receipt(expense_id: str, receipt: UploadFile = File(...)):
    report_no = get_report_no_for_expense(expense_id)
    if not report_no:
        # If expense wasn't created through our app, still allow upload
        report_no = f"EXP{expense_id}"

    ext = guess_extension(receipt.filename, receipt.content_type)
    new_filename = f"{report_no}{ext}"

    files = {
        "receipt": (new_filename, receipt.file, receipt.content_type or "application/octet-stream")
    }

    resp = zoho_request("POST", f"/expenses/{expense_id}/receipt", files=files, timeout=90)
    data = zoho_json_or_text(resp)

    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)

    return {"ok": True, "expense_id": expense_id, "expense_report_no": report_no, "filename": new_filename, "zoho": data}

@app.get("/expenses/{expense_id}/receipt")
def get_expense_receipt(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}/receipt", timeout=60)

    # Zoho may return file bytes; just pass through content-type
    content_type = resp.headers.get("content-type", "application/octet-stream")
    return Response(content=resp.content, media_type=content_type)

@app.delete("/expenses/{expense_id}/receipt")
def delete_expense_receipt(expense_id: str):
    resp = zoho_request("DELETE", f"/expenses/{expense_id}/receipt", timeout=60)
    data = zoho_json_or_text(resp)
    if not (isinstance(data, dict) and data.get("code") == 0):
        raise HTTPException(status_code=400, detail=data)
    return {"ok": True, "data": data}

# -------------------------------------------------
# Vendors
# -------------------------------------------------
@app.get("/vendors/list")
def list_vendors(page: int = 1, per_page: int = 200):
    resp = zoho_request("GET", "/contacts", params={"page": page, "per_page": per_page, "contact_type": "vendor"}, timeout=30)
    data = zoho_json_or_text(resp)
    if not (isinstance(data, dict) and data.get("code") == 0):
        raise HTTPException(status_code=400, detail=data)
    vendors = data.get("contacts", [])
    return {"ok": True, "vendors": vendors}
