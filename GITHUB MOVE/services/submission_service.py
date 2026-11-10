import os
import uuid
import secrets
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Tuple
from config import Config
from database.db_manager import db_manager

logger = logging.getLogger("bea.submission_service")

# Local in-memory store for drafts & submissions when running locally before Supabase sync
_local_submissions: Dict[str, Dict[str, Any]] = {}  # key: (assessment_id, student_id)
_local_pages: Dict[str, List[Dict[str, Any]]] = {}  # key: submission_id
_local_evaluations: Dict[str, Dict[str, Any]] = {}  # key: submission_id -> {question_id: eval_dict}
_local_rechecks: Dict[str, Dict[str, Any]] = {}  # key: f"{submission_id}_{question_id}"

class SubmissionService:
    """
    Manages question-wise answer page uploads, draft saving,
    single-submission locking, unique receipt code generation,
    timestamping in Asia/Kolkata, and lecturer reopening.
    """

    @staticmethod
    def generate_receipt_code() -> str:
        """Generates a cryptographically strong, human-readable submission receipt code."""
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_token = secrets.token_hex(3).upper()
        return f"BEA-{today_str}-{rand_token}"

    @classmethod
    def get_recent_submissions(cls, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Retrieves the latest submitted scripts across assessments
        for public activity feed before login.
        """
        supabase = db_manager.get_supabase(use_service_role=True)
        recent = []
        if supabase:
            try:
                res = supabase.table("answer_submissions").select(
                    "id, submission_receipt_code, status, submitted_at, students(full_name, pin), assessments(title)"
                ).in_("status", ["submitted", "evaluated", "approved"]).order("submitted_at", desc=True).limit(limit).execute()
                
                for s in (res.data or []):
                    st = s.get("students") or {}
                    asm = s.get("assessments") or {}
                    recent.append({
                        "id": s["id"],
                        "pin": st.get("pin", "26054-MEE"),
                        "name": st.get("full_name", "Student"),
                        "test_name": asm.get("title", "BEA Assessment"),
                        "submitted_at": s.get("submitted_at"),
                        "status": s.get("status", "submitted")
                    })
                if recent:
                    return recent
            except Exception as e:
                logger.error(f"Error fetching recent submissions: {e}")

        # Local mode / seeded recent activity
        now_utc = datetime.now(timezone.utc)
        for sub in _local_submissions.values():
            if sub.get("status") in ("submitted", "evaluated", "approved"):
                recent.append({
                    "id": sub["id"],
                    "pin": "26054-ES-041",
                    "name": "KASHABOINA PRANAVI",
                    "test_name": "Slip Test 1: BJT Fundamentals & CE Configuration",
                    "submitted_at": sub.get("submitted_at", now_utc.isoformat()),
                    "status": sub.get("status", "submitted")
                })

        if not recent:
            # Realistic seeded submissions for Diploma batch preview
            recent = [
                {
                    "pin": "26054-ES-041",
                    "name": "KASHABOINA PRANAVI",
                    "test_name": "Slip Test 1: BJT Fundamentals & CE Configuration",
                    "submitted_at": now_utc.isoformat(),
                    "status": "submitted"
                },
                {
                    "pin": "26054-CPS-001",
                    "name": "A SUJATHA",
                    "test_name": "Slip Test 1: BJT Fundamentals & CE Configuration",
                    "submitted_at": now_utc.isoformat(),
                    "status": "submitted"
                },
                {
                    "pin": "26054-CPS-005",
                    "name": "BADAVATH ROHITH RAJ",
                    "test_name": "Slip Test 1: BJT Fundamentals & CE Configuration",
                    "submitted_at": now_utc.isoformat(),
                    "status": "submitted"
                },
                {
                    "pin": "26054-SCT-032",
                    "name": "LOLABHATTU SAI SATWIK",
                    "test_name": "Slip Test 1: BJT Fundamentals & CE Configuration",
                    "submitted_at": now_utc.isoformat(),
                    "status": "submitted"
                }
            ]
        return recent

    @classmethod
    def get_submissions_for_assessment(cls, assessment_id: str) -> List[Dict[str, Any]]:
        """
        Retrieves all submissions for an assessment, newest first.
        Includes nested student info for display. Falls back to the local
        in-memory store when Supabase is unconfigured or unreachable.
        """
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("answer_submissions").select(
                    "*, students(id, pin, full_name, branch, section)"
                ).eq("assessment_id", assessment_id).order("submitted_at", desc=True).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error fetching submissions for assessment {assessment_id}: {e}")

        # Local mode: enrich records with student details for the teacher view
        from services.student_service import _local_students
        subs = []
        for sub in _local_submissions.values():
            if sub.get("assessment_id") != assessment_id:
                continue
            record = dict(sub)
            student = next(
                (st for st in _local_students if st.get("id") == sub.get("student_id")
                 or st.get("pin") == sub.get("student_id")),
                None
            )
            record["students"] = {
                "id": sub.get("student_id"),
                "pin": (student or {}).get("pin", sub.get("student_id", "")),
                "full_name": (student or {}).get("full_name", "Student"),
                "branch": (student or {}).get("branch", "ECE"),
                "section": (student or {}).get("section", "A"),
            } if student else None
            subs.append(record)

        subs.sort(key=lambda s: s.get("submitted_at") or s.get("created_at") or "", reverse=True)
        return subs

    @classmethod
    def get_or_create_draft_submission(cls, assessment_id: str, student_id: str) -> Dict[str, Any]:
        """
        Retrieves existing submission or initializes a new draft.
        Enforces one submission record per student per assessment.
        """
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("answer_submissions").select("*").eq(
                    "assessment_id", assessment_id
                ).eq("student_id", student_id).execute()

                if res.data:
                    return res.data[0]

                # Create new draft
                receipt_code = cls.generate_receipt_code()
                new_sub = {
                    "assessment_id": assessment_id,
                    "student_id": student_id,
                    "submission_receipt_code": receipt_code,
                    "status": "draft",
                    "is_locked": False,
                    "total_pages_uploaded": 0
                }
                insert_res = supabase.table("answer_submissions").insert(new_sub).execute()
                if insert_res.data:
                    return insert_res.data[0]
            except Exception as e:
                logger.error(f"Supabase get_or_create_submission error: {e}")

        # Local fallback mode
        key = f"{assessment_id}_{student_id}"
        if key not in _local_submissions:
            sub_id = str(uuid.uuid4())
            _local_submissions[key] = {
                "id": sub_id,
                "assessment_id": assessment_id,
                "student_id": student_id,
                "submission_receipt_code": cls.generate_receipt_code(),
                "status": "draft",
                "is_locked": False,
                "total_pages_uploaded": 0,
                "submitted_at": None,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            _local_pages[sub_id] = []
        return _local_submissions[key]

    @classmethod
    def save_answer_page(
        cls,
        submission_id: str,
        question_id: str,
        page_number: int,
        file_bytes: bytes,
        mime_type: str,
        metadata: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], str]:
        """
        Saves an uploaded answer page:
        - Uploads to private Supabase Storage bucket ('bea-answer-scripts').
        - Creates / updates answer_pages table metadata.
        """
        sub = cls.get_submission_by_id(submission_id)
        if not sub:
            return False, None, "Submission not found."

        if sub.get("is_locked", False):
            return False, None, "This submission is locked and cannot be modified."

        storage_path = f"submissions/{submission_id}/{question_id}_page_{page_number}_{metadata.get('sha256', '')[:8]}.jpg"
        supabase = db_manager.get_supabase(use_service_role=True)

        if supabase:
            try:
                # Upload to Supabase Private Storage
                try:
                    supabase.storage.from_(Config.STORAGE_BUCKET).upload(
                        path=storage_path,
                        file=file_bytes,
                        file_options={"content-type": mime_type, "upsert": "true"}
                    )
                except Exception as st_err:
                    logger.warning(f"Supabase storage upload note: {st_err}")

                # Insert / update record in answer_pages
                page_record = {
                    "submission_id": submission_id,
                    "question_id": question_id,
                    "page_number": page_number,
                    "storage_bucket": Config.STORAGE_BUCKET,
                    "storage_path": storage_path,
                    "mime_type": mime_type,
                    "original_size_bytes": metadata.get("original_size_bytes", len(file_bytes)),
                    "optimized_size_bytes": metadata.get("optimized_size_bytes", len(file_bytes)),
                    "image_width": metadata.get("width"),
                    "image_height": metadata.get("height"),
                    "sha256_checksum": metadata.get("sha256")
                }

                res = supabase.table("answer_pages").upsert(
                    page_record,
                    on_conflict="submission_id,question_id,page_number"
                ).execute()

                # Update page count
                count_res = supabase.table("answer_pages").select("id", count="exact").eq("submission_id", submission_id).execute()
                total = count_res.count if hasattr(count_res, "count") else len(count_res.data or [])
                supabase.table("answer_submissions").update({"total_pages_uploaded": total}).eq("id", submission_id).execute()

                return True, storage_path, "Page saved successfully."
            except Exception as e:
                logger.error(f"Error saving answer page to Supabase: {e}")
                return False, None, str(e)

        # Local mode
        pages = _local_pages.setdefault(submission_id, [])
        pages = [p for p in pages if not (p["question_id"] == question_id and p["page_number"] == page_number)]
        page_obj = {
            "id": str(uuid.uuid4()),
            "submission_id": submission_id,
            "question_id": question_id,
            "page_number": page_number,
            "storage_path": storage_path,
            "mime_type": mime_type,
            "original_size_bytes": metadata.get("original_size_bytes", len(file_bytes)),
            "optimized_size_bytes": metadata.get("optimized_size_bytes", len(file_bytes)),
            "image_width": metadata.get("width"),
            "image_height": metadata.get("height"),
            "bytes": file_bytes
        }
        pages.append(page_obj)
        _local_pages[submission_id] = pages
        sub["total_pages_uploaded"] = len(pages)
        return True, storage_path, "Page saved (Local mode)."

    @classmethod
    def get_pages_for_submission(cls, submission_id: str) -> List[Dict[str, Any]]:
        """Retrieves all answer pages for a submission, organized question-wise."""
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("answer_pages").select("*").eq("submission_id", submission_id).order("page_number").execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Error fetching answer pages: {e}")

        return _local_pages.get(submission_id, [])

    @classmethod
    def finalize_submission(cls, submission_id: str, client_ip: str = "", user_agent: str = "", mcq_answers: Optional[Dict[str, str]] = None) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Enforces final submission:
        - Locks student editing (is_locked = True, status = 'submitted' or 'approved' for MCQs).
        - Auto-grades MCQs against answer keys with instantaneous scoring.
        - Records server timestamp in Asia/Kolkata timezone.
        - Returns submission receipt.
        """
        sub = cls.get_submission_by_id(submission_id)
        if not sub:
            return False, None, "Submission record not found."

        if sub.get("is_locked", False):
            return True, sub, "Submission was already submitted and locked."

        now_utc = datetime.now(timezone.utc)
        now_kolkata = now_utc.astimezone(Config.TIMEZONE).strftime("%d %b %Y, %I:%M:%S %p %Z")

        # Auto-grade MCQs if student submitted choices
        evaluations_dict = {}
        if mcq_answers:
            from services.assessment_service import AssessmentService
            asm = AssessmentService.get_assessment_by_id(sub.get("assessment_id", ""))
            if asm and asm.get("questions"):
                mcq_score = 0.0
                total_mcq_possible = 0.0
                for q in asm["questions"]:
                    qid = str(q.get("id"))
                    q_num = str(q.get("question_number"))
                    correct_opt = (q.get("correct_option") or "A").strip().upper()
                    student_choice = (mcq_answers.get(qid) or mcq_answers.get(q_num) or "").strip().upper()
                    q_max = float(q.get("max_marks", 1.0))
                    total_mcq_possible += q_max
                    is_correct = bool(student_choice and student_choice == correct_opt)
                    awarded = q_max if is_correct else 0.0
                    mcq_score += awarded

                    eval_record = {
                        "id": str(uuid.uuid4()),
                        "submission_id": submission_id,
                        "question_id": qid,
                        "ai_marks_awarded": awarded,
                        "approved_marks": awarded,
                        "max_marks": q_max,
                        "student_selected_option": student_choice,
                        "correct_option": correct_opt,
                        "is_correct": is_correct,
                        "model_answer": q.get("model_answer", ""),
                        "explanation": q.get("model_answer", "") or f"Option {correct_opt} is the correct answer.",
                        "approval_status": "approved_as_is",
                        "ai_student_feedback": f"Selected Option {student_choice or 'None'}. {'✓ Correct! Full marks awarded.' if is_correct else f'✗ Incorrect. Correct answer is Option {correct_opt}.'}"
                    }
                    evaluations_dict[qid] = eval_record

                sub["mcq_answers"] = mcq_answers
                sub["ai_total_marks"] = mcq_score
                sub["official_total_marks"] = mcq_score
                max_total = float(asm.get("max_marks") or total_mcq_possible or 1.0)
                sub["percentage"] = round((mcq_score / max_total) * 100, 1)
                sub["pass_status"] = "pass" if sub["percentage"] >= float(asm.get("pass_percentage", 35.0)) else "fail"
                sub["status"] = "approved"

        _local_evaluations[submission_id] = evaluations_dict

        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                update_data = {
                    "status": sub.get("status", "submitted"),
                    "is_locked": True,
                    "submitted_at": now_utc.isoformat(),
                    "client_ip": client_ip,
                    "user_agent": user_agent
                }
                if mcq_answers:
                    update_data["ai_total_marks"] = sub.get("ai_total_marks")
                    update_data["official_total_marks"] = sub.get("official_total_marks")
                    update_data["percentage"] = sub.get("percentage")
                    update_data["pass_status"] = sub.get("pass_status")

                res = supabase.table("answer_submissions").update(update_data).eq("id", submission_id).execute()

                # Upsert individual evaluations into Supabase
                for qid, ev_row in evaluations_dict.items():
                    try:
                        supabase.table("question_evaluations").upsert({
                            "submission_id": submission_id,
                            "question_id": qid,
                            "ai_marks_awarded": ev_row["ai_marks_awarded"],
                            "approved_marks": ev_row["approved_marks"],
                            "max_marks": ev_row["max_marks"],
                            "approval_status": "approved_as_is",
                            "ai_student_feedback": ev_row["ai_student_feedback"],
                            "ai_model_answer_reference": ev_row["explanation"]
                        }, on_conflict="submission_id,question_id").execute()
                    except Exception as q_err:
                        logger.warning(f"Note inserting evaluation row: {q_err}")

                if res.data:
                    updated = res.data[0]
                    updated["submitted_at_formatted"] = now_kolkata
                    return True, updated, "Submission finalized and locked successfully."
            except Exception as e:
                logger.error(f"Error finalizing submission: {e}")
                return False, None, str(e)

        # Local mode
        if not mcq_answers:
            sub["status"] = "submitted"
        sub["is_locked"] = True
        sub["submitted_at"] = now_utc.isoformat()
        sub["submitted_at_formatted"] = now_kolkata
        return True, sub, "Submission finalized (Local mode)."

    @classmethod
    def get_evaluations_for_submission(cls, submission_id: str) -> Dict[str, Dict[str, Any]]:
        """Returns question evaluations mapping question_id -> evaluation details."""
        supabase = db_manager.get_supabase(use_service_role=True)
        evals = {}
        if supabase:
            try:
                res = supabase.table("question_evaluations").select("*").eq("submission_id", submission_id).execute()
                for row in (res.data or []):
                    evals[row["question_id"]] = row
                if evals:
                    return evals
            except Exception as e:
                logger.error(f"Error fetching evaluations from Supabase: {e}")

        # Local mode fallback
        return _local_evaluations.get(submission_id, {})

    @classmethod
    def get_submission_by_id(cls, submission_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves submission record by ID."""
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("answer_submissions").select(
                    "*, students(full_name, pin, branch, section), assessments(title, max_marks)"
                ).eq("id", submission_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                logger.error(f"Error fetching submission {submission_id}: {e}")

        for sub in _local_submissions.values():
            if sub["id"] == submission_id:
                return sub
        return None

    @classmethod
    def reopen_submission(cls, submission_id: str, lecturer_id: str, reason: str) -> Tuple[bool, str]:
        """Allows a teacher to reopen a locked submission with an audit reason."""
        if not reason or len(reason.strip()) < 5:
            return False, "A specific audit reason (at least 5 characters) is required to reopen."

        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                supabase.table("answer_submissions").update({
                    "is_locked": False,
                    "status": "reopened",
                    "reopened_by": lecturer_id,
                    "reopened_at": now_iso,
                    "reopen_reason": reason.strip()
                }).eq("id", submission_id).execute()

                # Add to marks_audit_log
                supabase.table("marks_audit_log").insert({
                    "submission_id": submission_id,
                    "action_type": "submission_reopened",
                    "reason": reason.strip(),
                    "actor_lecturer_id": lecturer_id
                }).execute()

                return True, "Submission reopened for student resubmission."
            except Exception as e:
                logger.error(f"Error reopening submission: {e}")
                return False, str(e)

        sub = cls.get_submission_by_id(submission_id)
        if sub:
            sub["is_locked"] = False
            sub["status"] = "reopened"
            sub["reopen_reason"] = reason
            return True, "Submission reopened (Local mode)."
        return False, "Submission not found."
