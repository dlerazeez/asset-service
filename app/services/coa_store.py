from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

from ..core.config import settings


class COAStore:
    """
    Minimal CSV-backed COA loader for dropdowns.
    Expected columns: Account Name, Account Code, Account Type, Account SubType, Account ID (optional)
    """
    def __init__(self, csv_path: str) -> None:
        self.csv_path = csv_path
        self._rows: List[Dict[str, str]] = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.csv_path or not os.path.exists(self.csv_path):
            self._rows = []
            return

        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            self._rows = [r for r in reader]

    def expense_accounts(self) -> List[Dict[str, str]]:
        self._load()
        # Heuristic: include "Expense" and "Cost of Goods Sold"
        out = []
        for r in self._rows:
            t = (r.get("Account Type") or "").strip().lower()
            if "expense" in t or "cost of goods sold" in t:
                out.append(r)
        return out

    def paid_through_accounts(self) -> List[Dict[str, str]]:
        self._load()
        # Heuristic: bank/cash/credit card
        out = []
        for r in self._rows:
            t = (r.get("Account Type") or "").strip().lower()
            if any(x in t for x in ["bank", "cash", "credit card"]):
                out.append(r)
        return out

    def accrued_paid_through_account(self) -> Optional[Dict[str, str]]:
        """
        Finds the COA row that represents the 'Accrued Expenses' liability account.
        Matching is by:
          - settings.accrued_paid_through_account_id if provided, else
          - Account Name equals settings.accrued_expenses_account_name (case-insensitive)
        """
        self._load()

        if settings.accrued_paid_through_account_id:
            target = settings.accrued_paid_through_account_id.strip()
            for r in self._rows:
                rid = (r.get("Account ID") or r.get("Account Id") or r.get("account_id") or "").strip()
                if rid == target:
                    return r

        target_name = (settings.accrued_expenses_account_name or "").strip().lower()
        if not target_name:
            return None

        for r in self._rows:
            name = (r.get("Account Name") or r.get("account_name") or "").strip().lower()
            if name == target_name:
                return r
        return None


coa_store = COAStore(settings.coa_csv_path)
