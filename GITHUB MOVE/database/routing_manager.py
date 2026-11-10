import os
import json
import uuid
import logging
from typing import Optional, Dict, Any, List, Tuple
from database.server_registry import db_server_manager

logger = logging.getLogger("bea.routing_manager")

# Standard data categories required by the Universal Academic Data Storage system
ACADEMIC_CATEGORIES = [
    "academic_materials",  # Textbooks, reference books, PDFs, Word, PPTs, notes, lab manuals
    "syllabus",            # Course syllabi, CO, LO, CO-PO mapping, practical experiments
    "students",            # Student PIN, demographics, enrollments, registrations
    "examinations",        # Tests, questions, question banks, rubrics, submissions
    "ai_evaluations",      # OCR output, extracted text, AI scoring, confidence, rubric history
    "application_data"     # Users, lecturer profiles, audit logs, system metadata
]


class StorageRoutingManager:
    """
    Manages data storage assignment matrix across Firebase, MySQL, and Supabase.
    Evaluates rule hierarchy (Test-level > Subject-level > Department-level > Category default).
    """
    _instance: Optional['StorageRoutingManager'] = None

    def __init__(self):
        self.config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        os.makedirs(self.config_dir, exist_ok=True)
        self.routing_file = os.path.join(self.config_dir, "storage_routing.json")

        self.category_defaults: Dict[str, str] = {
            "academic_materials": "supabase_main",
            "syllabus": "supabase_main",
            "students": "mysql_primary",
            "examinations": "supabase_main",
            "ai_evaluations": "supabase_main",
            "application_data": "supabase_main"
        }
        self.overrides: List[Dict[str, Any]] = []
        self._load_routing()

    @classmethod
    def get_instance(cls) -> 'StorageRoutingManager':
        if cls._instance is None:
            cls._instance = StorageRoutingManager()
        return cls._instance

    def _load_routing(self):
        """Loads routing matrix from file or loads intelligent institutional defaults."""
        if os.path.exists(self.routing_file):
            try:
                with open(self.routing_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.category_defaults.update(data.get("category_defaults", {}))
                        self.overrides = data.get("overrides", [])
            except Exception as e:
                logger.warning(f"Could not load storage_routing.json: {e}")

        # Ensure sample overrides exist if empty
        if not self.overrides:
            self.overrides = [
                {
                    "id": "override-sample-01",
                    "category": "academic_materials",
                    "material_category": "textbook",
                    "server_id": "supabase_main",
                    "notes": "Large textbook PDFs stored in Supabase Object Storage"
                },
                {
                    "id": "override-sample-02",
                    "category": "students",
                    "department": "ECE",
                    "server_id": "mysql_primary",
                    "notes": "Student registration and academic history routed to College MySQL"
                }
            ]
            self._save_routing()

    def _save_routing(self):
        """Persists storage routing configuration to disk."""
        try:
            with open(self.routing_file, "w", encoding="utf-8") as f:
                json.dump({
                    "category_defaults": self.category_defaults,
                    "overrides": self.overrides
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving storage routing: {e}")

    def get_all_routes(self) -> Dict[str, Any]:
        """Returns current category defaults and overrides."""
        return {
            "category_defaults": self.category_defaults,
            "overrides": self.overrides,
            "available_servers": [
                {"id": s["server_id"], "name": s["server_name"], "provider": s["provider"]}
                for s in db_server_manager.list_servers()
            ]
        }

    def set_category_default(self, category: str, server_id: str) -> Tuple[bool, str]:
        """Sets default storage server for a high-level academic data category."""
        if category not in ACADEMIC_CATEGORIES:
            return False, f"Invalid category '{category}'."
        if not db_server_manager.get_server(server_id):
            return False, f"Server '{server_id}' not found."

        self.category_defaults[category] = server_id
        self._save_routing()
        return True, f"Category '{category}' default set to '{server_id}'."

    def add_override(
        self,
        category: str,
        server_id: str,
        test_id: Optional[str] = None,
        subject_code: Optional[str] = None,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None,
        material_category: Optional[str] = None,
        notes: str = ""
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Adds a specific routing override.
        Allows administrator to route e.g. BJT Test 1 -> Supabase Project A,
        Electronics Department Syllabus -> Firebase Project B, etc.
        """
        if not db_server_manager.get_server(server_id):
            return False, f"Server '{server_id}' is not registered in the system.", {}

        rule_id = f"rule_{uuid.uuid4().hex[:8]}"
        rule = {
            "id": rule_id,
            "category": category,
            "server_id": server_id,
            "test_id": test_id.strip() if test_id else None,
            "subject_code": subject_code.strip() if subject_code else None,
            "department": department.strip() if department else None,
            "semester": semester.strip() if semester else None,
            "academic_year": academic_year.strip() if academic_year else None,
            "material_category": material_category.strip() if material_category else None,
            "notes": notes.strip()
        }

        self.overrides.append(rule)
        self._save_routing()
        return True, "Storage assignment override added successfully.", rule

    def delete_override(self, rule_id: str) -> Tuple[bool, str]:
        """Deletes a routing override by ID."""
        initial_len = len(self.overrides)
        self.overrides = [r for r in self.overrides if r.get("id") != rule_id]
        if len(self.overrides) < initial_len:
            self._save_routing()
            return True, "Assignment rule removed."
        return False, "Rule not found."

    # -------------------------------------------------------------------------
    # ROUTE RESOLVER (CORE ENGINE)
    # -------------------------------------------------------------------------
    def resolve_server_for_data(
        self,
        category: str,
        test_id: Optional[str] = None,
        subject_code: Optional[str] = None,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None,
        material_category: Optional[str] = None
    ) -> str:
        """
        Resolves the designated storage server ID according to priority:
        1. Specific Test ID override (Highest priority, e.g. 'BJT Test 1' -> Supabase Project A)
        2. Material Category + Subject override (e.g. 'Multimedia Reference Books' -> Supabase Project C)
        3. Subject Code override (e.g. 'BEA (MEE-105)' -> Firebase Project B)
        4. Department + Semester override
        5. Department override (e.g. 'Electronics Dept Syllabus' -> Supabase Project A)
        6. Academic Year override
        7. Category Default (e.g. students -> MySQL College Database)
        8. System Default fallback ('supabase_main' or 'local_default')
        """
        # Step 1: Check Test ID override
        if test_id:
            for rule in self.overrides:
                if rule.get("test_id") and rule.get("test_id").lower() == str(test_id).lower():
                    srv = rule.get("server_id")
                    if db_server_manager.get_server(srv):
                        return srv

        # Step 2: Check Material Category override
        if material_category:
            for rule in self.overrides:
                if rule.get("material_category") and rule.get("material_category").lower() == str(material_category).lower():
                    if not rule.get("subject_code") or (subject_code and rule.get("subject_code").lower() in str(subject_code).lower()):
                        srv = rule.get("server_id")
                        if db_server_manager.get_server(srv):
                            return srv

        # Step 3: Check Subject Code override
        if subject_code:
            for rule in self.overrides:
                if rule.get("subject_code") and rule.get("subject_code").lower() in str(subject_code).lower():
                    srv = rule.get("server_id")
                    if db_server_manager.get_server(srv):
                        return srv

        # Step 4: Check Department + Semester override
        if department and semester:
            for rule in self.overrides:
                if (rule.get("department") and rule.get("department").lower() == str(department).lower() and
                    rule.get("semester") and rule.get("semester").lower() == str(semester).lower()):
                    srv = rule.get("server_id")
                    if db_server_manager.get_server(srv):
                        return srv

        # Step 5: Check Department override
        if department:
            for rule in self.overrides:
                if rule.get("department") and rule.get("department").lower() == str(department).lower():
                    srv = rule.get("server_id")
                    if db_server_manager.get_server(srv):
                        return srv

        # Step 6: Check Academic Year override
        if academic_year:
            for rule in self.overrides:
                if rule.get("academic_year") and rule.get("academic_year").lower() == str(academic_year).lower():
                    srv = rule.get("server_id")
                    if db_server_manager.get_server(srv):
                        return srv

        # Step 7: Category default (supports both short names e.g. 'materials' and 'academic_materials')
        norm_cat = {
            "materials": "academic_materials",
            "academic_materials": "academic_materials",
            "syllabi": "syllabus",
            "syllabus": "syllabus",
            "students": "students",
            "questions": "examinations",
            "examinations": "examinations",
            "evaluations": "ai_evaluations",
            "ai_evaluations": "ai_evaluations",
        }.get(category, category)

        cat_default = self.category_defaults.get(norm_cat) or self.category_defaults.get(category)
        if cat_default and db_server_manager.get_server(cat_default):
            return cat_default

        # Step 8: Fallback
        supa = db_server_manager.get_server("supabase_main")
        if supa:
            return "supabase_main"
        return "local_default"


# Global singleton
storage_routing_manager = StorageRoutingManager.get_instance()

