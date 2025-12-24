import os


def guess_extension(filename: str | None, content_type: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext:
        return ext
    if content_type:
        ct = content_type.lower()
        if "pdf" in ct:
            return ".pdf"
        if "png" in ct:
            return ".png"
        if "jpeg" in ct or "jpg" in ct:
            return ".jpg"
    return ".bin"

from typing import Any, Dict


def ensure_ok_zoho(resp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates a Zoho API JSON response.

    Returns the response if OK.
    Raises RuntimeError if Zoho indicates an error.
    """
    if not isinstance(resp, dict):
        raise RuntimeError(f"Invalid Zoho response: {resp}")

    # Common Zoho error patterns
    if resp.get("code") not in (None, 0) and resp.get("status") == "error":
        raise RuntimeError(
            f"Zoho API error: {resp.get('message') or resp}"
        )

    if resp.get("error"):
        raise RuntimeError(f"Zoho API error: {resp['error']}")

    return resp
