import os
import logging
from typing import Optional, Dict, Any, List, Tuple
from config import Config

logger = logging.getLogger("bea.db_manager")

try:
    from supabase import create_client, Client
    SUPABASE_SDK_AVAILABLE = True
except ImportError:
    SUPABASE_SDK_AVAILABLE = False
    Client = Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

# Core tables that must be present in the BEA database
REQUIRED_TABLES = [
    "teacher_profiles",
    "students",
    "topics",
    "assessments",
    "questions",
    "question_topics",
    "marking_criteria",
    "answer_submissions",
    "answer_pages",
    "evaluation_runs",
    "question_evaluations",
    "recheck_requests",
    "marks_audit_log",
    "application_settings"
]

class DatabaseManager:
    """
    Manages Supabase PostgreSQL connection, schema validation,
    RLS claim propagation, and storage bucket verification.
    """
    _instance: Optional['DatabaseManager'] = None
    _supabase_client: Optional[Client] = None
    _service_client: Optional[Client] = None

    # Placeholder detection is shared with Config so .env.example tokens
    # are recognised consistently across Supabase and Gemini credentials.
    _is_placeholder = staticmethod(Config.is_placeholder)

    @property
    def is_configured(self) -> bool:
        """True only when real (non-placeholder) Supabase credentials are present."""
        return not self._is_placeholder(self.url) and (
            not self._is_placeholder(self.anon_key) or not self._is_placeholder(self.service_key)
        )

    @property
    def _effective_service_key(self) -> str:
        """Service role key if real, otherwise fall back to a real anon key."""
        if not self._is_placeholder(self.service_key):
            return self.service_key
        if not self._is_placeholder(self.anon_key):
            return self.anon_key
        return ""

    def __init__(self):
        self.url = Config.SUPABASE_URL
        self.anon_key = Config.SUPABASE_KEY
        self.service_key = Config.SUPABASE_SERVICE_ROLE_KEY
        self.db_url = Config.SUPABASE_DB_URL
        self.storage_bucket = Config.STORAGE_BUCKET

    @classmethod
    def get_instance(cls) -> 'DatabaseManager':
        if cls._instance is None:
            cls._instance = DatabaseManager()
        return cls._instance

    def get_supabase(self, use_service_role: bool = False) -> Optional[Client]:
        """
        Returns a Supabase client.
        - If use_service_role is True, uses SUPABASE_SERVICE_ROLE_KEY for server-side trusted operations.
        - Otherwise uses SUPABASE_KEY (anon/public).
        - Returns None when credentials are missing or are unedited .env.example
          placeholders, so callers fall back to local/demo mode instead of
          attempting (and failing) network calls to a fake host.
        """
        if not SUPABASE_SDK_AVAILABLE:
            logger.warning("Supabase SDK is not installed.")
            return None

        if not self.is_configured:
            return None

        if use_service_role:
            service_key = self._effective_service_key
            if not self._service_client and self.url and service_key:
                try:
                    self._service_client = create_client(self.url, service_key)
                except Exception as e:
                    logger.error(f"Error creating Supabase service client: {e}")
            return self._service_client
        else:
            if not self._supabase_client and self.url and not self._is_placeholder(self.anon_key):
                try:
                    self._supabase_client = create_client(self.url, self.anon_key)
                except Exception as e:
                    logger.error(f"Error creating Supabase public client: {e}")
            return self._supabase_client

    def test_connection(self) -> Dict[str, Any]:
        """
        Tests connectivity to Supabase REST API and PostgreSQL database.
        Returns a comprehensive diagnostic report.
        """
        report = {
            "configured": self.is_configured,
            "supabase_url": self.url or "Not configured",
            "has_anon_key": bool(self.anon_key) and not self._is_placeholder(self.anon_key),
            "has_service_key": bool(self.service_key) and not self._is_placeholder(self.service_key),
            "has_db_url": bool(self.db_url) and not self._is_placeholder(self.db_url),
            "api_connected": False,
            "db_connected": False,
            "schema_valid": False,
            "missing_tables": [],
            "storage_bucket_exists": False,
            "error": None
        }

        # 1. Test Supabase API client
        client = self.get_supabase(use_service_role=True) or self.get_supabase(use_service_role=False)
        if client:
            try:
                # Query application_settings table to verify REST API read
                res = client.table("application_settings").select("setting_key").limit(1).execute()
                report["api_connected"] = True
            except Exception as e:
                report["error"] = f"Supabase API check: {str(e)}"

        # 2. Test Direct PostgreSQL connection if db_url is provided
        if self.db_url and PSYCOPG2_AVAILABLE:
            try:
                conn = psycopg2.connect(self.db_url, connect_timeout=5)
                report["db_connected"] = True
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # Check tables
                    cur.execute("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_schema = 'public';
                    """)
                    existing_tables = {row["table_name"] for row in cur.fetchall()}
                    missing = [t for t in REQUIRED_TABLES if t not in existing_tables]
                    report["missing_tables"] = missing
                    report["schema_valid"] = (len(missing) == 0)

                    # Check storage bucket
                    try:
                        cur.execute("SELECT id FROM storage.buckets WHERE id = %s;", (self.storage_bucket,))
                        if cur.fetchone():
                            report["storage_bucket_exists"] = True
                    except Exception:
                        pass
                conn.close()
            except Exception as e:
                if not report["error"]:
                    report["error"] = f"PostgreSQL direct check: {str(e)}"
        elif report["api_connected"]:
            # If no direct db_url, inspect schema via REST client
            try:
                missing = []
                for tbl in REQUIRED_TABLES:
                    try:
                        client.table(tbl).select("*").limit(0).execute()
                    except Exception:
                        missing.append(tbl)
                report["missing_tables"] = missing
                report["schema_valid"] = (len(missing) == 0)
            except Exception as e:
                report["error"] = f"REST schema check: {str(e)}"

        return report

    def execute_migration_file(self, filepath: str) -> Tuple[bool, str]:
        """
        Executes a SQL migration script against the PostgreSQL database.
        """
        if self._is_placeholder(self.db_url) or not PSYCOPG2_AVAILABLE:
            return False, "Direct database URL (SUPABASE_DB_URL) or psycopg2 is not configured."

        if not os.path.exists(filepath):
            return False, f"Migration file not found: {filepath}"

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                sql_content = f.read()

            conn = psycopg2.connect(self.db_url, connect_timeout=10)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql_content)
            conn.close()
            return True, "Migration executed successfully."
        except Exception as e:
            logger.error(f"Migration execution failed: {e}")
            return False, str(e)

# Global singleton accessor
db_manager = DatabaseManager.get_instance()
