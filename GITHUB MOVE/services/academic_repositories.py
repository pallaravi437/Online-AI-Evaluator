import os
import json
import uuid
import csv
import io
from datetime import datetime, timezone
import logging
from typing import Optional, Dict, Any, List, Tuple
import openpyxl

from database.server_registry import db_server_manager
from database.routing_manager import storage_routing_manager
from database.file_storage_manager import file_storage_manager

logger = logging.getLogger("bea.academic_repositories")


class BaseRepository:
    """Base class providing multi-server dispatch and local JSON persistence fallback."""
    def __init__(self, category: str, local_filename: str):
        self.category = category
        self.local_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "database", "local_store")
        os.makedirs(self.local_dir, exist_ok=True)
        self.local_file = os.path.join(self.local_dir, local_filename)

    def _read_local_records(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.local_file):
            try:
                with open(self.local_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except Exception as e:
                logger.warning(f"Error reading local store {self.local_file}: {e}")
        return []

    def _save_local_records(self, records: List[Dict[str, Any]]):
        try:
            with open(self.local_file, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving local store {self.local_file}: {e}")


# =============================================================================
# 1. STUDENT MANAGEMENT REPOSITORY
# =============================================================================
class StudentRepository(BaseRepository):
    """
    Manages student demographics, enrollment, subject registrations,
    and examination history with deduplication and cross-database imports.
    """
    def __init__(self):
        super().__init__("students", "students_master.json")

    def get_student_by_pin(self, student_pin: str) -> Optional[Dict[str, Any]]:
        """Retrieves student by unique PIN across storage servers."""
        clean_pin = str(student_pin).strip().upper()
        # 1. Try local cache
        records = self._read_local_records()
        for r in records:
            if str(r.get("student_pin", "")).upper() == clean_pin:
                return r

        # 2. Try Supabase
        supa = db_server_manager.get_supabase_client("supabase_main", service_role=True)
        if supa:
            try:
                res = supa.table("students").select("*").eq("student_pin", clean_pin).execute()
                if res.data:
                    return res.data[0]
            except Exception:
                pass

        # 3. Try MySQL
        conn = db_server_manager.get_mysql_connection("mysql_primary")
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM students_master WHERE student_pin = %s LIMIT 1;", (clean_pin,))
                    row = cur.fetchone()
                    if row:
                        return row
                conn.close()
            except Exception:
                pass

        return None

    def upsert_student(
        self,
        student_pin: str,
        full_name: str = "",
        department: str = "",
        semester: str = "",
        academic_year: str = "",
        status: str = "active",
        enrollment_details: Optional[Dict[str, Any]] = None,
        subject_registrations: Optional[List[str]] = None,
        examination_history: Optional[List[Dict[str, Any]]] = None,
        storage_server_id: Optional[str] = None,
        **kwargs
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Creates or updates student record with deduplication on student_pin.
        Routes to assigned storage server.
        """
        clean_pin = str(student_pin).strip().upper()
        eff_name = full_name or kwargs.get("student_name") or kwargs.get("name") or ""
        if not clean_pin or not eff_name:
            return False, {}, "Student PIN and Full Name are required."

        assigned_server = storage_server_id or storage_routing_manager.resolve_server_for_data(
            category="students",
            department=department,
            semester=semester,
            academic_year=academic_year
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        student_data = {
            "id": f"std_{uuid.uuid4().hex[:10]}",
            "student_pin": clean_pin,
            "full_name": eff_name.strip(),
            "student_name": eff_name.strip(),
            "department": department.strip() if department else "General",
            "semester": semester.strip() if semester else "Semester 1",
            "academic_year": academic_year.strip() if academic_year else "2026-2027",
            "status": status,
            "enrollment_details": enrollment_details or {},
            "subject_registrations": subject_registrations or [],
            "examination_history": examination_history or [],
            "storage_server_id": assigned_server,
            "updated_at": now_iso
        }

        # Save to resolved server
        server = db_server_manager.get_server(assigned_server)
        provider = server.get("provider", "local") if server else "local"

        if provider == "mysql":
            conn = db_server_manager.get_mysql_connection(assigned_server)
            if conn:
                try:
                    with conn.cursor() as cur:
                        # Ensure table exists
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS students_master (
                                id VARCHAR(64) PRIMARY KEY,
                                student_pin VARCHAR(64) UNIQUE NOT NULL,
                                full_name VARCHAR(255) NOT NULL,
                                department VARCHAR(100),
                                semester VARCHAR(50),
                                academic_year VARCHAR(50),
                                status VARCHAR(50) DEFAULT 'active',
                                enrollment_details JSON,
                                subject_registrations JSON,
                                examination_history JSON,
                                storage_server_id VARCHAR(100),
                                updated_at DATETIME
                            );
                        """)
                        sql = """
                            INSERT INTO students_master 
                            (id, student_pin, full_name, department, semester, academic_year, status, 
                             enrollment_details, subject_registrations, examination_history, storage_server_id, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                            ON DUPLICATE KEY UPDATE
                            full_name = VALUES(full_name),
                            department = VALUES(department),
                            semester = VALUES(semester),
                            academic_year = VALUES(academic_year),
                            status = VALUES(status),
                            updated_at = NOW();
                        """
                        cur.execute(sql, (
                            student_data["id"], clean_pin, student_data["full_name"],
                            student_data["department"], student_data["semester"], student_data["academic_year"],
                            status, json.dumps(student_data["enrollment_details"]),
                            json.dumps(student_data["subject_registrations"]),
                            json.dumps(student_data["examination_history"]),
                            assigned_server
                        ))
                    conn.close()
                except Exception as e:
                    logger.warning(f"MySQL upsert failed: {e}")

        # Always sync to local store
        records = self._read_local_records()
        existing_idx = next((i for i, r in enumerate(records) if r.get("student_pin") == clean_pin), -1)
        if existing_idx >= 0:
            student_data["id"] = records[existing_idx]["id"]
            records[existing_idx] = student_data
        else:
            student_data["created_at"] = now_iso
            records.append(student_data)
        self._save_local_records(records)

        return True, student_data, f"Student {clean_pin} stored on server '{assigned_server}'."

    def list_all_students(
        self,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None,
        query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lists students with filtering and search."""
        records = self._read_local_records()
        filtered = []
        q = (query or "").lower().strip()
        for r in records:
            if department and r.get("department", "").lower() != department.lower():
                continue
            if semester and r.get("semester", "").lower() != semester.lower():
                continue
            if academic_year and r.get("academic_year", "").lower() != academic_year.lower():
                continue
            if q:
                pin_match = q in str(r.get("student_pin", "")).lower()
                name_match = q in str(r.get("full_name", "")).lower()
                if not pin_match and not name_match:
                    continue
            filtered.append(r)
        return filtered

    # -------------------------------------------------------------------------
    # MULTI-SOURCE STUDENT IMPORT
    # -------------------------------------------------------------------------
    def import_from_csv(self, csv_text_or_bytes: Any) -> Tuple[int, int, List[str]]:
        """Imports students from CSV data. Avoids duplicate PINs."""
        if isinstance(csv_text_or_bytes, bytes):
            text = csv_text_or_bytes.decode("utf-8", errors="replace")
        else:
            text = str(csv_text_or_bytes)

        reader = csv.DictReader(io.StringIO(text))
        imported, skipped = 0, 0
        errors = []

        for row in reader:
            pin = row.get("student_pin") or row.get("pin") or row.get("PIN") or row.get("Roll No") or row.get("roll_no")
            name = row.get("full_name") or row.get("student_name") or row.get("name") or row.get("Name") or row.get("Student Name")
            dept = row.get("department") or row.get("dept") or "General"
            sem = row.get("semester") or row.get("sem") or "Semester 1"
            year = row.get("academic_year") or row.get("year") or "2026-2027"

            if not pin or not name:
                skipped += 1
                continue

            ok, _, err = self.upsert_student(
                student_pin=pin,
                full_name=name,
                department=dept,
                semester=sem,
                academic_year=year
            )
            if ok:
                imported += 1
            else:
                errors.append(f"{pin}: {err}")

        return imported, skipped, errors

    def import_from_excel(self, excel_bytes: bytes) -> Tuple[int, int, List[str]]:
        """Imports students from an Excel (.xlsx) file."""
        try:
            wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), data_only=True)
            sheet = wb.active
            rows = list(sheet.iter_rows(values_only=True))
            if not rows or len(rows) < 2:
                return 0, 0, ["Excel sheet is empty or has no header row."]

            headers = [str(c).strip().lower() if c else "" for c in rows[0]]
            pin_idx = next((i for i, h in enumerate(headers) if "pin" in h or "roll" in h), 0)
            name_idx = next((i for i, h in enumerate(headers) if "name" in h), 1)
            dept_idx = next((i for i, h in enumerate(headers) if "dept" in h or "branch" in h), -1)
            sem_idx = next((i for i, h in enumerate(headers) if "sem" in h), -1)
            year_idx = next((i for i, h in enumerate(headers) if "year" in h), -1)

            imported, skipped = 0, 0
            errors = []

            for row in rows[1:]:
                if not row or not any(row):
                    continue
                pin = str(row[pin_idx]).strip() if pin_idx < len(row) and row[pin_idx] else ""
                name = str(row[name_idx]).strip() if name_idx < len(row) and row[name_idx] else ""
                dept = str(row[dept_idx]).strip() if dept_idx >= 0 and dept_idx < len(row) and row[dept_idx] else "General"
                sem = str(row[sem_idx]).strip() if sem_idx >= 0 and sem_idx < len(row) and row[sem_idx] else "Semester 1"
                year = str(row[year_idx]).strip() if year_idx >= 0 and year_idx < len(row) and row[year_idx] else "2026-2027"

                if not pin or not name:
                    skipped += 1
                    continue

                ok, _, err = self.upsert_student(pin, name, dept, sem, year)
                if ok:
                    imported += 1
                else:
                    errors.append(f"{pin}: {err}")

            return imported, skipped, errors
        except Exception as e:
            return 0, 0, [f"Excel parse error: {str(e)}"]

    def import_from_mysql(self, server_id: str = "mysql_primary", query: Optional[str] = None) -> Tuple[int, int, List[str]]:
        """Imports students directly from an existing external MySQL database."""
        conn = db_server_manager.get_mysql_connection(server_id)
        if not conn:
            return 0, 0, [f"Could not connect to MySQL server '{server_id}'."]

        sql = query or "SELECT student_pin, full_name, department, semester, academic_year FROM students_master LIMIT 500;"
        imported, skipped = 0, 0
        errors = []
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                for r in rows:
                    pin = r.get("student_pin")
                    name = r.get("full_name")
                    if not pin or not name:
                        skipped += 1
                        continue
                    ok, _, err = self.upsert_student(
                        student_pin=pin,
                        full_name=name,
                        department=r.get("department", "General"),
                        semester=r.get("semester", "Semester 1"),
                        academic_year=r.get("academic_year", "2026-2027"),
                        storage_server_id=server_id
                    )
                    if ok:
                        imported += 1
                    else:
                        errors.append(f"{pin}: {err}")
            conn.close()
            return imported, skipped, errors
        except Exception as e:
            return 0, 0, [f"MySQL import error: {str(e)}"]


# =============================================================================
# 2. SYLLABUS MANAGEMENT REPOSITORY
# =============================================================================
class SyllabusRepository(BaseRepository):
    """
    Manages institutional syllabi, course outcomes (CO), learning outcomes (LO),
    CO-PO mapping, practical experiments, and syllabus versioning.
    """
    def __init__(self):
        super().__init__("syllabus", "syllabus_curriculum.json")

    def save_syllabus(
        self,
        title: str = "",
        course_code: str = "",
        subject_name: str = "",
        department: str = "",
        semester: str = "",
        academic_year: str = "",
        version: str = "1.0",
        units_breakdown: Optional[List[Dict[str, Any]]] = None,
        course_outcomes: Optional[List[Dict[str, Any]]] = None,
        learning_outcomes: Optional[List[Dict[str, Any]]] = None,
        copo_mapping: Optional[Dict[str, Any]] = None,
        practical_experiments: Optional[List[Dict[str, Any]]] = None,
        teaching_hours: int = 60,
        credits: int = 4,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        created_by: str = "Lecturer",
        previous_version_id: Optional[str] = None,
        **kwargs
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Saves or updates syllabus curriculum with full version management.
        """
        eff_title = title or kwargs.get("course_title") or subject_name or ""
        eff_subject = subject_name or eff_title
        eff_units = units_breakdown or kwargs.get("units") or []
        if not eff_title or not course_code or not department:
            return False, {}, "Title, course code, and department are required."

        assigned_server = storage_routing_manager.resolve_server_for_data(
            category="syllabus",
            subject_code=course_code,
            department=department,
            semester=semester,
            academic_year=academic_year
        )

        file_metadata = {}
        if file_bytes and filename:
            file_metadata = file_storage_manager.upload_file(
                server_id=assigned_server,
                file_bytes=file_bytes,
                filename=filename,
                folder="syllabus"
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        syllabus_id = f"syl_{uuid.uuid4().hex[:10]}"

        record = {
            "id": syllabus_id,
            "title": eff_title.strip(),
            "course_title": eff_title.strip(),
            "course_code": course_code.strip().upper(),
            "subject_name": eff_subject.strip(),
            "department": department.strip(),
            "semester": semester.strip(),
            "academic_year": academic_year.strip(),
            "version": version,
            "is_active": True,
            "previous_version_id": previous_version_id,
            "teaching_hours": teaching_hours,
            "credits": credits,
            "units_breakdown": eff_units,
            "course_outcomes": course_outcomes or [],
            "learning_outcomes": learning_outcomes or [],
            "copo_mapping": copo_mapping or {},
            "practical_experiments": practical_experiments or [],
            "storage_server_id": assigned_server,
            "file_path": file_metadata.get("storage_path"),
            "file_url": file_metadata.get("file_url"),
            "created_by": created_by,
            "created_at": now_iso,
            "updated_at": now_iso
        }

        # If this is a new version of an existing syllabus, deactivate previous version
        records = self._read_local_records()
        if previous_version_id:
            for r in records:
                if r.get("id") == previous_version_id:
                    r["is_active"] = False

        records.append(record)
        self._save_local_records(records)

        # Sync to Supabase if connected
        supa = db_server_manager.get_supabase_client(assigned_server, service_role=True)
        if supa:
            try:
                supa.table("syllabus_curriculum").upsert(record).execute()
            except Exception as e:
                logger.warning(f"Could not sync syllabus to Supabase: {e}")

        return True, record, f"Syllabus '{title}' (v{version}) stored on server '{assigned_server}'."

    def get_syllabus_by_id(self, syllabus_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves syllabus from its original storage server."""
        records = self._read_local_records()
        for r in records:
            if r.get("id") == syllabus_id:
                return r
        return None

    def get_active_syllabus_for_course(
        self,
        course_code: str,
        department: Optional[str] = None,
        academic_year: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Retrieves latest active syllabus version for generating quizzes or materials."""
        records = self._read_local_records()
        code_clean = str(course_code).upper().strip()
        candidates = [
            r for r in records
            if (r.get("course_code") == code_clean or code_clean in str(r.get("title", "")).upper())
            and r.get("is_active", True)
        ]
        if department:
            dept_c = [r for r in candidates if r.get("department", "").lower() == department.lower()]
            if dept_c:
                candidates = dept_c
        if academic_year:
            yr_c = [r for r in candidates if r.get("academic_year", "") == academic_year]
            if yr_c:
                candidates = yr_c

        return candidates[-1] if candidates else None

    def list_syllabi(
        self,
        department: Optional[str] = None,
        semester: Optional[str] = None,
        academic_year: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lists syllabi with optional filters."""
        records = self._read_local_records()
        results = []
        for r in records:
            if department and r.get("department", "").lower() != department.lower():
                continue
            if semester and r.get("semester", "").lower() != semester.lower():
                continue
            if academic_year and r.get("academic_year") != academic_year:
                continue
            results.append(r)
        return results


# =============================================================================
# 3. ACADEMIC MATERIALS REPOSITORY
# =============================================================================
class MaterialRepository(BaseRepository):
    """
    Manages textbooks, reference books, PDFs, Word, PPTs, notes, question papers,
    reference answers, and laboratory manuals with versioning across servers.
    """
    def __init__(self):
        super().__init__("academic_materials", "academic_materials.json")

    def save_material(
        self,
        title: str,
        file_bytes: bytes,
        filename: str,
        material_category: str = "study_material",
        subject_code: str = "",
        department: str = "",
        semester: str = "",
        academic_year: str = "",
        unit_topic: str = "",
        version: str = "1.0",
        uploaded_by: str = "Lecturer",
        previous_version_id: Optional[str] = None,
        extracted_text: str = "",
        structured_content: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Uploads material asset to assigned object storage server and
        persists metadata in relational database.
        """
        if not file_bytes:
            return False, {}, "File bytes cannot be empty."

        assigned_server = storage_routing_manager.resolve_server_for_data(
            category="academic_materials",
            subject_code=subject_code,
            department=department,
            semester=semester,
            academic_year=academic_year,
            material_category=material_category
        )

        # Upload file to storage server
        upload_meta = file_storage_manager.upload_file(
            server_id=assigned_server,
            file_bytes=file_bytes,
            filename=filename,
            folder=f"materials/{material_category}"
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        mat_id = f"mat_{uuid.uuid4().hex[:10]}"

        record = {
            "id": mat_id,
            "title": title.strip() or filename,
            "material_category": material_category,
            "subject_code": subject_code.strip(),
            "department": department.strip(),
            "semester": semester.strip(),
            "academic_year": academic_year.strip(),
            "unit_topic": unit_topic.strip(),
            "version": version,
            "previous_version_id": previous_version_id,
            "file_type": upload_meta.get("file_type", "bin"),
            "file_size_kb": upload_meta.get("file_size_kb", 0),
            "storage_server_id": assigned_server,
            "storage_path": upload_meta.get("storage_path"),
            "file_url": upload_meta.get("file_url"),
            "extracted_text": extracted_text,
            "structured_content": structured_content or {},
            "uploaded_by": uploaded_by,
            "created_at": now_iso,
            "updated_at": now_iso
        }

        records = self._read_local_records()
        records.append(record)
        self._save_local_records(records)

        # Sync to Supabase if connected
        supa = db_server_manager.get_supabase_client(assigned_server, service_role=True)
        if supa:
            try:
                supa.table("academic_materials").upsert(record).execute()
            except Exception as e:
                logger.warning(f"Could not sync material to Supabase: {e}")

        return True, record, f"Material stored on '{assigned_server}'."

    def get_material_by_id(self, material_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves material metadata by ID."""
        records = self._read_local_records()
        for r in records:
            if r.get("id") == material_id:
                return r
        return None

    def list_materials(
        self,
        subject_code: Optional[str] = None,
        department: Optional[str] = None,
        material_category: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 100,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Lists materials with multi-faceted filtering."""
        records = self._read_local_records()
        filtered = []
        q = (query or "").lower().strip()
        for r in records:
            if subject_code and r.get("subject_code", "").lower() != subject_code.lower():
                continue
            if department and r.get("department", "").lower() != department.lower():
                continue
            if material_category and material_category != "all" and r.get("material_category") != material_category:
                continue
            if q:
                match_title = q in r.get("title", "").lower()
                match_subj = q in r.get("subject_code", "").lower()
                match_unit = q in r.get("unit_topic", "").lower()
                if not (match_title or match_subj or match_unit):
                    continue
            filtered.append(r)
        return filtered


# =============================================================================
# 4. QUESTION & EXAMINATION REPOSITORY
# =============================================================================
class QuestionRepository(BaseRepository):
    """
    Manages questions, question bank, difficulty, LO mapping,
    reference answers, and rubric versioning.
    """
    def __init__(self):
        super().__init__("examinations", "questions_bank.json")

    def save_question_to_bank(
        self,
        question_text: str,
        subject_code: str,
        question_type: str = "descriptive",
        max_marks: float = 5.0,
        difficulty: str = "medium",
        unit_topic: str = "",
        learning_outcome_id: Optional[str] = None,
        model_answer: str = "",
        model_answer_version: str = "1.0",
        marking_criteria: Optional[List[Dict[str, Any]]] = None,
        rubric_version: str = "1.0",
        test_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Saves a question to the universal bank with versioned rubrics."""
        assigned_server = storage_routing_manager.resolve_server_for_data(
            category="examinations",
            subject_code=subject_code,
            test_id=test_id
        )

        qid = f"qb_{uuid.uuid4().hex[:10]}"
        q_record = {
            "id": qid,
            "question_text": question_text.strip(),
            "subject_code": subject_code.strip(),
            "question_type": question_type,
            "max_marks": float(max_marks),
            "difficulty": difficulty,
            "unit_topic": unit_topic.strip(),
            "learning_outcome_id": learning_outcome_id,
            "model_answer": model_answer.strip(),
            "model_answer_version": model_answer_version,
            "marking_criteria": marking_criteria or [],
            "rubric_version": rubric_version,
            "storage_server_id": assigned_server,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        records = self._read_local_records()
        records.append(q_record)
        self._save_local_records(records)
        return q_record


# =============================================================================
# 5. SUBMISSION & EVALUATION REPOSITORY
# =============================================================================
class EvaluationRepository(BaseRepository):
    """
    Maintains link between student answer photo, OCR extracted text,
    AI evaluation, confidence score, reference answer version, rubric version,
    and lecturer approved marks.
    """
    def __init__(self):
        super().__init__("ai_evaluations", "evaluations_audit_trail.json")

    def record_evaluation_run(
        self,
        submission_id: str,
        question_id: str,
        student_pin: str,
        original_image_path: str,
        ocr_extracted_text: str,
        ai_awarded_marks: float,
        max_marks: float,
        criterion_breakdown: List[Dict[str, Any]],
        confidence_level: str = "high",
        reference_answer_version: str = "1.0",
        rubric_version: str = "1.0",
        lecturer_approved_marks: Optional[float] = None,
        lecturer_comments: str = "",
        test_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Records full immutable evaluation audit trail ensuring that
        the exact rubric version and model answer used during evaluation are frozen.
        """
        assigned_server = storage_routing_manager.resolve_server_for_data(
            category="ai_evaluations",
            test_id=test_id
        )

        eval_id = f"eval_{uuid.uuid4().hex[:10]}"
        eval_record = {
            "id": eval_id,
            "submission_id": submission_id,
            "question_id": question_id,
            "student_pin": student_pin,
            "original_image_path": original_image_path,
            "ocr_extracted_text": ocr_extracted_text,
            "ai_awarded_marks": ai_awarded_marks,
            "max_marks": max_marks,
            "criterion_breakdown": criterion_breakdown,
            "confidence_level": confidence_level,
            "reference_answer_version": reference_answer_version,
            "rubric_version": rubric_version,
            "lecturer_approved_marks": lecturer_approved_marks if lecturer_approved_marks is not None else ai_awarded_marks,
            "lecturer_comments": lecturer_comments,
            "storage_server_id": assigned_server,
            "evaluated_at": datetime.now(timezone.utc).isoformat()
        }

        records = self._read_local_records()
        records.append(eval_record)
        self._save_local_records(records)
        return eval_record


# Repository singletons
student_repository = StudentRepository()
syllabus_repository = SyllabusRepository()
material_repository = MaterialRepository()
question_repository = QuestionRepository()
evaluation_repository = EvaluationRepository()

