"""
Universal Academic Data Manager
================================
Top-level facade that exposes a single entry point to all academic data storage
components: server registry, routing, file storage, and all repositories.

Usage:
    from database.universal_data_manager import universal_data_manager as udm

    # Resolve which server holds student data
    server_id = udm.routing.resolve_server_for_data("students")

    # Save a material
    udm.materials.save_material(...)

    # Upload a file
    udm.file_storage.upload_file(server_id, file_bytes, "syllabus.pdf", "syllabi")

    # Search across all categories
    results = udm.search("BJT transistor", categories=["materials", "syllabi"])
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("bea.universal_data_manager")


class UniversalDataManager:
    """
    Facade over all Universal Academic Data Storage sub-systems.

    Sub-systems are imported lazily (on first access) to avoid circular
    imports and slow startup times when only a subset of features is used.
    """

    # ------------------------------------------------------------------
    # Sub-system properties (lazy imports)
    # ------------------------------------------------------------------

    @property
    def server_manager(self):
        """DatabaseServerManager — manages DB server registry."""
        if not hasattr(self, "_server_manager"):
            from database.server_registry import db_server_manager
            self._server_manager = db_server_manager
        return self._server_manager

    @property
    def routing(self):
        """StorageRoutingManager — resolves which server stores each data category."""
        if not hasattr(self, "_routing"):
            from database.routing_manager import storage_routing_manager
            self._routing = storage_routing_manager
        return self._routing

    @property
    def file_storage(self):
        """FileStorageManager — uploads/downloads files across all providers."""
        if not hasattr(self, "_file_storage"):
            from database.file_storage_manager import file_storage_manager
            self._file_storage = file_storage_manager
        return self._file_storage

    @property
    def students(self):
        """StudentRepository — CRUD for student master records."""
        if not hasattr(self, "_students"):
            from services.academic_repositories import student_repository
            self._students = student_repository
        return self._students

    @property
    def syllabi(self):
        """SyllabusRepository — versioned syllabus / CO / LO storage."""
        if not hasattr(self, "_syllabi"):
            from services.academic_repositories import syllabus_repository
            self._syllabi = syllabus_repository
        return self._syllabi

    @property
    def materials(self):
        """MaterialRepository — academic materials (PDFs, PPTs, notes, etc.)."""
        if not hasattr(self, "_materials"):
            from services.academic_repositories import material_repository
            self._materials = material_repository
        return self._materials

    @property
    def questions(self):
        """QuestionRepository — question bank management."""
        if not hasattr(self, "_questions"):
            from services.academic_repositories import question_repository
            self._questions = question_repository
        return self._questions

    @property
    def evaluations(self):
        """EvaluationRepository — immutable AI evaluation audit trail."""
        if not hasattr(self, "_evaluations"):
            from services.academic_repositories import evaluation_repository
            self._evaluations = evaluation_repository
        return self._evaluations

    # ------------------------------------------------------------------
    # Universal Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        categories: Optional[List[str]] = None,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None,
        storage_server_id: Optional[str] = None,
        user_role: str = "lecturer",
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Universal search across all academic data categories.

        Parameters
        ----------
        query           : Free-text search string.
        categories      : List of categories to search, e.g. ["students", "materials"].
                          Defaults to all categories.
        department      : Filter by department code.
        semester        : Filter by semester number/label.
        academic_year   : Filter by academic year string.
        storage_server_id: Restrict search to a specific storage server.
        user_role       : "student" | "lecturer" | "admin" — controls data visibility.
        user_id         : Student PIN or lecturer ID for role-based access control.
        limit           : Maximum results per category.

        Returns
        -------
        Dict mapping category names to lists of matching records.
        """
        try:
            from services.universal_search_service import universal_search_service
            return universal_search_service.search_all(
                query=query,
                categories=categories,
                department=department,
                semester=semester,
                academic_year=academic_year,
                storage_server_id=storage_server_id,
                user_role=user_role,
                user_id=user_id,
                limit=limit,
            )
        except ImportError:
            logger.warning("universal_search_service not yet available; using basic search.")
            return self._basic_search(query, categories, limit)

    def _basic_search(
        self,
        query: str,
        categories: Optional[List[str]] = None,
        limit: int = 50,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Minimal in-memory search fallback when universal_search_service is unavailable."""
        results: Dict[str, List[Dict[str, Any]]] = {}
        q_lower = query.lower().strip()

        # Search materials
        if not categories or "materials" in categories:
            try:
                all_mats = self.materials.list_materials()
                matched = [
                    m for m in all_mats
                    if q_lower in (m.get("title") or "").lower()
                    or q_lower in (m.get("subject_code") or "").lower()
                    or q_lower in (m.get("unit_topic") or "").lower()
                    or q_lower in (m.get("extracted_text") or "").lower()
                ]
                results["materials"] = matched[:limit]
            except Exception as e:
                logger.warning(f"Basic material search failed: {e}")
                results["materials"] = []

        # Search syllabi
        if not categories or "syllabi" in categories:
            try:
                all_syl = self.syllabi.list_syllabi()
                matched = [
                    s for s in all_syl
                    if q_lower in (s.get("course_title") or "").lower()
                    or q_lower in (s.get("subject_code") or "").lower()
                    or q_lower in (s.get("department") or "").lower()
                ]
                results["syllabi"] = matched[:limit]
            except Exception as e:
                logger.warning(f"Basic syllabus search failed: {e}")
                results["syllabi"] = []

        # Search students
        if not categories or "students" in categories:
            try:
                all_stu = self.students.list_all_students()
                matched = [
                    s for s in all_stu
                    if q_lower in (s.get("student_name") or "").lower()
                    or q_lower in (s.get("student_pin") or "").lower()
                ]
                results["students"] = matched[:limit]
            except Exception as e:
                logger.warning(f"Basic student search failed: {e}")
                results["students"] = []

        return results

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def get_system_status(self) -> Dict[str, Any]:
        """
        Returns a summary of system health: active servers, routing rules,
        repository record counts.
        """
        status: Dict[str, Any] = {
            "servers": {"total": 0, "healthy": 0, "list": []},
            "routing": {"rules": 0},
            "repositories": {},
        }

        try:
            servers = self.server_manager.list_servers()
            status["servers"]["total"] = len(servers)
            status["servers"]["list"] = [
                {"id": s["server_id"], "name": s.get("name", s["server_id"]), "type": s.get("db_type", "?")}
                for s in servers
            ]
        except Exception as e:
            logger.warning(f"Status: could not list servers: {e}")

        try:
            routes = self.routing.get_all_routes()
            total_rules = len(routes.get("overrides", [])) + len(routes.get("category_defaults", {}))
            status["routing"]["rules"] = total_rules
        except Exception as e:
            logger.warning(f"Status: could not get routing info: {e}")

        try:
            status["repositories"]["materials"] = len(self.materials.list_materials())
        except Exception:
            status["repositories"]["materials"] = "?"

        try:
            status["repositories"]["syllabi"] = len(self.syllabi.list_syllabi())
        except Exception:
            status["repositories"]["syllabi"] = "?"

        try:
            status["repositories"]["students"] = len(self.students.list_all_students())
        except Exception:
            status["repositories"]["students"] = "?"

        return status


# Global singleton — imported by routes and services
universal_data_manager = UniversalDataManager()

