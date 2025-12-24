from __future__ import annotations

from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Depends, Query

from app.core.auth import get_current_user, CurrentUser
from app.core.zoho import zoho_request
from app.services.pending_store import pending_store

router = APIRouter(prefix="/cash", tags=["cash"])


# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------

def _safe_float(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _round2(v: float) -> float:
    return round(float(v) + 1e-12, 2)


# -------------------------------------------------------------------
# GENERAL CASH DASHBOARD
# -------------------------------------------------------------------

@router.get("")
async def get_cash_dashboard(user: CurrentUser = Depends(get_current_user)):
    resp = await zoho_request("GET", "/bankaccounts")
    accounts = resp.get("bankaccounts", [])

    # Restrict non-admins
    if not user.is_admin:
        allowed = set(user.allowed_cash_accounts or [])
        accounts = [
            a for a in accounts
            if str(a.get("account_id")) in allowed
        ]

    cashboxes = []

    for acct in accounts:
        account_id = str(acct.get("account_id"))
        balance = _safe_float(acct.get("balance"))
        pending = pending_store.pending_total_for_account(account_id)

        cashboxes.append({
            "account_id": account_id,
            "account_name": acct.get("account_name"),
            "zoho_cash_after_approved": _round2(balance),
            "pending_not_approved_total": _round2(pending),
            "cash_before_approval": _round2(balance - pending),
        })

    return {"cashboxes": cashboxes}


# -------------------------------------------------------------------
# PARAMETERIZED WINGS CASH (OPTION C)
# -------------------------------------------------------------------

@router.get("/wings")
async def get_wings_cash(
    account_id: str = Query(..., description="Paid Through Account ID"),
    user: CurrentUser = Depends(get_current_user),
):
    # Permission check
    if not user.is_admin:
        if account_id not in (user.allowed_cash_accounts or []):
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this cash account",
            )

    resp = await zoho_request("GET", "/bankaccounts")
    accounts = resp.get("bankaccounts", [])

    acct = next(
        (a for a in accounts if str(a.get("account_id")) == str(account_id)),
        None,
    )

    if not acct:
        raise HTTPException(status_code=404, detail="Cash account not found")

    balance = _safe_float(acct.get("balance"))
    pending = pending_store.pending_total_for_account(account_id)

    return {
        "account_id": account_id,
        "account_name": acct.get("account_name"),
        "zoho_cash_after_approved": _round2(balance),
        "pending_not_approved_total": _round2(pending),
        "cash_before_approval": _round2(balance - pending),
    }
