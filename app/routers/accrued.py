from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.auth import get_current_user, require_admin, CurrentUser
from ..services.coa_store import coa_store
from ..services.pending_store import pending_store

router = APIRouter()


class ClearingPayload(BaseModel):
    paid_through_account_id: str
    paid_through_account_name: str
    amount: float
    date: str | None = None  # YYYY-MM-DD


@router.get("/expenses")
def list_accrued(
    include_cleared: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    items = pending_store.list_accrued(include_cleared=include_cleared)

    # 🔐 Restrict non-admin users
    if not user.is_admin:
        allowed = set(user.allowed_cash_accounts or [])
        items = [
            e for e in items
            if (
                e.get("created_by") == user.user_id
                or e.get("paid_through_account_id") in allowed
            )
        ]

    return {"accrued": items}

    # ✅ FIX: restrict non-admin users to their allowed cash accounts
    if not user.is_admin:
        allowed = set(user.allowed_cash_accounts or [])
        items = [
            a for a in items
            if a.get("paid_through_account_id") in allowed
        ]

    return {"accrued": items}


@router.post("/{expense_id}/clear")
def clear_accrued(
    expense_id: str,
    payload: ClearingPayload,
    _: CurrentUser = Depends(require_admin),
):
    updated = pending_store.add_clearing(
        expense_id,
        amount=payload.amount,
        paid_through_account_id=payload.paid_through_account_id,
        paid_through_account_name=payload.paid_through_account_name,
        clearing_date=payload.date,
    )
    if not updated:
        raise HTTPException(
            status_code=400,
            detail="Unable to clear accrued expense (check id/type/status/amount)",
        )
    return {"ok": True, "expense": updated}
