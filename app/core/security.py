from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
except Exception:  # pragma: no cover
    PasswordHasher = None  # type: ignore[assignment]
    VerifyMismatchError = Exception  # type: ignore[misc]

# If argon2-cffi is installed, this will be used for hashing new passwords too.
_ARGON2: Optional["PasswordHasher"] = PasswordHasher() if PasswordHasher else None


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("utf-8"))


def hash_password(password: str, *, iterations: int = 200_000) -> str:
    """
    Prefer Argon2 if available (matches your existing stored hashes).
    Fallback to PBKDF2-SHA256 if argon2-cffi isn't installed.
    """
    if _ARGON2:
        return _ARGON2.hash(password)

    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False

    # Existing hashes in your users.json are Argon2:
    if hashed.startswith("$argon2"):
        if not _ARGON2:
            # argon2-cffi not installed => cannot verify these hashes
            return False
        try:
            return _ARGON2.verify(hashed, password)
        except VerifyMismatchError:
            return False
        except Exception:
            return False

    # PBKDF2 fallback
    if hashed.startswith("pbkdf2_sha256$"):
        try:
            algo, it_s, salt_s, dk_s = hashed.split("$", 3)
            if algo != "pbkdf2_sha256":
                return False
            iterations = int(it_s)
            salt = _b64d(salt_s)
            dk_expected = _b64d(dk_s)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(dk, dk_expected)
        except Exception:
            return False

    return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
