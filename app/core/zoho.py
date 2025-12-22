import time
import requests
from typing import Any

_access_token = None
_token_expiry = 0


def zoho_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text, "status_code": resp.status_code}


def get_access_token(settings) -> str:
    global _access_token, _token_expiry

    if _access_token and time.time() < _token_expiry:
        return _access_token

    resp = requests.post(
        settings.zoho_auth_url,
        data={
            "grant_type": "refresh_token",
            "client_id": settings.zoho_client_id,
            "client_secret": settings.zoho_client_secret,
            "refresh_token": settings.zoho_refresh_token,
        },
        timeout=20,
    )
    data = zoho_json(resp)

    if "access_token" not in data:
        raise RuntimeError(f"Failed to refresh Zoho token: {data}")

    _access_token = data["access_token"]
    _token_expiry = time.time() + int(data.get("expires_in", 3600)) - 60
    return _access_token


def zoho_headers(settings, extra: dict | None = None) -> dict:
    token = get_access_token(settings)
    h = {"Authorization": f"Zoho-oauthtoken {token}"}
    if extra:
        h.update(extra)
    return h


def zoho_request(settings, method: str, path: str, *, params=None, json=None, files=None, headers=None, timeout=30):
    if not path.startswith("/"):
        path = "/" + path
    url = f"{settings.zoho_base}{path}"

    p = params.copy() if isinstance(params, dict) else {}
    p["organization_id"] = settings.zoho_org_id

    h = zoho_headers(settings, headers or {})

    return requests.request(
        method=method.upper(),
        url=url,
        params=p,
        json=json,
        files=files,
        headers=h,
        timeout=timeout,
    )


def extract_cf_expense_report(settings, expense_obj: dict) -> str | None:
    api_name = settings.expense_cf_api_name

    if not expense_obj or not isinstance(expense_obj, dict):
        return None

    cfh = expense_obj.get("custom_field_hash")
    if isinstance(cfh, dict):
        v = cfh.get(api_name)
        if isinstance(v, str) and v.strip():
            return v.strip()

    cfs = expense_obj.get("custom_fields")
    if isinstance(cfs, list):
        for item in cfs:
            if not isinstance(item, dict):
                continue
            if item.get("api_name") == api_name:
                val = item.get("value")
                if isinstance(val, str) and val.strip():
                    return val.strip()

    return None


def zoho_create_expense(settings, payload: dict) -> dict:
    resp = zoho_request(
        settings,
        "POST",
        "/expenses",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    return zoho_json(resp)


def zoho_update_expense(settings, expense_id: str, payload: dict) -> dict:
    resp = zoho_request(
        settings,
        "PUT",
        f"/expenses/{expense_id}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    return zoho_json(resp)


def zoho_get_expense(settings, expense_id: str) -> dict:
    resp = zoho_request(settings, "GET", f"/expenses/{expense_id}", timeout=30)
    return zoho_json(resp)


def zoho_add_expense_attachment(settings, expense_id: str, filename: str, fileobj, content_type: str | None) -> dict:
    # Zoho “Add attachment to an expense” uses multipart with "attachment"
    files = {"attachment": (filename, fileobj, content_type or "application/octet-stream")}
    resp = zoho_request(settings, "POST", f"/expenses/{expense_id}/attachment", files=files, timeout=90)
    return zoho_json(resp)


def zoho_add_expense_receipt(settings, expense_id: str, filename: str, fileobj, content_type: str | None) -> dict:
    # Single receipt (may overwrite). Keep for “receipt open” compatibility.
    files = {"receipt": (filename, fileobj, content_type or "application/octet-stream")}
    resp = zoho_request(settings, "POST", f"/expenses/{expense_id}/receipt", files=files, timeout=90)
    return zoho_json(resp)


def zoho_list_expenses(
    settings,
    *,
    page: int = 1,
    per_page: int = 200,
    filter_by: str = "Status.All",
    search_text: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    params = {"page": page, "per_page": per_page, "filter_by": filter_by}
    if search_text:
        params["search_text"] = search_text

    # Optional date filters (safe to pass; Zoho may ignore if not supported on this endpoint)
    if date_from:
        params["date_start"] = date_from
    if date_to:
        params["date_end"] = date_to

    resp = zoho_request(settings, "GET", "/expenses", params=params, timeout=30)
    return zoho_json(resp)


def zoho_delete_expense(settings, expense_id: str) -> dict:
    resp = zoho_request(settings, "DELETE", f"/expenses/{expense_id}", timeout=30)
    return zoho_json(resp)

def zoho_list_expense_attachments(settings, expense_id: str) -> dict:
    """Return list of attachments on an expense.

    Calls zoho_get_expense() and extracts attachments/documents/files list.
    If the API response is not successful, returns an error dict.
    """
    data = zoho_get_expense(settings, expense_id)
    # Ensure we received a valid dict with code 0
    if not isinstance(data, dict) or data.get("code") != 0:
        return data if isinstance(data, dict) else {"code": -1, "message": "Invalid response"}

    exp = data.get("expense") or {}
    attachments = (
        exp.get("attachments")
        or exp.get("documents")
        or exp.get("files")
        or []
    )
    if not isinstance(attachments, list):
        attachments = []
    return {"code": 0, "expense_id": expense_id, "attachments": attachments}

