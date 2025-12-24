from __future__ import annotations

import json
import os
import threading
import time
import inspect
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _parse_yyyy_mm_dd(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception:
        return None


def _month_bounds(today: Optional[date] = None) -> Tuple[str, str]:
    t = today or date.today()
    start = t.replace(day=1)
    # next month start
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    end = nxt  # exclusive
    return (start.isoformat(), end.isoformat())


def _json_sanitize(obj: Any) -> Any:
    """
    Ensure the structure is JSON-serializable.
    Converts unknown objects (including coroutine objects) to safe strings.
    """
    # Handle coroutines / awaitables / generators
    try:
        if inspect.iscoroutine(obj):
            return "<coroutine>"
        if inspect.isawaitable(obj):
            return "<awaitable>"
    except Exception:
        pass

    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sanitize(x) for x in obj]
    # Fallback
    return str(obj)


class PendingStore:
    """
    File-backed store for expenses:
      - status: pending | approved | rejected
      - approved expenses are still kept here (UI uses /approved and /accrued)
      - accrued clearing state is stored and affects only the Accrued view
    """
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path or not os.path.exists(self.path):
            self._data = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f) or {}
        except Exception:
            self._data = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        safe = _json_sanitize(self._data)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(safe, f, ensure_ascii=False, indent=2)

    def _next_id(self) -> str:
        return str(int(time.time() * 1000))

    # ---------------------------
    # Core CRUD
    # ---------------------------
    def add_pending(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create or overwrite a pending record. Used by /api/expenses/create.
        """
        self._load()

        expense_id = str(record.get("expense_id") or self._next_id())

        payload = record.get("payload") or record
        # Make vendor_name resilient
        vendor_name = record.get("vendor_name") or (payload.get("vendor_name") if isinstance(payload, dict) else "") or ""

        amount = _safe_float(record.get("amount"))
        exp_type = (record.get("expense_type") or "ordinary").lower().strip()

        normalized: Dict[str, Any] = {
            "expense_id": expense_id,
            "status": "pending",
            "created_at": int(time.time()),

            "date": record.get("date") or "",
            "vendor_id": record.get("vendor_id"),
            "vendor_name": vendor_name,
            "amount": amount,
            "reference_number": record.get("reference_number") or "",
            "expense_type": exp_type,

            "expense_account_id": record.get("expense_account_id") or "",
            "paid_through_account_id": record.get("paid_through_account_id") or "",
            "description": record.get("description") or "",

            "receipts": record.get("receipts") or [],

            # Zoho posting flags (set during approve)
            "zoho_posted": bool(record.get("zoho_posted", False)),
            "zoho_error": record.get("zoho_error"),
            "zoho_response": record.get("zoho_response"),

            # Accrued clearing support
            "balance": _safe_float(record.get("balance")),
            "clearing": record.get("clearing") or [],
            "cleared_at": record.get("cleared_at"),

            # Keep raw payload for later posting/debugging
            "payload": payload,
        }

        # If accrued: initialize balance if missing
        if exp_type == "accrued":
            if normalized["balance"] is None and amount is not None:
                normalized["balance"] = float(amount)

        with self._lock:
            self._data[expense_id] = normalized
            self._save()

        return normalized

    # Compatibility alias used by older routers
    def create_pending(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return self.add_pending(record)

    def get(self, expense_id: str) -> Optional[Dict[str, Any]]:
        self._load()
        with self._lock:
            return self._data.get(str(expense_id))

    def delete(self, expense_id: str) -> bool:
        """
        Hard delete (UI "Delete").
        """
        self._load()
        key = str(expense_id)
        with self._lock:
            if key not in self._data:
                return False
            del self._data[key]
            self._save()
            return True

    def update_fields(self, expense_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Partial update helper used by routers (approve flow / Zoho status).
        Must only persist JSON-serializable data.
        """
        self._load()
        key = str(expense_id)

        if not isinstance(fields, dict):
            return self.get(key)

        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return None

            safe_fields = _json_sanitize(fields)
            if isinstance(safe_fields, dict):
                rec.update(safe_fields)

            self._save()
            return rec

    def add_receipt(self, expense_id: str, *, filename: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Attach a receipt to an expense (pending/approved/accrued).
        Called by /api/receipts/upload/{expense_id}.
        """
        self._load()
        key = str(expense_id)

        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return None

            rec.setdefault("receipts", [])
            rec["receipts"].append({
                "filename": filename,
                "url": url,
                "created_at": int(time.time()),
            })

            self._save()
            return rec

    # ---------------------------
    # Listing helpers
    # ---------------------------
    def list_pending(self) -> List[Dict[str, Any]]:
        """
        Pending view must exclude approved/rejected.
        """
        self._load()
        with self._lock:
            out = [x for x in self._data.values() if (x.get("status") == "pending")]
        out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return out

    def list_all(self) -> List[Dict[str, Any]]:
        self._load()
        with self._lock:
            return list(self._data.values())

    def list_approved(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        default_current_month: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Approved view:
          - if no dates provided AND default_current_month=True -> current month only
          - otherwise date filter is optional
        """
        self._load()

        sd = (start_date or "").strip() or None
        ed = (end_date or "").strip() or None

        if default_current_month and not sd and not ed:
            sd, ed = _month_bounds()

        sd_d = _parse_yyyy_mm_dd(sd) if sd else None
        ed_d = _parse_yyyy_mm_dd(ed) if ed else None

        def in_range(d: str) -> bool:
            dd = _parse_yyyy_mm_dd(d)
            if not dd:
                return False
            if sd_d and dd < sd_d:
                return False
            if ed_d and dd >= ed_d:  # end is exclusive for month-bound behavior
                return False
            return True

        with self._lock:
            items = [x for x in self._data.values() if x.get("status") == "approved"]

        # If no filter requested at all, return all approved
        if not sd_d and not ed_d:
            items.sort(key=lambda x: x.get("approved_at", 0), reverse=True)
            return items

        filtered = [x for x in items if in_range(x.get("date") or "")]
        filtered.sort(key=lambda x: x.get("approved_at", 0), reverse=True)
        return filtered

    def list_accrued(self, *, include_cleared: bool = False) -> List[Dict[str, Any]]:
        """
        Accrued view must show approved accrued expenses.
        If include_cleared=False, hide items with balance <= 0.
        Always ignores date filters.
        """
        self._load()
        with self._lock:
            items = [
                x for x in self._data.values()
                if x.get("status") == "approved" and (x.get("expense_type") or "").lower() == "accrued"
            ]

        out: List[Dict[str, Any]] = []
        for x in items:
            bal = _safe_float(x.get("balance"))
            amt = _safe_float(x.get("amount"))
            if bal is None and amt is not None:
                bal = float(amt)
                x["balance"] = bal  # normalize in-memory

            if not include_cleared:
                if bal is not None and bal <= 0:
                    continue

            out.append(x)

        out.sort(key=lambda x: x.get("approved_at", 0), reverse=True)
        return out

    # ---------------------------
    # State transitions
    # ---------------------------
    def approve(self, expense_id: str, *, zoho_response: Optional[Dict[str, Any]] = None) -> bool:
        self._load()
        key = str(expense_id)
        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return False

            rec["status"] = "approved"
            rec["approved_at"] = int(time.time())

            # If accrued, ensure balance initialized
            if (rec.get("expense_type") or "").lower() == "accrued":
                amt = _safe_float(rec.get("amount"))
                if rec.get("balance") is None and amt is not None:
                    rec["balance"] = float(amt)

            if zoho_response is not None:
                rec["zoho_posted"] = True
                rec["zoho_error"] = None
                rec["zoho_response"] = zoho_response

            self._save()
            return True

    def reject(self, expense_id: str) -> bool:
        self._load()
        key = str(expense_id)
        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return False
            rec["status"] = "rejected"
            rec["rejected_at"] = int(time.time())
            self._save()
            return True

    # ---------------------------
    # Accrued clearing
    # ---------------------------
    def clear_accrued(
        self,
        expense_id: str,
        *,
        amount: float,
        paid_through_account_id: str,
        paid_through_account_name: Optional[str] = None,
        clearing_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Reduces balance for an approved accrued expense only.
        Does not affect approved listing; only hides from accrued view when balance <= 0 (unless include_cleared).
        """
        self._load()
        key = str(expense_id)
        amt = _safe_float(amount)
        if not amt or amt <= 0:
            return None

        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return None
            if rec.get("status") != "approved":
                return None
            if (rec.get("expense_type") or "").lower() != "accrued":
                return None

            bal = _safe_float(rec.get("balance"))
            if bal is None:
                orig = _safe_float(rec.get("amount")) or 0.0
                bal = float(orig)

            new_bal = float(bal) - float(amt)
            if new_bal < 0:
                new_bal = 0.0

            rec["balance"] = new_bal
            rec.setdefault("clearing", [])
            rec["clearing"].append({
                "amount": float(amt),
                "paid_through_account_id": paid_through_account_id,
                "paid_through_account_name": paid_through_account_name or "",
                "date": clearing_date or "",
                "created_at": int(time.time()),
            })

            if new_bal <= 0:
                rec["cleared_at"] = int(time.time())

            self._save()
            return rec

    # Compatibility alias used by accrued router(s)
    def add_clearing(
        self,
        expense_id: str,
        *,
        amount: float,
        paid_through_account_id: str,
        paid_through_account_name: Optional[str] = None,
        date: Optional[str] = None,
        clearing_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self.clear_accrued(
            expense_id,
            amount=amount,
            paid_through_account_id=paid_through_account_id,
            paid_through_account_name=paid_through_account_name,
            clearing_date=clearing_date or date,
        )

    def vendor_names(self) -> List[str]:
        self._load()
        names = set()
        with self._lock:
            for v in self._data.values():
                vn = v.get("vendor_name")
                if vn:
                    names.add(str(vn))
        return sorted(names)


pending_store = PendingStore(path=os.path.join(os.path.dirname(__file__), "pending_expenses.json"))
