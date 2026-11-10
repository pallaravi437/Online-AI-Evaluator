import os
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
import bcrypt

logger = logging.getLogger("bea.lecturer_service")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_STORE_DIR = os.path.join(BASE_DIR, "database", "local_store")
LECTURERS_FILE = os.path.join(LOCAL_STORE_DIR, "lecturers.json")
TENANTS_DIR = os.path.join(LOCAL_STORE_DIR, "tenants")

# Ensure required directories exist
os.makedirs(LOCAL_STORE_DIR, exist_ok=True)
os.makedirs(TENANTS_DIR, exist_ok=True)

_lecturers_cache: Optional[List[Dict[str, Any]]] = None

class LecturerService:
    """
    Manages multi-tenant lecturer registration, approval lifecycle,
    per-lecturer configuration (DB credentials & API keys), and tenant data isolation.
    """

    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "87654321"
    ADMIN_ID = "admin-00000000-0000-0000-0000-000000000000"

    @classmethod
    def _load_lecturers(cls) -> List[Dict[str, Any]]:
        global _lecturers_cache
        if _lecturers_cache is not None:
            return _lecturers_cache

        if not os.path.exists(LECTURERS_FILE):
            _lecturers_cache = []
            cls._save_lecturers(_lecturers_cache)
            return _lecturers_cache

        try:
            with open(LECTURERS_FILE, "r", encoding="utf-8") as f:
                _lecturers_cache = json.load(f)
        except Exception as e:
            logger.error(f"Error loading lecturers file: {e}")
            _lecturers_cache = []
        return _lecturers_cache

    @classmethod
    def _save_lecturers(cls, lecturers: List[Dict[str, Any]]) -> None:
        global _lecturers_cache
        _lecturers_cache = lecturers
        try:
            with open(LECTURERS_FILE, "w", encoding="utf-8") as f:
                json.dump(lecturers, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving lecturers file: {e}")

    @classmethod
    def get_tenant_data_dir(cls, lecturer_id: str) -> str:
        """Returns the isolated folder path for a lecturer's own data."""
        clean_id = str(lecturer_id).replace("..", "").replace("/", "").replace("\\", "")
        tenant_path = os.path.join(TENANTS_DIR, clean_id)
        os.makedirs(tenant_path, exist_ok=True)
        return tenant_path

    @classmethod
    def init_tenant_directory(cls, lecturer_id: str) -> str:
        """Initializes empty isolated store files for a lecturer."""
        tenant_dir = cls.get_tenant_data_dir(lecturer_id)
        defaults = {
            "assessments.json": [],
            "students.json": [],
            "submissions.json": [],
            "materials.json": [],
            "config.json": {
                "gemini_api_key": "",
                "ai_provider": "gemini",
                "ai_model": "auto",
                "database_type": "local",
                "supabase_url": "",
                "supabase_key": "",
                "mysql_host": "",
                "mysql_user": "",
                "mysql_password": "",
                "mysql_database": ""
            }
        }
        for fname, default_data in defaults.items():
            fpath = os.path.join(tenant_dir, fname)
            if not os.path.exists(fpath):
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(default_data, f, indent=2)
                except Exception as e:
                    logger.error(f"Failed to init tenant file {fname} for {lecturer_id}: {e}")
        return tenant_dir

    @classmethod
    def load_tenant_data(cls, lecturer_id: str, filename: str) -> Any:
        tenant_dir = cls.get_tenant_data_dir(lecturer_id)
        fpath = os.path.join(tenant_dir, filename)
        if not os.path.exists(fpath):
            cls.init_tenant_directory(lecturer_id)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to read {filename} for tenant {lecturer_id}: {e}")
        return []

    @classmethod
    def save_tenant_data(cls, lecturer_id: str, filename: str, data: Any) -> bool:
        tenant_dir = cls.get_tenant_data_dir(lecturer_id)
        fpath = os.path.join(tenant_dir, filename)
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            return True
        except Exception as e:
            logger.error(f"Failed to save {filename} for tenant {lecturer_id}: {e}")
            return False

    @classmethod
    def register_lecturer(
        cls,
        full_name: str,
        username: str,
        email: str,
        password: str,
        subject: str = "",
        mobile: str = "",
        specialization: str = ""
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Registers a new lecturer account pending administrator approval."""
        full_name = (full_name or "").strip()
        username = (username or "").strip()
        email = (email or "").strip().lower()

        if not full_name or not username or not email or not password:
            return False, None, "Full name, username, email, and password are required."

        if username.lower() == cls.ADMIN_USERNAME.lower():
            return False, None, "The username 'admin' is reserved for the system administrator."

        lecturers = cls._load_lecturers()

        for l in lecturers:
            if l.get("username", "").lower() == username.lower():
                return False, None, f"Username '{username}' is already registered."
            if l.get("email", "").lower() == email:
                return False, None, f"Email '{email}' is already registered."

        pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        lecturer_id = f"lec-{uuid.uuid4()}"
        created_at = datetime.now(timezone.utc).isoformat()

        record = {
            "id": lecturer_id,
            "full_name": full_name,
            "username": username,
            "email": email,
            "mobile": mobile.strip(),
            "subject": subject.strip(),
            "specialization": specialization.strip(),
            "password_hash": pw_hash,
            "is_active": False,
            "is_approved": False,
            "registered_at": created_at,
            "approved_at": None,
            "config": {
                "gemini_api_key": "",
                "ai_provider": "gemini",
                "ai_model": "auto",
                "database_type": "local",
                "supabase_url": "",
                "supabase_key": ""
            }
        }

        lecturers.append(record)
        cls._save_lecturers(lecturers)
        cls.init_tenant_directory(lecturer_id)

        logger.info(f"New lecturer registration submitted: {username} ({email}). Pending admin approval.")
        return True, {
            "id": lecturer_id,
            "username": username,
            "email": email,
            "full_name": full_name
        }, "Registration submitted successfully! Please wait for admin approval before logging in."

    @classmethod
    def authenticate_lecturer(cls, identifier: str, password: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Validates login credentials.
        Priority:
        1. Hardcoded Admin (username=admin, password=87654321)
        2. Approved registered lecturers
        """
        clean_id = (identifier or "").strip().lower()
        if not clean_id or not password:
            return False, None, "Please provide your username/email and password."

        # 1. Check Admin Superuser
        if clean_id in (cls.ADMIN_USERNAME, "admin@bea.edu") and password == cls.ADMIN_PASSWORD:
            return True, {
                "id": cls.ADMIN_ID,
                "email": "admin@bea.edu",
                "full_name": "System Administrator",
                "username": cls.ADMIN_USERNAME,
                "role": "admin",
                "is_approved": True,
                "is_active": True
            }, "Welcome Administrator."

        # 2. Check registered lecturers
        lecturers = cls._load_lecturers()
        match = None
        for l in lecturers:
            if l.get("username", "").lower() == clean_id or l.get("email", "").lower() == clean_id:
                match = l
                break

        if not match:
            return False, None, "Invalid lecturer credentials."

        pw_hash = match.get("password_hash", "").encode("utf-8")
        if not pw_hash or not bcrypt.checkpw(password.encode("utf-8"), pw_hash):
            return False, None, "Invalid lecturer credentials."

        if not match.get("is_approved", False):
            return False, None, "Your account is pending admin approval. Please contact the administrator."

        if not match.get("is_active", True):
            return False, None, "Your lecturer account is currently suspended. Please contact the administrator."

        # Ensure tenant storage directory is initialized
        cls.init_tenant_directory(match["id"])

        return True, {
            "id": match["id"],
            "email": match["email"],
            "full_name": match["full_name"],
            "username": match.get("username", ""),
            "role": "lecturer",
            "subject": match.get("subject", ""),
            "is_approved": True,
            "is_active": True
        }, f"Welcome back, {match['full_name']}!"

    @classmethod
    def list_all_lecturers(cls) -> List[Dict[str, Any]]:
        lecturers = cls._load_lecturers()
        # Return sanitized list without password hashes
        safe_list = []
        for l in lecturers:
            copy_l = dict(l)
            copy_l.pop("password_hash", None)
            safe_list.append(copy_l)
        return safe_list

    @classmethod
    def get_lecturer_by_id(cls, lecturer_id: str) -> Optional[Dict[str, Any]]:
        lecturers = cls._load_lecturers()
        for l in lecturers:
            if l.get("id") == lecturer_id:
                copy_l = dict(l)
                copy_l.pop("password_hash", None)
                return copy_l
        return None

    @classmethod
    def approve_lecturer(cls, lecturer_id: str) -> Tuple[bool, str]:
        lecturers = cls._load_lecturers()
        for l in lecturers:
            if l.get("id") == lecturer_id:
                l["is_approved"] = True
                l["is_active"] = True
                l["approved_at"] = datetime.now(timezone.utc).isoformat()
                cls._save_lecturers(lecturers)
                cls.init_tenant_directory(lecturer_id)
                logger.info(f"Lecturer {lecturer_id} approved by admin.")
                return True, f"Lecturer '{l.get('full_name')}' ({l.get('username')}) has been approved successfully."
        return False, "Lecturer record not found."

    @classmethod
    def reject_lecturer(cls, lecturer_id: str) -> Tuple[bool, str]:
        lecturers = cls._load_lecturers()
        new_list = [l for l in lecturers if l.get("id") != lecturer_id]
        if len(new_list) < len(lecturers):
            cls._save_lecturers(new_list)
            logger.info(f"Lecturer registration {lecturer_id} rejected and removed.")
            return True, "Lecturer registration rejected and removed."
        return False, "Lecturer record not found."

    @classmethod
    def delete_lecturer(cls, lecturer_id: str) -> Tuple[bool, str]:
        return cls.reject_lecturer(lecturer_id)

    @classmethod
    def update_lecturer_config(cls, lecturer_id: str, config_updates: Dict[str, Any]) -> Tuple[bool, str]:
        lecturers = cls._load_lecturers()
        for l in lecturers:
            if l.get("id") == lecturer_id:
                cfg = l.setdefault("config", {})
                for k, v in config_updates.items():
                    if v is not None and not str(v).startswith("••••"):
                        cfg[k] = v
                cls._save_lecturers(lecturers)
                # Also save to tenant config.json
                cls.save_tenant_data(lecturer_id, "config.json", cfg)
                return True, "Lecturer database & API configuration updated successfully."
        return False, "Lecturer record not found."

    @classmethod
    def get_lecturer_config(cls, lecturer_id: str) -> Dict[str, Any]:
        lecturer = cls.get_lecturer_by_id(lecturer_id)
        if lecturer:
            return lecturer.get("config", {})
        # Check tenant store
        cfg = cls.load_tenant_data(lecturer_id, "config.json")
        if isinstance(cfg, dict):
            return cfg
        return {}

