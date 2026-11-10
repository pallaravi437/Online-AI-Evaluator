"""
Universal Search Service
========================
Provides unified, multi-attribute searching across all academic data categories
(Students, Materials, Syllabi, Questions, Evaluations) with role-based filtering
and multi-server source attribution.
"""

import logging
from typing import Any, Dict, List, Optional
from database.server_registry import db_server_manager
from database.routing_manager import storage_routing_manager
from services.academic_repositories import (
    student_repository,
    syllabus_repository,
    material_repository,
    question_repository,
    evaluation_repository,
)

logger = logging.getLogger("bea.universal_search")


class UniversalSearchService:
    """
    Search engine across Universal Academic Data Storage repositories.
    """

    SUPPORTED_CATEGORIES = ["materials", "syllabi", "students", "questions", "evaluations"]

    def search_all(
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
        Executes a filtered search across specified categories.
        """
        q = (query or "").lower().strip()
        target_cats = [c.lower() for c in (categories or self.SUPPORTED_CATEGORIES)]
        results: Dict[str, List[Dict[str, Any]]] = {}

        # 1. Search Materials
        if "materials" in target_cats and user_role in ("student", "lecturer", "admin"):
            results["materials"] = self._search_materials(
                q, department=department, semester=semester,
                storage_server_id=storage_server_id, limit=limit
            )

        # 2. Search Syllabi
        if "syllabi" in target_cats and user_role in ("student", "lecturer", "admin"):
            results["syllabi"] = self._search_syllabi(
                q, department=department, semester=semester,
                academic_year=academic_year, storage_server_id=storage_server_id, limit=limit
            )

        # 3. Search Students
        if "students" in target_cats:
            # Role check: students can only see their own record
            if user_role == "student":
                if user_id:
                    stu = student_repository.get_student_by_pin(user_id)
                    results["students"] = [stu] if stu else []
                else:
                    results["students"] = []
            else:
                results["students"] = self._search_students(
                    q, department=department, semester=semester,
                    academic_year=academic_year, storage_server_id=storage_server_id, limit=limit
                )

        # 4. Search Questions (lecturer/admin only)
        if "questions" in target_cats and user_role in ("lecturer", "admin"):
            results["questions"] = self._search_questions(
                q, storage_server_id=storage_server_id, limit=limit
            )

        # 5. Search Evaluations
        if "evaluations" in target_cats:
            results["evaluations"] = self._search_evaluations(
                q, user_role=user_role, user_id=user_id,
                storage_server_id=storage_server_id, limit=limit
            )

        return results

    def _matches_common_filters(
        self,
        record: Dict[str, Any],
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None,
        storage_server_id: Optional[str] = None,
    ) -> bool:
        if storage_server_id and record.get("storage_server_id") != storage_server_id:
            return False
        if department and str(record.get("department", "")).upper() != department.upper():
            return False
        if semester and str(record.get("semester", "")).lower() != str(semester).lower():
            return False
        if academic_year and str(record.get("academic_year", "")).lower() != str(academic_year).lower():
            return False
        return True

    def _search_materials(
        self,
        q: str,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        storage_server_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            records = material_repository.list_materials(limit=200)
            matches = []
            for m in records:
                if not self._matches_common_filters(m, department=department, semester=semester, storage_server_id=storage_server_id):
                    continue
                if not q:
                    matches.append(m)
                    continue
                searchable_text = " ".join([
                    str(m.get("title", "")),
                    str(m.get("subject_code", "")),
                    str(m.get("unit_topic", "")),
                    str(m.get("material_type", "")),
                    str(m.get("file_type", "")),
                    str((m.get("structured_content") or {}).get("summary", "")),
                ]).lower()
                if q in searchable_text:
                    matches.append(m)
            return matches[:limit]
        except Exception as e:
            logger.error(f"Error searching materials: {e}")
            return []

    def _search_syllabi(
        self,
        q: str,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None,
        storage_server_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            records = syllabus_repository.list_syllabi(department=department, semester=semester, academic_year=academic_year)
            matches = []
            for s in records:
                if storage_server_id and s.get("storage_server_id") != storage_server_id:
                    continue
                if not q:
                    matches.append(s)
                    continue
                co_text = " ".join([str(item) for item in s.get("course_outcomes", [])])
                lo_text = " ".join([str(item) for item in s.get("learning_outcomes", [])])
                searchable = " ".join([
                    str(s.get("course_code", "")),
                    str(s.get("subject_code", "")),
                    str(s.get("course_title", "")),
                    str(s.get("department", "")),
                    str(s.get("academic_year", "")),
                    co_text,
                    lo_text,
                ]).lower()
                if q in searchable:
                    matches.append(s)
            return matches[:limit]
        except Exception as e:
            logger.error(f"Error searching syllabi: {e}")
            return []

    def _search_students(
        self,
        q: str,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None,
        storage_server_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            records = student_repository.list_all_students(department=department, semester=semester)
            matches = []
            for s in records:
                if not self._matches_common_filters(s, department=department, semester=semester, academic_year=academic_year, storage_server_id=storage_server_id):
                    continue
                if not q:
                    matches.append(s)
                    continue
                searchable = " ".join([
                    str(s.get("student_pin", "")),
                    str(s.get("student_name", "")),
                    str(s.get("department", "")),
                    str(s.get("email", "")),
                ]).lower()
                if q in searchable:
                    matches.append(s)
            return matches[:limit]
        except Exception as e:
            logger.error(f"Error searching students: {e}")
            return []

    def _search_questions(
        self,
        q: str,
        storage_server_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            records = question_repository._read_local_records()
            matches = []
            for item in records:
                if storage_server_id and item.get("storage_server_id") != storage_server_id:
                    continue
                if not q:
                    matches.append(item)
                    continue
                searchable = " ".join([
                    str(item.get("question_text", "")),
                    str(item.get("subject_code", "")),
                    str(item.get("test_id", "")),
                    str(item.get("difficulty", "")),
                    str(item.get("question_type", "")),
                ]).lower()
                if q in searchable:
                    matches.append(item)
            return matches[:limit]
        except Exception as e:
            logger.error(f"Error searching questions: {e}")
            return []

    def _search_evaluations(
        self,
        q: str,
        user_role: str,
        user_id: Optional[str],
        storage_server_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            records = evaluation_repository._read_local_records()
            matches = []
            for item in records:
                if storage_server_id and item.get("storage_server_id") != storage_server_id:
                    continue
                # Student restriction
                if user_role == "student" and user_id:
                    if str(item.get("student_pin", "")).upper() != user_id.upper():
                        continue
                if not q:
                    matches.append(item)
                    continue
                searchable = " ".join([
                    str(item.get("submission_id", "")),
                    str(item.get("student_pin", "")),
                    str(item.get("test_id", "")),
                    str(item.get("lecturer_feedback", "")),
                ]).lower()
                if q in searchable:
                    matches.append(item)
            return matches[:limit]
        except Exception as e:
            logger.error(f"Error searching evaluations: {e}")
            return []


universal_search_service = UniversalSearchService()

