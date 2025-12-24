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
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return (start.isoformat(), nxt.isoformat())


def _json_sanitize(obj: Any) -> Any:
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
    return str(obj)


class PendingStore:
    """
    File-backed store for expenses:
      - status: pending | approved | rejected
      - approved expenses are still kept here
      - accrued clearing state is stored here
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # ----------------------------------------------------
    # RECEIPTS
    # ----------------------------------------------------

    def add_receipt(self, expense_id: str, *, filename: str, url: str) -> Optional[Dict[str, Any]]:
        self._load()
        key = str(expense_id)

        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return None

            rec.setdefault("receipts", []).append({
                "filename": filename,
                "url": url,
                "created_at": int(time.time()),
            })

            self._save()
            return rec

    # ----------------------------------------------------
    # Load / Save
    # ----------------------------------------------------

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
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                _json_sanitize(self._data),
                f,
                indent=2,
                ensure_ascii=False,
            )

    def _next_id(self) -> str:
        return str(int(time.time() * 1000))

    # ----------------------------------------------------
    # CASH AGGREGATION
    # ----------------------------------------------------

    def pending_total_for_account(self, account_id: str) -> float:
        self._load()
        total = 0.0

        with self._lock:
            for rec in self._data.values():
                if rec.get("status") != "pending":
                    continue
                if str(rec.get("paid_through_account_id")) != str(account_id):
                    continue

                amt = _safe_float(rec.get("amount"))
                if amt is None and isinstance(rec.get("payload"), dict):
                    amt = _safe_float(rec["payload"].get("amount"))

                if amt:
                    total += float(amt)

        return float(total)

    # ----------------------------------------------------
    # CRUD
    # ----------------------------------------------------

    def add_pending(self, record: Dict[str, Any]) -> Dict[str, Any]:
        self._load()

        expense_id = str(record.get("expense_id") or self._next_id())
        payload = record.get("payload") or record

        vendor_name = (
            record.get("vendor_name")
            or (payload.get("vendor_name") if isinstance(payload, dict) else "")
            or ""
        )

        amount = _safe_float(record.get("amount"))
        exp_type = (record.get("expense_type") or "ordinary").lower().strip()

        normalized: Dict[str, Any] = {
            "expense_id": expense_id,
            "status": "pending",
            "created_at": int(time.time()),
            "created_by": record.get("created_by"),

            # ✅ FIX — persist owner
            "created_by": record.get("created_by"),

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
            "zoho_posted": bool(record.get("zoho_posted", False)),
            "zoho_error": record.get("zoho_error"),
            "zoho_response": record.get("zoho_response"),
            "balance": _safe_float(record.get("balance")),
            "clearing": record.get("clearing") or [],
            "cleared_at": record.get("cleared_at"),
            "payload": payload,
        }

        if exp_type == "accrued" and normalized["balance"] is None and amount is not None:
            normalized["balance"] = float(amount)

        with self._lock:
            self._data[expense_id] = normalized
            self._save()

        return normalized

    def create_pending(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return self.add_pending(record)

    def get(self, expense_id: str) -> Optional[Dict[str, Any]]:
        self._load()
        with self._lock:
            return self._data.get(str(expense_id))

    def update_fields(self, expense_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self._load()
        key = str(expense_id)

        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return None
            rec.update(updates)
            self._save()
            return rec

    def update(self, expense_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self._load()
        key = str(expense_id)

        with self._lock:
            rec = self._data.get(key)
            if not rec or rec.get("status") != "pending":
                return None
            rec.update(updates)
            self._save()
            return rec

    def delete(self, expense_id: str) -> bool:
        self._load()
        key = str(expense_id)

        with self._lock:
            if key not in self._data:
                return False
            del self._data[key]
            self._save()
            return True

    # ----------------------------------------------------
    # Listing
    # ----------------------------------------------------

    def list_approved(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        default_current_month: bool = False,
    ) -> List[Dict[str, Any]]:
        self._load()

        if not start_date and not end_date and default_current_month:
            start_date, end_date = _month_bounds()

        sd = _parse_yyyy_mm_dd(start_date)
        ed = _parse_yyyy_mm_dd(end_date)

        out: List[Dict[str, Any]] = []

        with self._lock:
            for rec in self._data.values():
                if rec.get("status") != "approved":
                    continue

                d = _parse_yyyy_mm_dd(rec.get("date"))
                if sd and (not d or d < sd):
                    continue
                if ed and (not d or d >= ed):
                    continue

                out.append(rec)

        out.sort(key=lambda x: x.get("approved_at", 0), reverse=True)
        return out

    def list_accrued(self, *, include_cleared: bool = False) -> List[Dict[str, Any]]:
        self._load()

        with self._lock:
            items = [
                x for x in self._data.values()
                if x.get("status") == "approved"
                and (x.get("expense_type") or "").lower() == "accrued"
            ]

        out: List[Dict[str, Any]] = []
        for x in items:
            bal = _safe_float(x.get("balance"))
            amt = _safe_float(x.get("amount"))

            if bal is None and amt is not None:
                bal = float(amt)
                x["balance"] = bal

            if not include_cleared and bal is not None and bal <= 0:
                continue

            out.append(x)

        out.sort(key=lambda x: x.get("approved_at", 0), reverse=True)
        return out

    def list_pending(self) -> List[Dict[str, Any]]:
        self._load()
        with self._lock:
            out = [x for x in self._data.values() if x.get("status") == "pending"]
        out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return out

    def list_all(self) -> List[Dict[str, Any]]:
        self._load()
        with self._lock:
            return list(self._data.values())

    # ----------------------------------------------------
    # State transitions
    # ----------------------------------------------------

    def approve(self, expense_id: str, *, zoho_response: Optional[Dict[str, Any]] = None) -> bool:
        self._load()
        key = str(expense_id)

        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return False

            rec["status"] = "approved"
            rec["approved_at"] = int(time.time())

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

    # ----------------------------------------------------
    # Accrued clearing
    # ----------------------------------------------------

    def clear_accrued(
        self,
        expense_id: str,
        *,
        amount: float,
        paid_through_account_id: str,
        paid_through_account_name: Optional[str] = None,
        clearing_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
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
                bal = _safe_float(rec.get("amount")) or 0.0

            new_bal = max(0.0, float(bal) - float(amt))
            rec["balance"] = new_bal

            rec.setdefault("clearing", []).append({
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


pending_store = PendingStore(
    path=os.path.join(os.path.dirname(__file__), "pending_expenses.json")
)
