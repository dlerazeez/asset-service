from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .config import settings
from ..services.auth_store import auth_store


security = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(self, data: dict):
        self.user_id = data.get("user_id")
        self.email = data.get("email")
        self.role = data.get("role")

        # Derived helpers
        self.is_admin: bool = self.role == "admin"
        self.allowed_cash_accounts: list[str] = data.get(
            "allowed_cash_accounts", []
        )


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    """
    Resolve the current authenticated user from Bearer token.
    """
    if not creds or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    token = creds.credentials
    user = auth_store.get_user_by_session(token)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return CurrentUser(user)


def require_user(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Require any authenticated user.
    Exists for semantic clarity and router imports.
    """
    return user


def require_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Require admin privileges.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user
