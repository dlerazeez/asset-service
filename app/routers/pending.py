from __future__ import annotations

import os
import mimetypes
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.auth import get_current_user, require_admin, CurrentUser
from ..core.config import settings
from ..core.zoho import zoho_json, zoho
from ..services.pending_store import pending_store
from ..services.coa_store import coa_store

router = APIRouter()


class ApprovePayload(BaseModel):
    expense_id: str


class RejectPayload(BaseModel):
    expense_id: str


class AdminUpdatePayload(BaseModel):
    date: Optional[str] = None
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    amount: Optional[float] = None
    reference_number: Optional[str] = None
    description: Optional[str] = None
    paid_through_account_id: Optional[str] = None
    paid_through_account_name: Optional[str] = None


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


@router.patch("/expenses/{expense_id}/admin_update")
def admin_update(expense_id: str, patch: AdminUpdatePayload, _: CurrentUser = Depends(require_admin)):
    exp = pending_store.get(expense_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Expense not found")

    updated = pending_store.update_fields(expense_id, patch.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=400, detail="Update failed")
    return {"ok": True, "expense": updated}


def _build_zoho_expense_payload(rec: dict) -> dict:
    """
    Build payload for Zoho /expenses.
    IMPORTANT: use the edited record fields first (not stale payload).
    """
    vendor_id = rec.get("vendor_id")
    vendor_name = rec.get("vendor_name")

    zoho_payload = {
        "date": rec.get("date") or None,
        "account_id": rec.get("expense_account_id") or None,
        "paid_through_account_id": rec.get("paid_through_account_id") or None,
        "amount": rec.get("amount"),
        "reference_number": rec.get("reference_number") or None,
        "description": rec.get("description") or "",
    }

    if vendor_id:
        zoho_payload["vendor_id"] = vendor_id
    elif vendor_name:
        zoho_payload["vendor_name"] = vendor_name

    return {k: v for k, v in zoho_payload.items() if v is not None and v != ""}


async def _push_journal_attachments(journal_id: str, expense_id: str, receipts: list):
    if not journal_id or not receipts:
        return

    token = await zoho.get_access_token()
    params = {"organization_id": zoho.org_id} if zoho.org_id else {}
    url = f"{zoho.books_base_url.rstrip('/')}/journals/{str(journal_id).strip()}/attachment"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    errors = []
    for r in receipts:
        filename = (r or {}).get("filename") or ""
        if not filename:
            continue

        local_path = os.path.join(settings.uploads_dir, str(expense_id), filename)
        if not os.path.exists(local_path):
            errors.append(f"Missing local file: {filename}")
            continue

        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            with open(local_path, "rb") as f:
                files = {"attachment": (filename, f, ctype)}
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(url, headers=headers, params=params, files=files)
                    resp.raise_for_status()
        except Exception as e:
            errors.append(f"{filename}: {str(e)}")

    if errors:
        pending_store.update_fields(expense_id, {
            "zoho_attachment_posted": False,
            "zoho_attachment_error": "; ".join(errors),
        })
    else:
        pending_store.update_fields(expense_id, {
            "zoho_attachment_posted": True,
            "zoho_attachment_error": "",
        })


@router.post("/expenses/approve")
async def approve(payload: ApprovePayload, _: CurrentUser = Depends(require_admin)):
    expense_id = str(payload.expense_id or "").strip()
    if not expense_id:
        raise HTTPException(status_code=400, detail="expense_id is required")

    rec = pending_store.get(expense_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Pending expense not found")

    if rec.get("status") != "pending":
        return {"ok": True, "status": rec.get("status"), "expense": rec}

    pending_kind = (rec.get("pending_kind") or "expense").strip().lower()

    # -------------------------------------------------------
    # A) Normal expenses -> Zoho /expenses
    # -------------------------------------------------------
    if pending_kind == "expense":
        try:
            zoho_payload = _build_zoho_expense_payload(rec)
            zoho_resp = await zoho_json("POST", "/expenses", json=zoho_payload)
        except Exception as e:
            pending_store.update_fields(expense_id, {
                "zoho_posted": False,
                "zoho_error": str(e),
            })
            raise HTTPException(status_code=502, detail=f"Zoho post failed: {str(e)}")

        zoho_expense_id = (zoho_resp.get("expense") or {}).get("expense_id") or zoho_resp.get("expense_id")
        ok = pending_store.approve(expense_id, zoho_response=zoho_resp)
        if not ok:
            raise HTTPException(status_code=500, detail="Unable to mark approved")

        updated = pending_store.update_fields(expense_id, {
            "zoho_posted": True,
            "zoho_expense_id": zoho_expense_id,
            "zoho_response": zoho_resp,
            "zoho_error": "",
        })

        # push attachments after approval (keep your existing expense attachment logic)
        return {"ok": True, "expense": updated, "zoho_expense_id": zoho_expense_id}

    # -------------------------------------------------------
    # B) Payments Made (clearing) -> Zoho /journals
    # -------------------------------------------------------
    if pending_kind == "accrued_payment":
        source_id = (rec.get("source_accrued_expense_id") or rec.get("source_expense_id") or "").strip()
        src = pending_store.get(source_id) if source_id else None

        if (not src) or (src.get("status") != "approved") or ((src.get("expense_type") or "").lower() != "accrued"):
            raise HTTPException(status_code=400, detail="Invalid source accrued expense for clearing payment")

        accrued_row = coa_store.accrued_paid_through_account()
        if not accrued_row:
            raise HTTPException(status_code=400, detail="Accrued Expenses account not found in COA CSV")

        accrued_account_id = (
            accrued_row.get("Account ID")
            or accrued_row.get("Account Id")
            or accrued_row.get("account_id")
            or ""
        ).strip()

        cash_account_id = str(rec.get("paid_through_account_id") or "").strip()
        if not cash_account_id:
            raise HTTPException(status_code=400, detail="Paid through account is required")

        amt = float(rec.get("amount") or 0)
        if amt <= 0:
            raise HTTPException(status_code=400, detail="Amount must be > 0")

        vendor_id = rec.get("vendor_id")

        journal_payload = {
            "journal_date": rec.get("date") or None,
            "reference_number": rec.get("reference_number") or None,
            "notes": rec.get("description") or f"Accrued clearing payment for {source_id}",
            "line_items": [
                {
                    "account_id": accrued_account_id,
                    "debit_or_credit": "debit",
                    "amount": amt,
                    **({"customer_id": str(vendor_id)} if vendor_id else {}),
                },
                {
                    "account_id": cash_account_id,
                    "debit_or_credit": "credit",
                    "amount": amt,
                    **({"customer_id": str(vendor_id)} if vendor_id else {}),
                },
            ],
        }

        try:
            zoho_resp = await zoho_json("POST", "/journals", json=journal_payload)
        except Exception as e:
            pending_store.update_fields(expense_id, {
                "zoho_posted": False,
                "zoho_error": str(e),
            })
            raise HTTPException(status_code=502, detail=f"Zoho journal create failed: {str(e)}")

        journal_id = ((zoho_resp.get("journal") or {}).get("journal_id") or "").strip()

        ok = pending_store.approve(expense_id, zoho_response=zoho_resp)
        if not ok:
            raise HTTPException(status_code=500, detail="Unable to mark approved")

        updated = pending_store.update_fields(expense_id, {
            "zoho_posted": True,
            "zoho_journal_id": journal_id,
            "zoho_response": zoho_resp,
            "zoho_error": "",
        })

        pending_store.clear_accrued(
            source_id,
            amount=amt,
            paid_through_account_id=cash_account_id,
            paid_through_account_name=rec.get("paid_through_account_name") or "",
            clearing_date=rec.get("date") or "",
            reference_number=rec.get("reference_number") or "",
            source_payment_id=expense_id,
            receipts=(rec.get("receipts") or []),
        )

        receipts = (updated or {}).get("receipts") or []
        if journal_id and receipts:
            await _push_journal_attachments(journal_id, expense_id, receipts)

        return {"ok": True, "expense": updated, "zoho_journal_id": journal_id}

    raise HTTPException(status_code=400, detail="Unknown pending_kind")


@router.post("/expenses/reject")
def reject(payload: RejectPayload, _: CurrentUser = Depends(require_admin)):
    expense_id = str(payload.expense_id or "").strip()
    if not expense_id:
        raise HTTPException(status_code=400, detail="expense_id is required")

    ok = pending_store.reject(expense_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Pending expense not found")

    return {"ok": True}
