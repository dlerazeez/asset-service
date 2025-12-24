from __future__ import annotations

import os
import mimetypes

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.auth import get_current_user, require_admin, CurrentUser
from ..core.config import settings
from ..core.zoho import zoho_json, zoho
from ..services.pending_store import pending_store

router = APIRouter()


class ApprovePayload(BaseModel):
    expense_id: str


class RejectPayload(BaseModel):
    expense_id: str


@router.get("/expenses")
def list_pending(user: CurrentUser = Depends(get_current_user)):
    # IMPORTANT: Only pending items must show here
    items = pending_store.list_pending()

    # ✅ FIX: restrict non-admin users to their allowed cash accounts
    if not user.is_admin:
        allowed = set(user.allowed_cash_accounts or [])
        items = [
            p for p in items
            if (
                p.get("created_by") == user.user_id
                or p.get("paid_through_account_id") in allowed
            )
        ]

    return {"pending": items}


def _build_zoho_expense_payload(pending: dict) -> dict:
    """
    Convert our pending record into a Zoho Books Expense payload.
    We prioritize values saved in pending['payload'] because that is the original create payload.
    """
    payload = pending.get("payload") or {}

    vendor_id = payload.get("vendor_id") or pending.get("vendor_id")
    vendor_name = payload.get("vendor_name") or pending.get("vendor_name")

    zoho_payload = {
        "date": payload.get("date") or pending.get("date") or None,
        "account_id": payload.get("expense_account_id") or pending.get("expense_account_id") or None,
        "paid_through_account_id": payload.get("paid_through_account_id") or pending.get("paid_through_account_id") or None,
        "amount": payload.get("amount") if payload.get("amount") is not None else pending.get("amount"),
        "reference_number": payload.get("reference_number") or pending.get("reference_number") or None,
        "description": payload.get("description") or pending.get("description") or "",
    }

    # Vendor: Zoho typically expects vendor_id. If not available, send vendor_name as fallback.
    if vendor_id:
        zoho_payload["vendor_id"] = vendor_id
    elif vendor_name:
        zoho_payload["vendor_name"] = vendor_name

    # Remove null keys to avoid Zoho rejecting payload
    return {k: v for k, v in zoho_payload.items() if v is not None and v != ""}


@router.post("/expenses/approve")
async def approve(payload: ApprovePayload, _: CurrentUser = Depends(require_admin)):
    expense_id = str(payload.expense_id or "").strip()
    if not expense_id:
        raise HTTPException(status_code=400, detail="expense_id is required")

    pending = pending_store.get(expense_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Pending expense not found")

    if pending.get("status") != "pending":
        # already approved/rejected; treat as idempotent
        return {"ok": True, "status": pending.get("status"), "expense": pending}

    # 1) Post to Zoho FIRST
    try:
        zoho_payload = _build_zoho_expense_payload(pending)
        zoho_resp = await zoho_json("POST", "/expenses", json=zoho_payload)
    except Exception as e:
        # Leave it pending if Zoho fails
        pending_store.update_fields(expense_id, {
            "zoho_posted": False,
            "zoho_error": str(e),
        })
        raise HTTPException(status_code=502, detail=f"Zoho post failed: {str(e)}")

    # try to extract expense_id from common Zoho shapes
    zoho_expense_id = None
    if isinstance(zoho_resp, dict):
        if isinstance(zoho_resp.get("expense"), dict):
            zoho_expense_id = zoho_resp["expense"].get("expense_id")
        if not zoho_expense_id:
            zoho_expense_id = zoho_resp.get("expense_id")

    # 2) Mark approved only after Zoho succeeded
    ok = pending_store.approve(expense_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Unable to mark approved")

    updated = pending_store.update_fields(expense_id, {
        "zoho_posted": True,
        "zoho_expense_id": zoho_expense_id,
        "zoho_response": zoho_resp,
        "zoho_error": "",
    })

    # 3) If receipts exist (uploaded while pending), push them to Zoho now
    attachment_errors = []
    if zoho_expense_id and updated:
        receipts = updated.get("receipts") or []
        try:
            token = await zoho.get_access_token()
        except Exception as e:
            attachment_errors.append(f"Zoho token error: {str(e)}")
        else:
            params = {"organization_id": zoho.org_id} if zoho.org_id else {}
            url = f"{zoho.books_base_url.rstrip('/')}/expenses/{str(zoho_expense_id).strip()}/attachment"
            headers = {"Authorization": f"Zoho-oauthtoken {token}"}

            for r in receipts:
                filename = (r or {}).get("filename") or ""
                if not filename:
                    continue

                local_path = os.path.join(settings.uploads_dir, str(expense_id), filename)
                if not os.path.exists(local_path):
                    attachment_errors.append(f"Missing local file: {filename}")
                    continue

                ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                try:
                    with open(local_path, "rb") as f:
                        files = {"attachment": (filename, f, ctype)}
                        async with httpx.AsyncClient(timeout=60) as client:
                            resp = await client.post(url, headers=headers, params=params, files=files)
                            resp.raise_for_status()
                except Exception as e:
                    attachment_errors.append(f"{filename}: {str(e)}")

        if attachment_errors:
            pending_store.update_fields(expense_id, {
                "zoho_attachment_posted": False,
                "zoho_attachment_error": "; ".join(attachment_errors),
            })
        else:
            pending_store.update_fields(expense_id, {
                "zoho_attachment_posted": True,
                "zoho_attachment_error": "",
            })

    return {"ok": True, "expense": updated, "zoho_expense_id": zoho_expense_id}


@router.post("/expenses/reject")
def reject(payload: RejectPayload, _: CurrentUser = Depends(require_admin)):
    expense_id = str(payload.expense_id or "").strip()
    if not expense_id:
        raise HTTPException(status_code=400, detail="expense_id is required")

    ok = pending_store.reject(expense_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Pending expense not found")

    return {"ok": True}
