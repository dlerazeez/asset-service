from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.auth import get_current_user, require_admin, CurrentUser
from ..services.auth_store import auth_store

router = APIRouter()


# -----------------------
# Schemas
# -----------------------

class LoginPayload(BaseModel):
    email: str
    password: str


class InvitePayload(BaseModel):
    email: str
    role: str = Field(default="user")
    allowed_cash_accounts: List[str] = Field(default_factory=list)


class AcceptInvitePayload(BaseModel):
    invite_token: str
    password: str


class CashAccessPayload(BaseModel):
    allowed_cash_accounts: List[str]


class RolePayload(BaseModel):
    role: str


class ActivePayload(BaseModel):
    active: bool


class PasswordPayload(BaseModel):
    password: str


# -----------------------
# Auth Routes
# -----------------------

@router.post("/login")
def login(payload: LoginPayload):
    token = auth_store.login(payload.email, payload.password)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {"token": token}


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role,
        "allowed_cash_accounts": user.allowed_cash_accounts,
    }


@router.post("/invite")
def invite(payload: InvitePayload, _: CurrentUser = Depends(require_admin)):
    try:
        token = auth_store.invite_user(
            payload.email,
            payload.role,
            payload.allowed_cash_accounts,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "invite_token": token,
        "email": payload.email,
        "role": payload.role,
        "allowed_cash_accounts": payload.allowed_cash_accounts,
    }


@router.post("/accept")
def accept(payload: AcceptInvitePayload):
    u = auth_store.accept_invite(payload.invite_token, payload.password)
    if not u:
        raise HTTPException(
            status_code=400,
            detail="Invalid invite token or password too short (min 8)",
        )

    return {
        "ok": True,
        "email": u["email"],
        "role": u["role"],
        "allowed_cash_accounts": u.get("allowed_cash_accounts", []),
    }


# -----------------------
# Admin: Users
# -----------------------

@router.get("/users")
def list_users(_: CurrentUser = Depends(require_admin)):
    return {"users": auth_store.list_users()}


@router.patch("/users/{user_id}/role")
def set_role(
    user_id: str,
    payload: RolePayload,
    _: CurrentUser = Depends(require_admin),
):
    if payload.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role")

    ok = auth_store.update_role(user_id, payload.role)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True}


@router.patch("/users/{user_id}/cash-access")
def set_cash_access(
    user_id: str,
    payload: CashAccessPayload,
    _: CurrentUser = Depends(require_admin),
):
    ok = auth_store.update_cash_access(user_id, payload.allowed_cash_accounts)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True}


@router.patch("/users/{user_id}/active")
def set_user_active(
    user_id: str,
    payload: ActivePayload,
    _: CurrentUser = Depends(require_admin),
):
    ok = auth_store.set_active(user_id, payload.active)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True, "active": payload.active}


@router.patch("/users/{user_id}/password")
def admin_set_password(
    user_id: str,
    payload: PasswordPayload,
    _: CurrentUser = Depends(require_admin),
):
    if not payload.password or len(payload.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )

    ok = auth_store.set_password(user_id, payload.password)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True}
