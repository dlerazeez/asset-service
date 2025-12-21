import time
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import JSONResponse, Response

from app.core.utils import zoho_json, extract_cf_expense_report, guess_extension

router = APIRouter()

# Expenses (unchanged behavior: do NOT write reference_number)


@router.post("/expenses/create")
def create_expense(request: Request, payload: dict):
    required = ["date", "account_id", "amount", "paid_through_account_id"]
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(400, {"error": "Missing fields", "missing": missing})

    zoho_payload = {
        "date": payload["date"],
        "account_id": str(payload["account_id"]).strip(),
        "paid_through_account_id": str(payload["paid_through_account_id"]).strip(),
        "amount": float(payload["amount"]),
    }
# UI calls it "notes", Zoho field is "description"
notes = payload.get("notes") or payload.get("description")
if isinstance(notes, str) and notes.strip():
    zoho_payload["description"] = notes.strip()[:100]  # Zoho max length 100
# Reference field (Zoho uses reference_number)
ref = payload.get("reference_number") or payload.get("reference")
if isinstance(ref, str) and ref.strip():
    zoho_payload["reference_number"] = ref.strip()[:100]  # Zoho max length 100
    if payload.get("vendor_id"):
        zoho_payload["vendor_id"] = payload["vendor_id"]

    zoho = request.app.state.zoho
    settings = request.app.state.settings

    resp = zoho.request(
        "POST",
        "/expenses",
        json=zoho_payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    data = zoho_json(resp)

    if data.get("code") != 0:
        raise HTTPException(400, data)

    expense_id = (data.get("expense") or {}).get("expense_id")
    if not expense_id:
        raise HTTPException(400, {"error": "Expense created but expense_id not returned", "zoho": data})

    report_no = None
    last_get = None
    for _ in range(10):
        time.sleep(1)
        r = zoho.request("GET", f"/expenses/{expense_id}", timeout=30)
        last_get = zoho_json(r)
        if isinstance(last_get, dict) and last_get.get("code") == 0:
            exp_obj = last_get.get("expense") or {}
            report_no = extract_cf_expense_report(exp_obj, cf_api_name=settings.EXPENSE_CF_API_NAME)
            if report_no:
                break

    return {
        "ok": True,
        "expense_id": expense_id,
        "expense_report_no": report_no,
        "created": data,
        "latest": last_get,
    }


@router.get("/expenses/list")
def list_expenses(
    request: Request,
    page: int = 1,
    per_page: int = 50,
    filter_by: str = "Status.All",
    search_text: str | None = None,
):
    params = {"page": page, "per_page": per_page, "filter_by": filter_by}
    if search_text:
        params["search_text"] = search_text

    zoho = request.app.state.zoho
    resp = zoho.request("GET", "/expenses", params=params, timeout=30)
    data = zoho_json(resp)

    if data.get("code") != 0:
        raise HTTPException(400, data)

    return {"ok": True, "data": data}


@router.get("/expenses/by-id/{expense_id}")
def get_expense_by_id(request: Request, expense_id: str):
    zoho = request.app.state.zoho
    resp = zoho.request("GET", f"/expenses/{expense_id}", timeout=30)
    return zoho_json(resp)


@router.post("/expenses/{expense_id}/receipt")
def upload_expense_receipt(
    request: Request,
    expense_id: str,
    receipt: UploadFile = File(...),
    report_no: str | None = Query(default=None),
):
    zoho = request.app.state.zoho
    settings = request.app.state.settings

    if not report_no:
        r = zoho.request("GET", f"/expenses/{expense_id}", timeout=30)
        d = zoho_json(r)
        if isinstance(d, dict) and d.get("code") == 0:
            exp_obj = d.get("expense") or {}
            report_no = extract_cf_expense_report(exp_obj, cf_api_name=settings.EXPENSE_CF_API_NAME)

    report_no = (report_no or f"EXP{expense_id}").strip()

    ext = guess_extension(receipt.filename, receipt.content_type)
    new_filename = f"{report_no}{ext}"

    files = {"receipt": (new_filename, receipt.file, receipt.content_type or "application/octet-stream")}

    resp = zoho.request("POST", f"/expenses/{expense_id}/receipt", files=files, timeout=90)
    data = zoho_json(resp)

    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=data)

    return {"ok": True, "expense_id": expense_id, "expense_report_no": report_no, "filename": new_filename, "zoho": data}


@router.get("/expenses/{expense_id}/receipt")
def get_expense_receipt(request: Request, expense_id: str):
    zoho = request.app.state.zoho
    resp = zoho.request("GET", f"/expenses/{expense_id}/receipt", timeout=60)
    content_type = resp.headers.get("content-type", "application/octet-stream")
    return Response(content=resp.content, media_type=content_type)


@router.delete("/expenses/{expense_id}/receipt")
def delete_expense_receipt(request: Request, expense_id: str):
    zoho = request.app.state.zoho
    resp = zoho.request("DELETE", f"/expenses/{expense_id}/receipt", timeout=60)
    data = zoho_json(resp)
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}
