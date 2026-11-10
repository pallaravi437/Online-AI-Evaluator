import os
import json
import logging
from typing import Optional, Dict, Any, List, Tuple
from config import Config

logger = logging.getLogger("bea.server_registry")

# Optional driver imports with resilient error handling
try:
    from supabase import create_client, Client as SupabaseClient
    SUPABASE_SDK_AVAILABLE = True
except ImportError:
    SUPABASE_SDK_AVAILABLE = False
    SupabaseClient = Any

try:
    import pymysql
    from pymysql.cursors import DictCursor
    PYMYSQL_AVAILABLE = True
except ImportError:
    PYMYSQL_AVAILABLE = False

try:
    import firebase_admin
    from firebase_admin import credentials, firestore, storage as fb_storage
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False


class DatabaseServerManager:
    """
    Manages connections, credentials, and health diagnostics for
    multi-server database backends (Supabase, MySQL, Firebase, and Local).
    """
    _instance: Optional['DatabaseServerManager'] = None

    def __init__(self):
        self.config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        os.makedirs(self.config_dir, exist_ok=True)
        self.registry_file = os.path.join(self.config_dir, "storage_servers.json")

        self.servers: Dict[str, Dict[str, Any]] = {}
        self._supabase_clients: Dict[str, Any] = {}
        self._firebase_apps: Dict[str, Any] = {}
        self._load_registry()

    @classmethod
    def get_instance(cls) -> 'DatabaseServerManager':
        if cls._instance is None:
            cls._instance = DatabaseServerManager()
        return cls._instance

    def _load_registry(self):
        """Loads configured servers from JSON file and initializes environment defaults."""
        default_servers = {
            "supabase_main": {
                "server_id": "supabase_main",
                "server_name": "Supabase Primary Cloud (PostgreSQL)",
                "provider": "supabase",
                "endpoint_url": Config.SUPABASE_URL,
                "is_active": bool(Config.SUPABASE_URL and not Config.is_placeholder(Config.SUPABASE_URL)),
                "is_default": True,
                "config": {
                    "url": Config.SUPABASE_URL,
                    "anon_key": Config.SUPABASE_KEY,
                    "service_role_key": Config.SUPABASE_SERVICE_ROLE_KEY,
                    "db_url": Config.SUPABASE_DB_URL,
                    "storage_bucket": Config.STORAGE_BUCKET or "bea-answer-scripts"
                }
            },
            "mysql_primary": {
                "server_id": "mysql_primary",
                "server_name": "College MySQL Academic Database",
                "provider": "mysql",
                "endpoint_url": os.getenv("MYSQL_HOST", "localhost:3306"),
                "is_active": bool(os.getenv("MYSQL_HOST")),
                "is_default": False,
                "config": {
                    "host": os.getenv("MYSQL_HOST", "localhost"),
                    "port": int(os.getenv("MYSQL_PORT", "3306")),
                    "user": os.getenv("MYSQL_USER", "root"),
                    "password": os.getenv("MYSQL_PASSWORD", ""),
                    "database": os.getenv("MYSQL_DATABASE", "bea_college_academic"),
                    "charset": "utf8mb4"
                }
            },
            "firebase_primary": {
                "server_id": "firebase_primary",
                "server_name": "Firebase Institutional Cloud (Firestore)",
                "provider": "firebase",
                "endpoint_url": os.getenv("FIREBASE_PROJECT_ID", "bea-online-evaluator"),
                "is_active": bool(os.getenv("FIREBASE_PROJECT_ID")),
                "is_default": False,
                "config": {
                    "project_id": os.getenv("FIREBASE_PROJECT_ID", ""),
                    "storage_bucket": os.getenv("FIREBASE_STORAGE_BUCKET", ""),
                    "credentials_json": os.getenv("FIREBASE_CREDENTIALS_JSON", "")
                }
            },
            "local_default": {
                "server_id": "local_default",
                "server_name": "Local Storage & JSON Manifest",
                "provider": "local",
                "endpoint_url": "filesystem://static/uploads",
                "is_active": True,
                "is_default": False,
                "config": {
                    "base_upload_path": "static/uploads"
                }
            }
        }

        if os.path.exists(self.registry_file):
            try:
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        # Merge saved configuration on top of defaults
                        for k, v in saved.items():
                            default_servers[k] = v
            except Exception as e:
                logger.warning(f"Could not load storage_servers.json: {e}")

        self.servers = default_servers

    def _save_registry(self):
        """Persists server registry to disk."""
        try:
            with open(self.registry_file, "w", encoding="utf-8") as f:
                json.dump(self.servers, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save server registry: {e}")

    def list_servers(self) -> List[Dict[str, Any]]:
        """Returns all configured servers with masked sensitive credentials."""
        result = []
        for s_id, s in self.servers.items():
            item = dict(s)
            item["masked_config"] = self._mask_config(s.get("config", {}), s.get("provider"))
            result.append(item)
        return result

    def get_server(self, server_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves server configuration by ID."""
        return self.servers.get(server_id)

    def register_or_update_server(
        self,
        server_id: str,
        server_name: str,
        provider: str,
        config: Dict[str, Any],
        is_active: bool = True,
        is_default: bool = False
    ) -> Tuple[bool, str]:
        """Registers a new storage server or updates existing server configuration."""
        if not server_id or not provider:
            return False, "server_id and provider are required."

        if provider not in ("supabase", "mysql", "firebase", "local"):
            return False, f"Unsupported provider '{provider}'. Must be supabase, mysql, firebase, or local."

        if is_default:
            # Unmark other defaults
            for s in self.servers.values():
                s["is_default"] = False

        existing = self.servers.get(server_id, {})
        merged_config = dict(existing.get("config", {}))

        # Do not overwrite real credentials with masked strings
        for k, v in config.items():
            if v and not any(mask_char in str(v) for mask_char in ("•", "…", "●")):
                merged_config[k] = v

        self.servers[server_id] = {
            "server_id": server_id,
            "server_name": server_name or existing.get("server_name", server_id),
            "name": server_name or existing.get("server_name", server_id),
            "provider": provider,
            "endpoint_url": merged_config.get("url") or merged_config.get("host") or merged_config.get("project_id") or "local",
            "is_active": is_active,
            "is_default": is_default,
            "config": merged_config
        }

        # Clear cached client instances
        self._supabase_clients.pop(server_id, None)
        self._firebase_apps.pop(server_id, None)

        self._save_registry()
        return True, f"Server '{server_id}' configured successfully."

    def delete_server(self, server_id: str) -> Tuple[bool, str]:
        """Deletes a custom server (cannot delete local_default or supabase_main)."""
        if server_id in ("local_default", "supabase_main"):
            return False, f"Cannot delete core server '{server_id}'."
        if server_id in self.servers:
            del self.servers[server_id]
            self._save_registry()
            return True, f"Server '{server_id}' removed."
        return False, "Server not found."

    # -------------------------------------------------------------------------
    # CLIENT ACQUISITION HELPERS
    # -------------------------------------------------------------------------
    def get_supabase_client(self, server_id: str = "supabase_main", service_role: bool = True) -> Optional[Any]:
        """Returns initialized Supabase Client."""
        if not SUPABASE_SDK_AVAILABLE:
            return None

        server = self.get_server(server_id)
        if not server or server.get("provider") != "supabase":
            server = self.get_server("supabase_main")
            if not server:
                return None

        cfg = server.get("config", {})
        url = cfg.get("url")
        key = cfg.get("service_role_key") if service_role else cfg.get("anon_key")
        if not key or Config.is_placeholder(key):
            key = cfg.get("anon_key")

        if not url or not key or Config.is_placeholder(url) or Config.is_placeholder(key):
            return None

        cache_key = f"{server_id}_{'service' if service_role else 'anon'}"
        if cache_key in self._supabase_clients:
            return self._supabase_clients[cache_key]

        try:
            client = create_client(url, key)
            self._supabase_clients[cache_key] = client
            return client
        except Exception as e:
            logger.error(f"Error creating Supabase client for {server_id}: {e}")
            return None

    def get_mysql_connection(self, server_id: str = "mysql_primary") -> Optional[Any]:
        """Returns PyMySQL database connection."""
        if not PYMYSQL_AVAILABLE:
            logger.warning("PyMySQL is not installed.")
            return None

        server = self.get_server(server_id)
        if not server or server.get("provider") != "mysql":
            return None

        cfg = server.get("config", {})
        host = cfg.get("host")
        if not host or Config.is_placeholder(host):
            return None

        try:
            conn = pymysql.connect(
                host=host,
                port=int(cfg.get("port", 3306)),
                user=cfg.get("user", "root"),
                password=cfg.get("password", ""),
                database=cfg.get("database", "bea_college_academic"),
                charset=cfg.get("charset", "utf8mb4"),
                cursorclass=DictCursor,
                connect_timeout=5,
                autocommit=True
            )
            return conn
        except Exception as e:
            logger.error(f"Failed to connect to MySQL server '{server_id}': {e}")
            return None

    def get_firebase_app(self, server_id: str = "firebase_primary") -> Optional[Any]:
        """Returns initialized Firebase App."""
        if not FIREBASE_AVAILABLE:
            return None

        server = self.get_server(server_id)
        if not server or server.get("provider") != "firebase":
            return None

        if server_id in self._firebase_apps:
            return self._firebase_apps[server_id]

        cfg = server.get("config", {})
        project_id = cfg.get("project_id")
        creds_json = cfg.get("credentials_json")
        storage_bucket = cfg.get("storage_bucket")

        try:
            app_name = f"bea_{server_id}"
            # Check if app is already initialized
            try:
                app = firebase_admin.get_app(app_name)
                self._firebase_apps[server_id] = app
                return app
            except ValueError:
                pass

            cred = None
            if creds_json and os.path.exists(creds_json):
                cred = credentials.Certificate(creds_json)
            elif creds_json and creds_json.strip().startswith("{"):
                try:
                    cred_dict = json.loads(creds_json)
                    cred = credentials.Certificate(cred_dict)
                except Exception:
                    pass

            options = {}
            if project_id:
                options["projectId"] = project_id
            if storage_bucket:
                options["storageBucket"] = storage_bucket

            if cred:
                app = firebase_admin.initialize_app(cred, options, name=app_name)
            elif project_id:
                # Application default credentials
                app = firebase_admin.initialize_app(options=options, name=app_name)
            else:
                return None

            self._firebase_apps[server_id] = app
            return app
        except Exception as e:
            logger.error(f"Failed to initialize Firebase app '{server_id}': {e}")
            return None

    # -------------------------------------------------------------------------
    # TEST CONNECTION DIAGNOSTICS
    # -------------------------------------------------------------------------
    def test_connection(self, server_id: str) -> Dict[str, Any]:
        """Tests connectivity and returns detailed health report."""
        server = self.get_server(server_id)
        if not server:
            return {"success": False, "server_id": server_id, "message": "Server ID not found in registry."}

        provider = server.get("provider")
        report = {
            "server_id": server_id,
            "server_name": server.get("server_name"),
            "provider": provider,
            "configured": True,
            "connected": False,
            "details": {},
            "message": ""
        }

        if provider == "local":
            base_p = server.get("config", {}).get("base_upload_path", "static/uploads")
            full_p = os.path.join(os.path.dirname(os.path.dirname(__file__)), base_p)
            os.makedirs(full_p, exist_ok=True)
            report["connected"] = True
            report["message"] = f"Local filesystem active at '{base_p}'."
            report["details"]["path"] = full_p
            return report

        elif provider == "supabase":
            client = self.get_supabase_client(server_id, service_role=True)
            if not client:
                report["message"] = "Supabase credentials missing or invalid placeholder."
                return report

            try:
                # Query table to verify REST API
                client.table("application_settings").select("setting_key").limit(1).execute()
                report["connected"] = True
                report["message"] = "Supabase REST API connection verified successfully."
                report["details"]["url"] = server.get("config", {}).get("url")
            except Exception as e:
                # Try another table or report error
                try:
                    client.table("students").select("id").limit(1).execute()
                    report["connected"] = True
                    report["message"] = "Supabase connected via students table."
                except Exception as e2:
                    report["message"] = f"Supabase API error: {str(e2)}"
            return report

        elif provider == "mysql":
            if not PYMYSQL_AVAILABLE:
                report["message"] = "PyMySQL library is not installed."
                return report

            conn = self.get_mysql_connection(server_id)
            if not conn:
                report["message"] = "Could not connect to MySQL server. Check host, port, user and credentials."
                return report

            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT VERSION() AS ver, DATABASE() AS db;")
                    row = cur.fetchone() or {}
                    report["connected"] = True
                    report["message"] = f"MySQL connected! Version: {row.get('ver')}, Database: {row.get('db')}"
                    report["details"] = row
                conn.close()
            except Exception as e:
                report["message"] = f"MySQL query error: {str(e)}"
            return report

        elif provider == "firebase":
            if not FIREBASE_AVAILABLE:
                report["message"] = "firebase-admin library is not installed."
                return report

            app = self.get_firebase_app(server_id)
            if not app:
                report["message"] = "Firebase app could not be initialized with provided credentials."
                return report

            try:
                db = firestore.client(app=app)
                # Quick read test
                collections = list(db.collections())
                report["connected"] = True
                report["message"] = f"Firebase Firestore connected! Found {len(collections)} collections."
                report["details"]["project_id"] = server.get("config", {}).get("project_id")
            except Exception as e:
                report["message"] = f"Firebase check: {str(e)}"
            return report

        return report

    def _mask_config(self, cfg: Dict[str, Any], provider: str) -> Dict[str, Any]:
        """Masks sensitive tokens for secure rendering."""
        masked = {}
        for k, v in cfg.items():
            if not v or Config.is_placeholder(str(v)):
                masked[k] = ""
            elif any(s in k.lower() for s in ("key", "secret", "password", "token", "cred")):
                s_str = str(v)
                if len(s_str) <= 8:
                    masked[k] = "••••••••"
                else:
                    masked[k] = f"{s_str[:3]}••••••••{s_str[-3:]}"
            else:
                masked[k] = v
        return masked


# Global singleton
db_server_manager = DatabaseServerManager.get_instance()

