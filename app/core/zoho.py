import time
import requests
from app.core.config import Settings


class ZohoClient:
    """
    Wraps Zoho OAuth refresh-token flow + request helper.
    Preserves the original behavior:
      - in-memory token cache
      - organization_id is always injected into query params
    """

    def __init__(self, *, settings: Settings):
        self.settings = settings
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

    def get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        resp = requests.post(
            self.settings.ZOHO_AUTH_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.settings.ZOHO_CLIENT_ID,
                "client_secret": self.settings.ZOHO_CLIENT_SECRET,
                "refresh_token": self.settings.ZOHO_REFRESH_TOKEN,
            },
            timeout=20,
        )
        data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"Failed to refresh Zoho token: {data}")

        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600)) - 60
        return self._access_token

    def headers(self, extra: dict | None = None) -> dict:
        token = self.get_access_token()
        h = {"Authorization": f"Zoho-oauthtoken {token}"}
        if extra:
            h.update(extra)
        return h

    def request(
        self,
        method: str,
        path: str,
        *,
        params=None,
        json=None,
        files=None,
        headers=None,
        timeout=30,
    ) -> requests.Response:
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.settings.ZOHO_BASE}{path}"

        p = params.copy() if isinstance(params, dict) else {}
        p["organization_id"] = self.settings.ZOHO_ORG_ID

        h = self.headers(headers or {})

        return requests.request(
            method=method.upper(),
            url=url,
            params=p,
            json=json,
            files=files,
            headers=h,
            timeout=timeout,
        )
