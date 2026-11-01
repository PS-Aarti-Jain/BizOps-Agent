import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = os.getenv("LOGGER")


def get_google_sheets_credentials() -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Loads Google Cloud Service Account credentials from the path specified
    in GOOGLE_APPLICATION_CREDENTIALS environment variable.

    Returns:
        (credentials_object, status_dict)
    """
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    if not creds_path:
        return None, {
            "status": "AUTH_CONFIG_MISSING",
            "message": "GOOGLE_APPLICATION_CREDENTIALS environment variable is not set.",
            "authenticated": False,
        }

    p = Path(creds_path)
    if not p.is_file():
        return None, {
            "status": "CREDENTIALS_FILE_NOT_FOUND",
            "message": f"Service account key file not found at: {creds_path}",
            "authenticated": False,
        }

    try:
        from google.oauth2 import service_account
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        credentials = service_account.Credentials.from_service_account_file(
            str(p), scopes=scopes
        )
        return credentials, {
            "status": "AUTHENTICATED",
            "message": "Service account credentials loaded successfully.",
            "authenticated": True,
            "service_account_email": getattr(credentials, "service_account_email", "unknown"),
        }
    except Exception as e:
        return None, {
            "status": "AUTH_ERROR",
            "message": f"Failed to load service account credentials: {str(e)}",
            "authenticated": False,
        }

