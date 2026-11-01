import os
from pathlib import Path

# Load .env file automatically (system environment variables take priority)
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

# Base Directory (Project Root)
BASE_DIR = Path(__file__).resolve().parent.parent

# Database Configuration
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "bizops.db")))
SEED_DIR = DATA_DIR / "seed_fixtures"
SEED_DIR.mkdir(exist_ok=True)

# LLM Provider & Settings (Groq API Primary)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_TIMEOUT_SECONDS = float(os.getenv("GROQ_TIMEOUT_SECONDS", "20.0"))
GROQ_MAX_RETRIES = int(os.getenv("GROQ_MAX_RETRIES", "1"))
QUERY_TIMEOUT_SECONDS = float(os.getenv("QUERY_TIMEOUT_SECONDS", "60.0"))

LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", str(GROQ_TIMEOUT_SECONDS)))

# Google Sheets Configuration
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
GOOGLE_SHEETS_DEFAULT_WORKSHEET = os.getenv("GOOGLE_SHEETS_DEFAULT_WORKSHEET", "Inventory").strip()

# Execution & Safeguard Limits
MAX_REACT_ITERATIONS = int(os.getenv("MAX_REACT_ITERATIONS", "6"))
TOOL_EXECUTION_TIMEOUT_SECONDS = float(os.getenv("TOOL_EXECUTION_TIMEOUT_SECONDS", "10.0"))
OBSERVATION_CHAR_LIMIT = int(os.getenv("OBSERVATION_CHAR_LIMIT", "1500"))
PA_EXPIRATION_SECONDS = int(os.getenv("PA_EXPIRATION_SECONDS", "600"))  # 10 minutes TTL
PRICE_CHANGE_TOLERANCE_PCT = float(os.getenv("PRICE_CHANGE_TOLERANCE_PCT", "0.05"))

# Developer / Debug Mode
DEVELOPER_MODE = os.getenv("DEVELOPER_MODE", "false").lower() in ("true", "1", "yes")

# Environment & Server
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8080,http://localhost:8080").split(",")
    if o.strip()
]

# User Roles & RBAC Settings
DEFAULT_USER_ID = "USR-001"
DEFAULT_USER_ROLE = "Manager"  # Operator, Manager, Admin
ROLE_HIERARCHY = {"Operator": 1, "Manager": 2, "Admin": 3}

DEFAULT_TOKEN_USER_MAP = {
    "token-admin-1": {"user_id": "USR-ADMIN-01", "role": "Admin", "name": "System Administrator"},
    "token-manager-1": {"user_id": "USR-001", "role": "Manager", "name": "Operations Manager"},
    "token-operator-1": {"user_id": "USR-OP-01", "role": "Operator", "name": "Warehouse Operator"},
}

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() in ("true", "1", "yes")
MULTI_AGENT = os.getenv("MULTI_AGENT", "false").lower() in ("true", "1", "yes")

# Intent Routing Configuration
INTENTS_FILE = BASE_DIR / "config" / "intents.yaml"
try:
    import yaml
    if INTENTS_FILE.exists():
        with open(INTENTS_FILE, "r", encoding="utf-8") as f:
            INTENTS_CONFIG = yaml.safe_load(f) or {}
    else:
        INTENTS_CONFIG = {}
except Exception:
    INTENTS_CONFIG = {}

