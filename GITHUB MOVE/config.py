import os
from datetime import timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)

class Config:
    """Base application configuration with strict security defaults."""

    # Tokens that identify unedited template values shipped in .env.example
    PLACEHOLDER_TOKENS = ("your-", "changeme", "example.com", "generate_a_strong")

    @classmethod
    def is_placeholder(cls, value: str) -> bool:
        """True when a credential is empty or still an .env.example placeholder."""
        v = (value or "").strip().lower()
        if not v:
            return True
        return any(tok in v for tok in cls.PLACEHOLDER_TOKENS)

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "bea-secure-session-key-dev-default-38f92b71c4")
    
    # Session security
    SESSION_COOKIE_NAME = "bea_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=4)
    
    # Timezone (Asia/Kolkata default as required for Diploma assessments)
    TIMEZONE_NAME = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
    try:
        TIMEZONE = ZoneInfo(TIMEZONE_NAME)
    except Exception:
        from datetime import timezone as dt_timezone
        # IST is UTC + 5 hours 30 minutes
        TIMEZONE = dt_timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
    
    # Base directory
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    # Supabase Configuration
    SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()  # Public / Anon key
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()  # Backend only
    SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL", "").strip()
    STORAGE_BUCKET = "bea-answer-scripts"

    # MySQL Academic Database Configuration
    MYSQL_HOST = os.getenv("MYSQL_HOST", "").strip()
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root").strip()
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "").strip()
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "bea_college_academic").strip()

    # Firebase Admin Configuration
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
    FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
    
    # AI Provider & Model Configuration (Supports Gemini, OpenAI/ChatGPT, Claude, OpenRouter, Groq, Custom)
    AI_PROVIDER = os.getenv("AI_PROVIDER", "auto").strip().lower()
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("AI_API_KEY", "").strip()
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "auto").strip()
    AI_CUSTOM_BASE_URL = os.getenv("AI_CUSTOM_BASE_URL", "").strip()
    AI_RETRY_LIMIT = int(os.getenv("AI_RETRY_LIMIT", "3"))
    AI_BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", "5"))
    
    # MCP Integration Configuration
    MCP_ENABLED = os.getenv("MCP_ENABLED", "false").lower() in ("true", "1", "yes")
    MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "").strip()
    MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "").strip()
    
    # Student PIN Authentication & Rate Limiting
    MAX_FAILED_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = 15

    @classmethod
    def update_settings(cls, updates: dict) -> bool:
        """
        Updates configuration settings in memory and writes them permanently to the .env file.
        Ensures the system immediately uses the updated Gemini API Key and Model without restart.
        """
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        keys_written = set()
        new_lines = []
        for line in lines:
            line_str = line.strip()
            matched = False
            for k, v in updates.items():
                if line_str.startswith(f"{k}="):
                    new_lines.append(f"{k}={v}\n")
                    keys_written.add(k)
                    matched = True
                    break
            if not matched:
                new_lines.append(line)

        for k, v in updates.items():
            if k not in keys_written:
                new_lines.append(f"{k}={v}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        # Update in-memory Config class attributes immediately
        if "AI_PROVIDER" in updates and updates["AI_PROVIDER"] is not None:
            cls.AI_PROVIDER = str(updates["AI_PROVIDER"]).strip().lower()
            os.environ["AI_PROVIDER"] = cls.AI_PROVIDER
        if "GEMINI_API_KEY" in updates and updates["GEMINI_API_KEY"] is not None:
            cls.GEMINI_API_KEY = str(updates["GEMINI_API_KEY"]).strip()
            os.environ["GEMINI_API_KEY"] = cls.GEMINI_API_KEY
            os.environ["AI_API_KEY"] = cls.GEMINI_API_KEY
        if "AI_CUSTOM_BASE_URL" in updates and updates["AI_CUSTOM_BASE_URL"] is not None:
            cls.AI_CUSTOM_BASE_URL = str(updates["AI_CUSTOM_BASE_URL"]).strip()
            os.environ["AI_CUSTOM_BASE_URL"] = cls.AI_CUSTOM_BASE_URL
        if "GEMINI_MODEL" in updates and updates["GEMINI_MODEL"] is not None:
            cls.GEMINI_MODEL = str(updates["GEMINI_MODEL"]).strip()
            os.environ["GEMINI_MODEL"] = cls.GEMINI_MODEL
        if "AI_RETRY_LIMIT" in updates and updates["AI_RETRY_LIMIT"]:
            try:
                cls.AI_RETRY_LIMIT = int(updates["AI_RETRY_LIMIT"])
                os.environ["AI_RETRY_LIMIT"] = str(cls.AI_RETRY_LIMIT)
            except ValueError:
                pass
        if "AI_BATCH_SIZE" in updates and updates["AI_BATCH_SIZE"]:
            try:
                cls.AI_BATCH_SIZE = int(updates["AI_BATCH_SIZE"])
                os.environ["AI_BATCH_SIZE"] = str(cls.AI_BATCH_SIZE)
            except ValueError:
                pass
        if "MCP_ENABLED" in updates:
            cls.MCP_ENABLED = str(updates["MCP_ENABLED"]).lower() in ("true", "1", "yes")
            os.environ["MCP_ENABLED"] = "true" if cls.MCP_ENABLED else "false"
        if "MCP_SERVER_URL" in updates:
            cls.MCP_SERVER_URL = str(updates["MCP_SERVER_URL"]).strip()
            os.environ["MCP_SERVER_URL"] = cls.MCP_SERVER_URL
        if "MCP_AUTH_TOKEN" in updates:
            cls.MCP_AUTH_TOKEN = str(updates["MCP_AUTH_TOKEN"]).strip()
            os.environ["MCP_AUTH_TOKEN"] = cls.MCP_AUTH_TOKEN

        return True
    
    # File upload rules
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max payload
    ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "pdf"}
    MAX_PAGE_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB limit per page
    TARGET_COMPRESSED_KB = 400  # Target image size for fast AI & mobile viewing
