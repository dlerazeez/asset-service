from __future__ import annotations

from datetime import date
import inspect
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.auth import get_current_user, require_admin, CurrentUser
from ..services.coa_store import coa_store
from ..services.pending_store import pending_store

router = APIRouter()


class ClearingPayload(BaseModel):
    paid_through_account_id: str
    amount: float
    date: str | None = None  # YYYY-MM-DD
    reference_number: str | None = None
    description: str | None = None


def _load_accrued_expense(expense_id: str) -> dict | None:
    """
    Best-effort loader. Prefer a direct getter if your PendingStore exposes one;
    otherwise fallback to scanning list_accrued(include_cleared=True).
    """
    for getter_name in ("get_accrued", "get_accrued_expense", "find_accrued"):
        getter = getattr(pending_store, getter_name, None)
        if callable(getter):
            try:
                return getter(expense_id)
            except TypeError:
                # In case a getter expects different args, ignore and fallback.
                pass

    # Fallback: scan list
    try:
        items = pending_store.list_accrued(include_cleared=True)
    except TypeError:
        items = pending_store.list_accrued(True)

    for e in items or []:
        if str(e.get("id") or e.get("expense_id") or "") == str(expense_id):
            return e
    return None


def _compute_balance(expense: dict) -> float | None:
    """
    Extract remaining balance from common keys; fallback to computing from amount - cleared.
    If balance cannot be determined reliably, return None.
    """
    for key in ("balance", "remaining_balance", "remaining", "open_balance"):
        if key in expense and expense[key] is not None:
            try:
                return float(expense[key])
            except (TypeError, ValueError):
                return None

    amount = expense.get("amount")
    if amount is None:
        return None
    try:
        amount_f = float(amount)
    except (TypeError, ValueError):
        return None

    # Common patterns for cleared totals
    for cleared_key in ("cleared_total", "cleared_amount", "paid_total", "paid_amount"):
        if cleared_key in expense and expense[cleared_key] is not None:
            try:
                return amount_f - float(expense[cleared_key])
            except (TypeError, ValueError):
                return None

    # If there is a list of clearings/payments
    for list_key in ("clearings", "payments", "clearing_payments"):
        if isinstance(expense.get(list_key), list):
            try:
                cleared_sum = sum(float(x.get("amount", 0) or 0) for x in expense[list_key])
                return amount_f - cleared_sum
            except (TypeError, ValueError):
                return None

    return None


@router.get("/expenses")
def list_accrued(include_cleared: bool = False):
    return {"accrued": pending_store.list_accrued(include_cleared=include_cleared)}


@router.post("/{expense_id}/clear")
def clear_accrued(expense_id: str, payload: ClearingPayload, _: CurrentUser = Depends(require_admin)):
    updated = pending_store.add_clearing(
        expense_id,
        amount=payload.amount,
        paid_through_account_id=payload.paid_through_account_id,
        paid_through_account_name=payload.paid_through_account_name,
        clearing_date=payload.date,
    )
    if not updated:
        raise HTTPException(status_code=400, detail="Unable to clear accrued expense (check id/type/status/amount)")
    return {"ok": True, "expense": updated}
