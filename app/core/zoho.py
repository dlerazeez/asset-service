from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.core.utils import ensure_ok_zoho


class ZohoClient:
    def __init__(self) -> None:
        self.client_id = (settings.zoho_client_id or "").strip()
        self.client_secret = (settings.zoho_client_secret or "").strip()
        self.refresh_token = (settings.zoho_refresh_token or "").strip()
        self.org_id = (settings.zoho_org_id or "").strip()
        self.dc = (settings.zoho_dc or "com").strip()
        self.books_base_url = (settings.zoho_books_base_url or "").strip()

        self._access_token: Optional[str] = None
        self._access_token_expiry: float = 0.0

    # ---------------------------------------------------------
    # OAuth
    # ---------------------------------------------------------

    def _accounts_url(self) -> str:
        return {
            "com": "https://accounts.zoho.com",
            "eu": "https://accounts.zoho.eu",
            "in": "https://accounts.zoho.in",
            "au": "https://accounts.zoho.com.au",
            "ca": "https://accounts.zohocloud.ca",
            "jp": "https://accounts.zoho.jp",
            "sa": "https://accounts.zoho.sa",
        }.get(self.dc, "https://accounts.zoho.com")

    async def _refresh_access_token(self) -> str:
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise HTTPException(status_code=500, detail="Zoho OAuth settings missing")

        url = f"{self._accounts_url()}/oauth/v2/token"
        data = {
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, data=data)

        payload = r.json()
        if r.status_code != 200 or "access_token" not in payload:
            raise HTTPException(status_code=502, detail=payload)

        self._access_token = payload["access_token"]
        self._access_token_expiry = time.time() + int(payload.get("expires_in", 3600)) - 60
        return self._access_token

    async def get_access_token(self) -> str:
        if self._access_token and time.time() < self._access_token_expiry:
            return self._access_token
        return await self._refresh_access_token()

    # ---------------------------------------------------------
    # Core request
    # ---------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        token = await self.get_access_token()
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}

        params = params or {}
        params["organization_id"] = self.org_id

        url = f"{self.books_base_url.rstrip('/')}/{path.lstrip('/')}"

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                data=data,
                files=files,
            )
            r.raise_for_status()
            return r.json()


zoho = ZohoClient()


# -------------------------------------------------------------------
# BACKWARD-COMPATIBLE HELPERS (USED BY ROUTERS)
# -------------------------------------------------------------------

async def zoho_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return await zoho.request(
        method,
        path,
        params=params,
        json=json,
        data=data,
        files=files,
    )


async def zoho_json(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resp = await zoho_request(
        method,
        path,
        params=params,
        json=json,
        data=data,
    )
    return ensure_ok_zoho(resp)
