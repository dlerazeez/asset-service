class ZohoClient:
    def list_expenses(self): return []
    def get_expense(self, expense_id): return {"expense_id": expense_id}
    def create_expense(self, payload): return payload
    def approve_expense(self, expense_id): return True
    def delete_expense(self, expense_id): return True
    def list_vendors(self): return []

zoho = ZohoClient()
