import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # App
    app_name: str = os.getenv("APP_NAME", "asset-service")
    env: str = os.getenv("ENV", "dev")

    # Company / Auth
    company_email_domain: str = os.getenv("COMPANY_EMAIL_DOMAIN", "laveen-air.com")
    auth_secret: str = os.getenv("AUTH_SECRET", "change-me-in-prod")
    use_zoho: bool = os.getenv("USE_ZOHO", "true").lower() in ("1", "true", "yes")

    # Local data paths
    # root is project root (two levels up from this file: app/core/config.py)
    project_root: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir: str = os.getenv("DATA_DIR", os.path.join(project_root, "data"))
    uploads_dir: str = os.getenv("UPLOADS_DIR", os.path.join(data_dir, "uploads"))

    # Accrued handling
    accrued_expenses_account_name: str = os.getenv("ACCRUED_EXPENSES_ACCOUNT_NAME", "Accrued Expenses")
    accrued_paid_through_account_id: str = os.getenv("ACCRUED_PAID_THROUGH_ACCOUNT_ID", "")

    # Zoho OAuth
    zoho_client_id: str = os.getenv("ZOHO_CLIENT_ID", "")
    zoho_client_secret: str = os.getenv("ZOHO_CLIENT_SECRET", "")
    zoho_refresh_token: str = os.getenv("ZOHO_REFRESH_TOKEN", "")
    zoho_org_id: str = os.getenv("ZOHO_ORG_ID", "")
    zoho_dc: str = os.getenv("ZOHO_DC", "com")  # com, eu, in, etc.
    zoho_redirect_uri: str = os.getenv("ZOHO_REDIRECT_URI", "http://localhost")

    # Zoho Books
    zoho_books_base_url: str = os.getenv("ZOHO_BOOKS_BASE_URL", "https://www.zohoapis.com/books/v3")

    # COA CSV for dropdowns (optional local fallback)
    base_dir: str = os.path.dirname(os.path.abspath(__file__))
    coa_csv_path: str = os.getenv("COA_CSV_PATH", os.path.join(base_dir, "Chart_of_Accounts.csv"))


settings = Settings()

# Keep original behavior by default, but allow disabling Zoho in dev via USE_ZOHO=false
if settings.use_zoho and not all([settings.zoho_client_id, settings.zoho_client_secret, settings.zoho_refresh_token]):
    raise RuntimeError(
        "Missing Zoho OAuth environment variables (ZOHO_CLIENT_ID/ZOHO_CLIENT_SECRET/ZOHO_REFRESH_TOKEN)"
    )
