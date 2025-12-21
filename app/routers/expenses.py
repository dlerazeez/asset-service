from fastapi import APIRouter, HTTPException, UploadFile, File, Response
from app.core.zoho import zoho_request, zoho_json
from app.core.utils import guess_extension

router = APIRouter(prefix="/expenses", tags=["Expenses (Zoho)"])


@router.get("/list")
def list_expenses(page: int = 1, per_page: int = 50, filter_by: str = "Status.All", search_text: str | None = None):
    params = {"page": page, "per_page": per_page, "filter_by": filter_by}
    if search_text:
        params["search_text"] = search_text

    resp = zoho_request("GET", "/expenses", params=params, timeout=30)
    data = zoho_json(resp)
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


@router.get("/by-id/{expense_id}")
def get_expense_by_id(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}", timeout=30)
    return zoho_json(resp)


@router.put("/update/{expense_id}")
def update_expense(expense_id: str, payload: dict):
    # Map UI "notes" -> Zoho "description", and pass reference_number.
    zoho_payload = {}

    if "date" in payload:
        zoho_payload["date"] = payload["date"]
    if "account_id" in payload:
        zoho_payload["account_id"] = str(payload["account_id"]).strip()
    if "paid_through_account_id" in payload:
        zoho_payload["paid_through_account_id"] = str(payload["paid_through_account_id"]).strip()
    if "amount" in payload:
        zoho_payload["amount"] = float(payload["amount"])
    if "vendor_id" in payload:
        zoho_payload["vendor_id"] = str(payload["vendor_id"]).strip() if payload["vendor_id"] else None
    if "notes" in payload:
        zoho_payload["description"] = (payload.get("notes") or "").strip()
    if "reference_number" in payload:
        zoho_payload["reference_number"] = (payload.get("reference_number") or "").strip()

    resp = zoho_request(
        "PUT",
        f"/expenses/{expense_id}",
        json=zoho_payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    data = zoho_json(resp)
    if data.get("code") != 0:
        raise HTTPException(400, data)
    return {"ok": True, "data": data}


@router.get("/{expense_id}/receipt")
def get_expense_receipt(expense_id: str):
    resp = zoho_request("GET", f"/expenses/{expense_id}/receipt", timeout=60)
    content_type = resp.headers.get("content-type", "application/octet-stream")
    return Response(content=resp.content, media_type=content_type)


@router.post("/{expense_id}/attachment")
def add_expense_attachment(
    expense_id: str,
    file: UploadFile = File(...),
):
    # This is NOT the receipt endpoint; it attaches an additional file (no receipt override). :contentReference[oaicite:2]{index=2}
    ext = guess_extension(file.filename, file.content_type)
    safe_name = (file.filename or f"attachment{ext}")

    files = {"attachment": (safe_name, file.file, file.content_type or "application/octet-stream")}

    resp = zoho_request("POST", f"/expenses/{expense_id}/attachment", files=files, timeout=90)
    data = zoho_json(resp)

    if resp.status_code >= 400 or data.get("code") not in (0, None):
        raise HTTPException(resp.status_code if resp.status_code >= 400 else 400, data)

    return {"ok": True, "zoho": data}
