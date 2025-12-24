from __future__ import annotations

import mimetypes
import os
import time

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..core.auth import get_current_user, CurrentUser
from ..core.config import settings
from ..core.zoho import zoho
from ..services.pending_store import pending_store

router = APIRouter()


@router.post("/upload/{expense_id}")
async def upload_receipt(
    expense_id: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    exp = pending_store.get(expense_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Expense not found")

    # 🔐 Permission rules
    # - Admin: always allowed
    # - User: only own PENDING expenses
    if not user.is_admin:
        if exp.get("status") != "pending":
            raise HTTPException(
                status_code=403,
                detail="Only pending expenses can be modified",
            )

        # ✅ FIX: use user.user_id (matches expense creation)
        if exp.get("created_by") != user.user_id:
            raise HTTPException(
                status_code=403,
                detail="Not your expense",
            )

    os.makedirs(settings.uploads_dir, exist_ok=True)
    safe_name = (file.filename or "receipt").replace("\\", "_").replace("/", "_")
    folder = os.path.join(settings.uploads_dir, str(expense_id))
    os.makedirs(folder, exist_ok=True)

    ts = int(time.time())
    stored_name = f"{ts}_{safe_name}"
    path = os.path.join(folder, stored_name)

    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)

    # Served via /uploads mount
    url = f"/uploads/{expense_id}/{stored_name}"
    updated = pending_store.add_receipt(
        expense_id,
        filename=stored_name,
        url=url,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to attach receipt")

    # If already approved & posted in Zoho, push attachment immediately
    zoho_expense_id = (
        (updated or {}).get("zoho_expense_id")
        or (exp or {}).get("zoho_expense_id")
    )
    status = (updated or {}).get("status") or (exp or {}).get("status")

    if status == "approved" and zoho_expense_id:
        try:
            token = await zoho.get_access_token()
            params = {"organization_id": zoho.org_id} if zoho.org_id else {}

            attach_url = (
                f"{zoho.books_base_url.rstrip('/')}"
                f"/expenses/{str(zoho_expense_id).strip()}/attachment"
            )
            headers = {"Authorization": f"Zoho-oauthtoken {token}"}

            ctype = mimetypes.guess_type(stored_name)[0] or "application/octet-stream"
            files = {"attachment": (stored_name, content, ctype)}

            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    attach_url,
                    headers=headers,
                    params=params,
                    files=files,
                )
                r.raise_for_status()

            pending_store.update_fields(expense_id, {
                "zoho_attachment_posted": True,
                "zoho_attachment_error": "",
            })

        except Exception as e:
            pending_store.update_fields(expense_id, {
                "zoho_attachment_posted": False,
                "zoho_attachment_error": str(e),
            })

    return {"ok": True, "expense": updated}
