from fastapi import APIRouter
from app.core.zoho import zoho

router = APIRouter(prefix="/api/expenses")

@router.get("")
def list_expenses():
    return {"code":0,"expenses":zoho.list_expenses()}

@router.post("")
def create_expense(payload: dict):
    return {"code":0,"expense":zoho.create_expense(payload)}

@router.get("/{expense_id}")
def get_expense(expense_id: str):
    return {"code":0,"expense":zoho.get_expense(expense_id)}

@router.post("/approve/{expense_id}")
def approve(expense_id: str):
    zoho.approve_expense(expense_id)
    return {"code":0}

@router.delete("/{expense_id}")
def delete(expense_id: str):
    zoho.delete_expense(expense_id)
    return {"code":0}
