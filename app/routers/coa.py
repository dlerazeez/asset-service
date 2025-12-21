from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/coa/expense-accounts")
def coa_expense_accounts(request: Request):
    store = request.app.state.coa_store
    if store.load_error:
        return {"ok": False, "error": store.load_error, "accounts": []}
    accounts = store.filter({"Expense", "Other Expense"})
    return {"ok": True, "count": len(accounts), "accounts": accounts}


@router.get("/coa/paid-through-accounts")
def coa_paid_through_accounts(request: Request):
    store = request.app.state.coa_store
    if store.load_error:
        return {"ok": False, "error": store.load_error, "accounts": []}
    accounts = store.filter({"Cash", "Bank"})
    return {"ok": True, "count": len(accounts), "accounts": accounts}


@router.post("/coa/reload")
def coa_reload(request: Request):
    store = request.app.state.coa_store
    store.load()
    if store.load_error:
        raise HTTPException(400, store.load_error)
    return {"ok": True, "count": len(store.rows)}
