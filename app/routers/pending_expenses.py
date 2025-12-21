from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
import os
import time
import uuid

from app.core.utils import guess_extension
from app.core.zoho import (
    zoho_create_expense,
    zoho_get_expense,
    extract_cf_expense_report,
    zoho_add_expense_attachment,
)

router = APIRouter()


def _pending_dir(request: Request, pending_id: int) -> str:
    settings = request.app.state.settings
    d = os.path.join(settings.storage_dir, "pending", str(pending_id))
    os.makedirs(d, exist_ok=True)
    return d


@router.post("/pending-expenses/create")
def create_pending_expense(request: Request, payload: dict):
    required = ["date", "account_id", "amount", "paid_through_account_id"]
    missing = [f for f in required if f not in payload or payload[f] in (None, "")]
    if missing:
        raise HTTPException(400, {"error": "Missing fields", "missing": missing})

    store = request.app.state.pending_store
    row = store.create_pending(payload)
    return {"ok": True, "pending": row}


@router.get("/pending-expenses/list")
def list_pending_expenses(request: Request, date_from: str | None = None, date_to: str | None = None):
    store = request.app.state.pending_store
    rows = store.list_pending(date_from=date_from, date_to=date_to)
    # Ensure vendor_name is always present
    for r in rows:
        r["vendor_name"] = r.get("vendor_name") or ""
    return {"ok": True, "count": len(rows), "pending": rows}


@router.get("/pending-expenses/{pending_id}")
def get_pending_expense(request: Request, pending_id: int):
    store = request.app.state.pending_store
    try:
        row = store.get_pending(pending_id)
    except KeyError:
        raise HTTPException(404, "Pending expense not found")
    atts = store.list_attachments(pending_id)
    return {"ok": True, "pending": row, "attachments": atts}


@router.put("/pending-expenses/{pending_id}")
def update_pending_expense(request: Request, pending_id: int, payload: dict):
    store = request.app.state.pending_store
    try:
        row = store.update_pending(pending_id, payload)
    except KeyError:
        raise HTTPException(404, "Pending expense not found")
    return {"ok": True, "pending": row}


@router.post("/pending-expenses/{pending_id}/attachments")
def add_pending_attachment(request: Request, pending_id: int, file: UploadFile = File(...)):
    store = request.app.state.pending_store
    try:
        store.get_pending(pending_id)
    except KeyError:
        raise HTTPException(404, "Pending expense not found")

    ext = guess_extension(file.filename, file.content_type)
    stored_name = f"pending_{pending_id}_{uuid.uuid4().hex}{ext}"

    d = _pending_dir(request, pending_id)
    path = os.path.join(d, stored_name)
    with open(path, "wb") as out:
        out.write(file.file.read())

    att = store.add_attachment(
        pending_id=pending_id,
        original_name=file.filename,
        stored_name=stored_name,
        stored_path=path,
    )
    return {"ok": True, "attachment": att}


@router.get("/pending-expenses/{pending_id}/attachments/list")
def list_pending_attachments(request: Request, pending_id: int):
    store = request.app.state.pending_store
    return {"ok": True, "attachments": store.list_attachments(pending_id)}


@router.get("/pending-expenses/attachments/open/{attachment_id}")
def open_pending_attachment(request: Request, attachment_id: int):
    store = request.app.state.pending_store
    try:
        att = store.get_attachment(attachment_id)
    except KeyError:
        raise HTTPException(404, "Attachment not found")
    path = att["stored_path"]
    if not os.path.exists(path):
        raise HTTPException(404, "File missing on server")
    return FileResponse(path, filename=att.get("original_name") or att["stored_name"])


@router.post("/pending-expenses/{pending_id}/approve")
def approve_pending_expense(request: Request, pending_id: int):
    """
    Posts the pending expense to Zoho and uploads all local attachments as Zoho expense attachments.
    """
    settings = request.app.state.settings
    store = request.app.state.pending_store

    try:
        p = store.get_pending(pending_id)
    except KeyError:
        raise HTTPException(404, "Pending expense not found")

    if p.get("status") != "PENDING":
        raise HTTPException(400, "Pending expense is not in PENDING status")

    zoho_payload = {
        "date": p["date"],
        "account_id": str(p["account_id"]).strip(),
        "paid_through_account_id": str(p["paid_through_account_id"]).strip(),
        "amount": float(p["amount"]),
    }
    if p.get("notes"):
        zoho_payload["description"] = p["notes"]  # Notes -> Zoho description
    if p.get("vendor_id"):
        zoho_payload["vendor_id"] = p["vendor_id"]
    if p.get("reference"):
        zoho_payload["reference_number"] = p["reference"]

    created = zoho_create_expense(settings, zoho_payload)
    if created.get("code") != 0:
        raise HTTPException(400, created)

    expense_id = (created.get("expense") or {}).get("expense_id")
    if not expense_id:
        raise HTTPException(400, {"error": "Zoho created expense but no expense_id returned", "zoho": created})

    # Determine report number for renaming attachments
    exp = zoho_get_expense(settings, expense_id)
    report_no = None
    if exp.get("code") == 0:
        report_no = extract_cf_expense_report(settings, (exp.get("expense") or {}))
    report_no = (report_no or f"EXP{expense_id}").strip()

    # Upload attachments (non-overwriting) to Zoho
    atts = store.list_attachments(pending_id)
    uploaded = []
    idx = 1
    for a in atts:
        path = a["stored_path"]
        if not os.path.exists(path):
            continue
        # keep extension from stored name
        _, ext = os.path.splitext(a["stored_name"])
        ts = int(time.time())
        filename = f"{report_no}_{ts}_{idx}{ext or ''}"
        with open(path, "rb") as f:
            z = zoho_add_expense_attachment(settings, expense_id, filename, f, None)
        uploaded.append({"local_attachment_id": a["id"], "filename": filename, "zoho": z})
        idx += 1

    store.mark_posted(pending_id, expense_id)

    return {"ok": True, "zoho_expense_id": expense_id, "report_no": report_no, "uploaded": uploaded}
