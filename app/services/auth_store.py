from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from ..core.config import settings
from ..core.security import hash_password, verify_password, new_session_token


class AuthStore:
    """
    File-backed auth store:
    - users.json: users
    - invites.json: invite tokens
    - sessions.json: active sessions
    """

    def __init__(self) -> None:
        os.makedirs(settings.data_dir, exist_ok=True)
        self.users_path = os.path.join(settings.data_dir, "users.json")
        self.invites_path = os.path.join(settings.data_dir, "invites.json")
        self.sessions_path = os.path.join(settings.data_dir, "sessions.json")

        self._lock = threading.Lock()
        self._loaded = False

        self._users: Dict[str, Dict[str, Any]] = {}
        self._invites: Dict[str, Dict[str, Any]] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}

        self._load()
        self._ensure_default_admin()

    # -----------------------
    # Internal helpers
    # -----------------------

    def _load_json(self, path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _save_json(self, path: str, data: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        with self._lock:
            self._users = self._load_json(self.users_path)
            self._invites = self._load_json(self.invites_path)
            self._sessions = self._load_json(self.sessions_path)

    def _save_all(self) -> None:
        self._save_json(self.users_path, self._users)
        self._save_json(self.invites_path, self._invites)
        self._save_json(self.sessions_path, self._sessions)

    def _ensure_default_admin(self) -> None:
        with self._lock:
            if self._users:
                return

            email = f"admin@{settings.company_email_domain}".lower()
            user_id = secrets.token_hex(12)

            self._users[user_id] = {
                "user_id": user_id,
                "email": email,
                "role": "admin",
                "password_hash": hash_password("Admin123!"),
                "allowed_cash_accounts": [],
                "created_at": int(time.time()),
                "active": True,
            }

            self._save_all()

    def _validate_company_email(self, email: str) -> None:
        e = (email or "").strip().lower()
        if "@" not in e:
            raise ValueError("Invalid email")
        domain = e.split("@", 1)[1]
        if domain != settings.company_email_domain:
            raise ValueError(f"Email must be @{settings.company_email_domain}")

    # -----------------------
    # Public API
    # -----------------------

    def login(self, email: str, password: str) -> Optional[str]:
        self._load()
        e = (email or "").strip().lower()

        with self._lock:
            user = next(
                (
                    u for u in self._users.values()
                    if u.get("email") == e and u.get("active") is True
                ),
                None,
            )
            if not user:
                return None

            if not verify_password(password, user.get("password_hash", "")):
                return None

            token = new_session_token()
            self._sessions[token] = {
                "token": token,
                "user_id": user["user_id"],
                "created_at": int(time.time()),
            }

            self._save_all()
            return token

    def get_user_by_session(self, token: str) -> Optional[Dict[str, Any]]:
        self._load()
        with self._lock:
            s = self._sessions.get(token)
            if not s:
                return None

            u = self._users.get(s.get("user_id"))
            if not u or not u.get("active"):
                return None

            return u

    def list_users(self) -> List[Dict[str, Any]]:
        self._load()
        with self._lock:
            return sorted(
                self._users.values(),
                key=lambda x: x.get("email", ""),
            )

    def invite_user(
        self,
        email: str,
        role: str,
        allowed_cash_accounts: List[str],
    ) -> str:
        self._load()
        e = (email or "").strip().lower()
        self._validate_company_email(e)

        r = (role or "user").strip().lower()
        if r not in ("admin", "user"):
            r = "user"

        token = secrets.token_urlsafe(24)

        with self._lock:
            self._invites[token] = {
                "invite_token": token,
                "email": e,
                "role": r,
                "allowed_cash_accounts": list(set(allowed_cash_accounts or [])),
                "created_at": int(time.time()),
                "used": False,
            }
            self._save_all()

        return token

    def accept_invite(
        self,
        invite_token: str,
        password: str,
    ) -> Optional[Dict[str, Any]]:
        self._load()
        tok = (invite_token or "").strip()

        if not tok or len(password or "") < 8:
            return None

        with self._lock:
            inv = self._invites.get(tok)
            if not inv or inv.get("used"):
                return None

            user_id = secrets.token_hex(12)

            self._users[user_id] = {
                "user_id": user_id,
                "email": inv["email"],
                "role": inv.get("role", "user"),
                "password_hash": hash_password(password),
                "allowed_cash_accounts": inv.get("allowed_cash_accounts", []),
                "created_at": int(time.time()),
                "active": True,
            }

            inv["used"] = True
            inv["used_at"] = int(time.time())
            self._save_all()

            return self._users[user_id]

    def update_role(self, user_id: str, role: str) -> bool:
        self._load()
        r = (role or "").strip().lower()
        if r not in ("admin", "user"):
            return False

        with self._lock:
            u = self._users.get(user_id)
            if not u:
                return False

            u["role"] = r
            self._save_all()
            return True

    def update_cash_access(
        self,
        user_id: str,
        accounts: List[str],
    ) -> bool:
        self._load()
        with self._lock:
            u = self._users.get(user_id)
            if not u:
                return False

            u["allowed_cash_accounts"] = list(set(accounts or []))
            self._save_all()
            return True

    # -----------------------
    # NEW: Enable / Disable
    # -----------------------

    def set_active(self, user_id: str, active: bool) -> bool:
        self._load()
        with self._lock:
            u = self._users.get(user_id)
            if not u:
                return False

            u["active"] = bool(active)
            self._save_all()
            return True

    # -----------------------
    # NEW: Admin password reset
    # -----------------------

    def set_password(self, user_id: str, password: str) -> bool:
        if not password or len(password) < 8:
            return False

        self._load()
        with self._lock:
            u = self._users.get(user_id)
            if not u:
                return False

            u["password_hash"] = hash_password(password)
            self._save_all()
            return True


auth_store = AuthStore()
