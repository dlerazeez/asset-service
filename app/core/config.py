from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    base_dir: str

    def __post_init__(self):
        self.zoho_org_id = os.getenv("ZOHO_ORG_ID", "868880872")
        self.zoho_base = os.getenv("ZOHO_BASE", "https://www.zohoapis.com/books/v3")
        self.zoho_auth_url = os.getenv("ZOHO_AUTH_URL", "https://accounts.zoho.com/oauth/v2/token")

        self.zoho_client_id = os.getenv("ZOHO_CLIENT_ID")
        self.zoho_client_secret = os.getenv("ZOHO_CLIENT_SECRET")
        self.zoho_refresh_token = os.getenv("ZOHO_REFRESH_TOKEN")

        # Expense report custom field API name
        self.expense_cf_api_name = os.getenv("EXPENSE_CF_API_NAME", "cf_expense_report")

        # Paths
        self.frontend_dir = os.path.join(self.base_dir, "frontend")
        self.coa_csv_path = os.getenv("COA_CSV_PATH", os.path.join(self.base_dir, "Chart_of_Accounts.csv"))

        self.storage_dir = os.path.join(self.base_dir, "storage")
        os.makedirs(self.storage_dir, exist_ok=True)

        self.pending_db_path = os.path.join(self.storage_dir, "pending.sqlite")

        if not all([self.zoho_client_id, self.zoho_client_secret, self.zoho_refresh_token]):
            raise RuntimeError("Missing Zoho OAuth environment variables (ZOHO_CLIENT_ID/ZOHO_CLIENT_SECRET/ZOHO_REFRESH_TOKEN)")
