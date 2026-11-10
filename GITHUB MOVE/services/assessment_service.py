import os
import json
import logging
from datetime import date
from typing import List, Dict, Any, Optional, Tuple
from config import Config
from database.db_manager import db_manager

logger = logging.getLogger("bea.assessment_service")

# In-memory mock storage for local development / testing before Supabase connection
_local_assessments: List[Dict[str, Any]] = []
_local_questions: Dict[str, List[Dict[str, Any]]] = {}

class AssessmentService:
    """
    Manages creation and configuration of Assessments (Slip tests, Assignments, Unit tests),
    questions with rubrics/marking criteria, and syllabus topic mappings.
    """

    # ── Shared helpers (single source of truth) ───────────────────────────────

    @staticmethod
    def _detect_q_type(question_data: Dict[str, Any]) -> str:
        """Infers question type from data: 'mcq' if options present, else 'descriptive'."""
        explicit = question_data.get("question_type", "")
        if explicit in ("mcq", "descriptive", "numerical", "diagram"):
            return explicit
        if question_data.get("option_a") or question_data.get("option_b"):
            return "mcq"
        return "descriptive"

    @staticmethod
    def _default_mcq_criteria(correct_option: str, max_marks: float) -> List[Dict[str, Any]]:
        """Returns the default single-criterion rubric for an MCQ question."""
        return [{
            "criterion_title": f"Correct Option Selection ({correct_option})",
            "max_marks": max_marks,
            "description": "Accurate option selected"
        }]

    @staticmethod
    def _build_question_row(question_data: Dict[str, Any], q_type: str, clean_ans: str,
                            assessment_id: Optional[str] = None,
                            display_order: int = 1) -> Dict[str, Any]:
        """Single source of truth for the questions table row dict."""
        row: Dict[str, Any] = {
            "question_number": question_data.get("question_number", str(display_order)),
            "display_order": display_order,
            "question_type": q_type,
            "question_text": question_data.get("question_text", "").strip(),
            "unit_topic": question_data.get("unit_topic", "").strip(),
            "learning_outcome": question_data.get("learning_outcome", "").strip(),
            "option_a": question_data.get("option_a", "").strip(),
            "option_b": question_data.get("option_b", "").strip(),
            "option_c": question_data.get("option_c", "").strip(),
            "option_d": question_data.get("option_d", "").strip(),
            "correct_option": clean_ans if q_type == "mcq" else None,
            "max_marks": float(question_data.get("max_marks", 1.0 if q_type == "mcq" else 5.0)),
            "model_answer": question_data.get("model_answer", "").strip(),
            "acceptable_alternatives": question_data.get("acceptable_alternatives", "").strip(),
            "difficulty": question_data.get("difficulty", "medium"),
            "requires_diagram": bool(question_data.get("requires_diagram", False)),
            "requires_numerical": bool(question_data.get("requires_numerical", False)),
            "question_image_url": question_data.get("question_image_url", ""),
            "question_image_path": question_data.get("question_image_path", ""),
        }
        if assessment_id:
            row["assessment_id"] = assessment_id
        return row

    @staticmethod
    def _insert_criteria(supabase, question_id: str, criteria: List[Dict[str, Any]]):
        """Inserts marking_criteria rows for a question."""
        for idx, c in enumerate(criteria, start=1):
            supabase.table("marking_criteria").insert({
                "question_id": question_id,
                "criterion_order": idx,
                "criterion_title": c.get("criterion_title", f"Criterion {idx}").strip(),
                "max_marks": float(c.get("max_marks", 1.0)),
                "description": c.get("description", "").strip()
            }).execute()

    @classmethod
    def get_all_assessments(cls, teacher_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves list of all assessments with question count and submission summary, with multi-tenant isolation."""
        if teacher_id and teacher_id.startswith("lec-"):
            from services.lecturer_service import LecturerService
            tenant_asms = LecturerService.load_tenant_data(teacher_id, "assessments.json")
            matching_local = [a for a in _local_assessments if a.get("created_by") == teacher_id]
            combined_dict = {}
            for a in (tenant_asms or []):
                combined_dict[a["id"]] = a
            for a in matching_local:
                combined_dict[a["id"]] = a
            return list(combined_dict.values())

        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                query = supabase.table("assessments").select(
                    "id, title, assessment_type, subject_code, assessment_date, max_marks, pass_percentage, status, allow_submissions, results_published, created_at, created_by"
                )
                if teacher_id and not teacher_id.startswith("admin"):
                    query = query.eq("created_by", teacher_id)
                res = query.order("assessment_date", desc=True).execute()
                assessments = res.data or []
                return assessments
            except Exception as e:
                logger.error(f"Error fetching assessments: {e}")

        # Ensure default assessments are seeded if not present
        if not any(a.get("id") == "bea-slip-test-001" for a in _local_assessments):
            cls._seed_default_assessment()
        return _local_assessments

    @classmethod
    def get_assessment_by_id(cls, assessment_id: str) -> Optional[Dict[str, Any]]:
        """Fetches assessment details and questions."""
        if not any(a.get("id") == "bea-slip-test-001" for a in _local_assessments):
            cls._seed_default_assessment()

        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("assessments").select("*").eq("id", assessment_id).execute()
                if res.data:
                    assessment = res.data[0]
                    # Fetch questions
                    q_res = supabase.table("questions").select(
                        "*, marking_criteria(*)"
                    ).eq("assessment_id", assessment_id).order("display_order").execute()
                    assessment["questions"] = q_res.data or []
                    return assessment
            except Exception as e:
                logger.error(f"Error fetching assessment {assessment_id}: {e}")

        for asm in _local_assessments:
            if asm["id"] == assessment_id:
                asm_copy = dict(asm)
                asm_copy["questions"] = _local_questions.get(assessment_id, [])
                return asm_copy

        # Check tenant stores
        try:
            from services.lecturer_service import LecturerService, TENANTS_DIR
            import os, json
            if os.path.exists(TENANTS_DIR):
                for tid in os.listdir(TENANTS_DIR):
                    tenant_asms = LecturerService.load_tenant_data(tid, "assessments.json")
                    for a in (tenant_asms or []):
                        if a.get("id") == assessment_id:
                            a_copy = dict(a)
                            a_copy["questions"] = _local_questions.get(assessment_id, a.get("questions", []))
                            return a_copy
        except Exception as e:
            logger.debug(f"Tenant search error for assessment {assessment_id}: {e}")

        return None

    @classmethod
    def create_assessment(
        cls,
        title: str,
        assessment_type: str,
        max_marks: float,
        assessment_date: str,
        instructions: str = "",
        pass_percentage: float = 35.0,
        questions: Optional[List[Dict[str, Any]]] = None,
        created_by_id: Optional[str] = None,
        unit_topic: str = "General",
        deadline: Optional[str] = None,
        subject_code: str = "General Academic"
    ) -> Tuple[bool, Optional[str], str]:
        """Creates a new assessment with questions and marking criteria."""
        supabase = db_manager.get_supabase(use_service_role=True)
        
        record = {
            "title": title.strip(),
            "assessment_type": assessment_type,
            "subject_code": (subject_code or "General Academic").strip(),
            "assessment_date": assessment_date or str(date.today()),
            "unit_topic": unit_topic.strip() or "General",
            "deadline": deadline,
            "max_marks": float(max_marks),
            "pass_percentage": float(pass_percentage),
            "instructions": instructions.strip(),
            "status": "draft",
            "allow_submissions": False,
            "results_published": False,
            "created_by": created_by_id
        }

        if supabase:
            try:
                res = supabase.table("assessments").insert(record).execute()
                if res.data:
                    new_id = res.data[0]["id"]
                    if questions:
                        cls._save_questions(supabase, new_id, questions)
                    return True, new_id, "Assessment created successfully."
            except Exception as e:
                logger.error(f"Error creating assessment in Supabase: {e}")
                return False, None, str(e)

        # Local mode
        import uuid
        new_id = str(uuid.uuid4())
        record["id"] = new_id
        if not (created_by_id and created_by_id.startswith("lec-")):
            _local_assessments.insert(0, record)
        if questions:
            for idx, q in enumerate(questions, start=1):
                if "id" not in q or not q["id"]:
                    q["id"] = str(uuid.uuid4())
                if "question_number" not in q or not q["question_number"]:
                    q["question_number"] = str(idx)
            _local_questions[new_id] = questions
        else:
            _local_questions[new_id] = []

        if created_by_id and created_by_id.startswith("lec-"):
            from services.lecturer_service import LecturerService
            tenant_asms = LecturerService.load_tenant_data(created_by_id, "assessments.json") or []
            tenant_asms.insert(0, record)
            LecturerService.save_tenant_data(created_by_id, "assessments.json", tenant_asms)

        return True, new_id, "Assessment created (Local session)."

    @classmethod
    def recalculate_total_marks(cls, assessment_id: str) -> float:
        """Sums max_marks of all configured questions and synchronizes the assessment's total max_marks."""
        supabase = db_manager.get_supabase(use_service_role=True)
        total = 0.0

        if supabase:
            try:
                res = supabase.table("questions").select("max_marks").eq("assessment_id", assessment_id).execute()
                rows = res.data or []
                total = sum(float(r.get("max_marks") or 0.0) for r in rows)
                if total > 0:
                    supabase.table("assessments").update({"max_marks": total}).eq("id", assessment_id).execute()
                return total
            except Exception as e:
                logger.error(f"Error recalculating assessment total marks in Supabase: {e}")

        # Local mode
        questions = _local_questions.get(assessment_id, [])
        total = sum(float(q.get("max_marks") or 0.0) for q in questions)
        if total > 0:
            for asm in _local_assessments:
                if asm.get("id") == assessment_id:
                    asm["max_marks"] = total
                    break
        return total

    @classmethod
    def add_question(cls, assessment_id: str, question_data: Dict[str, Any]) -> Tuple[bool, Optional[str], str]:
        """Adds a single question with rubric criteria to an assessment."""
        supabase = db_manager.get_supabase(use_service_role=True)
        q_type = cls._detect_q_type(question_data)
        clean_ans = (question_data.get("correct_option") or "A").strip().upper()

        if supabase:
            try:
                cur_res = supabase.table("questions").select("id", count="exact").eq("assessment_id", assessment_id).execute()
                display_order = (cur_res.count if hasattr(cur_res, "count") else len(cur_res.data or [])) + 1
                q_row = cls._build_question_row(question_data, q_type, clean_ans, assessment_id, display_order)
                res = supabase.table("questions").insert(q_row).execute()
                if res.data:
                    q_id = res.data[0]["id"]
                    criteria = question_data.get("criteria", [])
                    if not criteria and q_type == "mcq":
                        criteria = cls._default_mcq_criteria(clean_ans, q_row["max_marks"])
                    cls._insert_criteria(supabase, q_id, criteria)
                    cls.recalculate_total_marks(assessment_id)
                    return True, q_id, "Question added successfully."
            except Exception as e:
                logger.error(f"Error adding question to Supabase: {e}")
                return False, None, str(e)

        # Local mode
        import uuid
        q_id = str(uuid.uuid4())
        q_copy = dict(question_data)
        q_copy.update({"id": q_id, "question_type": q_type})
        if q_type == "mcq" and not q_copy.get("correct_option"):
            q_copy["correct_option"] = "A"
        q_list = _local_questions.setdefault(assessment_id, [])
        q_copy["display_order"] = len(q_list) + 1
        q_list.append(q_copy)
        cls.recalculate_total_marks(assessment_id)
        return True, q_id, "Question added (Local mode)."

    @classmethod
    def update_question(cls, assessment_id: str, question_id: str, question_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Updates an existing question and its marking rubric criteria."""
        supabase = db_manager.get_supabase(use_service_role=True)
        q_type = cls._detect_q_type(question_data)
        clean_ans = (question_data.get("correct_option") or "A").strip().upper()

        if supabase:
            try:
                q_row = cls._build_question_row(question_data, q_type, clean_ans)
                q_row.pop("assessment_id", None)
                q_row.pop("display_order", None)
                q_row["question_number"] = question_data.get("question_number", "1")
                if question_data.get("question_image_url"):
                    q_row["question_image_url"] = question_data["question_image_url"]

                supabase.table("questions").update(q_row).eq("id", question_id).execute()

                criteria = question_data.get("criteria", [])
                if not criteria and q_type == "mcq":
                    criteria = cls._default_mcq_criteria(clean_ans, q_row["max_marks"])
                if criteria:
                    supabase.table("marking_criteria").delete().eq("question_id", question_id).execute()
                    cls._insert_criteria(supabase, question_id, criteria)
                cls.recalculate_total_marks(assessment_id)
                return True, "Question updated successfully."
            except Exception as e:
                logger.error(f"Error updating question: {e}")
                return False, str(e)

        # Local mode
        if assessment_id in _local_questions:
            for q in _local_questions[assessment_id]:
                if q.get("id") == question_id:
                    q.update({
                        "question_number": question_data.get("question_number", q.get("question_number")),
                        "question_type": q_type,
                        "option_a": question_data.get("option_a", ""),
                        "option_b": question_data.get("option_b", ""),
                        "option_c": question_data.get("option_c", ""),
                        "option_d": question_data.get("option_d", ""),
                        "correct_option": clean_ans if q_type == "mcq" else None,
                        "question_text": question_data.get("question_text", q.get("question_text")),
                        "max_marks": float(question_data.get("max_marks", 1.0 if q_type == "mcq" else 5.0)),
                        "model_answer": question_data.get("model_answer", q.get("model_answer")),
                        "acceptable_alternatives": question_data.get("acceptable_alternatives", q.get("acceptable_alternatives")),
                        "difficulty": question_data.get("difficulty", q.get("difficulty")),
                        "requires_diagram": bool(question_data.get("requires_diagram", False)),
                        "requires_numerical": bool(question_data.get("requires_numerical", False)),
                    })
                    if question_data.get("question_image_url"):
                        q["question_image_url"] = question_data["question_image_url"]
                    if question_data.get("criteria"):
                        q["marking_criteria"] = q["criteria"] = question_data["criteria"]
                    cls.recalculate_total_marks(assessment_id)
                    return True, "Question updated (Local mode)."
            return False, "Question not found."
        return False, "Assessment not found."

    @classmethod
    def delete_question(cls, assessment_id: str, question_id: str) -> Tuple[bool, str]:
        """Deletes a question from an assessment."""
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                supabase.table("questions").delete().eq("id", question_id).execute()
                cls.recalculate_total_marks(assessment_id)
                return True, "Question deleted successfully."
            except Exception as e:
                logger.error(f"Error deleting question: {e}")
                return False, str(e)

        if assessment_id in _local_questions:
            _local_questions[assessment_id] = [q for q in _local_questions[assessment_id] if q.get("id") != question_id]
            cls.recalculate_total_marks(assessment_id)
            return True, "Question deleted (Local mode)."
        return False, "Assessment not found."

    @classmethod
    def add_questions_bulk(cls, assessment_id: str, questions: List[Dict[str, Any]]) -> Tuple[bool, int, str]:
        """Bulk adds multiple parsed questions to an assessment."""
        if not questions:
            return False, 0, "No questions provided."

        count = 0
        for q in questions:
            ok, _, _ = cls.add_question(assessment_id, q)
            if ok:
                count += 1

        if count > 0:
            cls.recalculate_total_marks(assessment_id)

        return True, count, f"Successfully imported {count} question(s)."

    @classmethod
    def _save_questions(cls, supabase, assessment_id: str, questions: List[Dict[str, Any]]):
        """Bulk-inserts questions by delegating to add_question (single source of truth)."""
        for q in questions:
            cls.add_question(assessment_id, q)

    @classmethod
    def delete_assessment(cls, assessment_id: str) -> Tuple[bool, str]:
        """Deletes an assessment if it has no submitted student scripts."""
        from services.submission_service import SubmissionService
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                # Check for submissions
                sub_res = supabase.table("answer_submissions").select("id", count="exact").eq("assessment_id", assessment_id).in_("status", ["submitted", "evaluated", "approved"]).execute()
                count = sub_res.count if hasattr(sub_res, "count") else len(sub_res.data or [])
                if count > 0:
                    return False, f"Cannot delete assessment: {count} student submissions already exist. Unpublish or archive it instead."

                supabase.table("assessments").delete().eq("id", assessment_id).execute()
                return True, "Assessment deleted successfully."
            except Exception as e:
                logger.error(f"Error deleting assessment: {e}")
                return False, str(e)

        # Local mode
        global _local_assessments
        # Check if local submissions exist
        has_subs = False
        from services.submission_service import _local_submissions
        for sub in _local_submissions.values():
            if sub.get("assessment_id") == assessment_id and sub.get("status") in ("submitted", "evaluated", "approved"):
                has_subs = True
                break

        if has_subs:
            return False, "Cannot delete assessment: student submissions already exist."

        _local_assessments = [a for a in _local_assessments if a["id"] != assessment_id]
        if assessment_id in _local_questions:
            del _local_questions[assessment_id]
        return True, "Assessment deleted."

    @classmethod
    def toggle_publish(cls, assessment_id: str, publish: bool) -> Tuple[bool, str]:
        """Publishes or unpublishes an assessment."""
        supabase = db_manager.get_supabase(use_service_role=True)
        status_val = "published" if publish else "draft"
        if supabase:
            try:
                supabase.table("assessments").update({
                    "status": status_val,
                    "allow_submissions": publish
                }).eq("id", assessment_id).execute()
                return True, f"Assessment status changed to {status_val}."
            except Exception as e:
                return False, str(e)

        for a in _local_assessments:
            if a["id"] == assessment_id:
                a["status"] = status_val
                a["allow_submissions"] = publish
                return True, f"Assessment status changed to {status_val}."
        return False, "Assessment not found."

    @classmethod
    def _seed_default_assessment(cls):
        """Seeds default BEA MCQ Test and Slip Test for Diploma Students."""
        slip_id = "bea-slip-test-001"
        unit_id = "bea-unit-test-001"

        # 1. OBJECTIVE MCQ TEST: 100% Automatic immediate evaluation without AI
        _local_assessments.append({
            "id": slip_id,
            "title": "MCQ Test 1: BJT Fundamentals & Configurations",
            "assessment_type": "mcq_test",
            "subject_code": "BEA (MEE-105)",
            "unit_topic": "Unit 3: Transistors",
            "assessment_date": str(date.today()),
            "deadline": f"{date.today()} 23:59:00",
            "max_marks": 5.0,
            "pass_percentage": 40.0,
            "instructions": "Answer questions one at a time. Select the best option. Your score and comprehensive technical explanations will be displayed immediately upon submission without AI.",
            "status": "draft",
            "allow_submissions": False,
            "results_published": True
        })

        _local_questions[slip_id] = [
            {
                "id": f"{slip_id}-q1",
                "question_number": "1",
                "display_order": 1,
                "question_type": "mcq",
                "unit_topic": "Transistor Configurations",
                "learning_outcome": "CO3: Understand BJT Operation",
                "question_text": "In a Common Emitter (CE) configuration, what is the phase shift between input and output voltage?",
                "option_a": "0 degrees (in-phase)",
                "option_b": "90 degrees",
                "option_c": "180 degrees (phase reversal)",
                "option_d": "360 degrees",
                "correct_option": "C",
                "max_marks": 1.0,
                "model_answer": "In a Common Emitter amplifier, when input base voltage increases, base current increases, driving more collector current through load resistor RC. This increases the voltage drop across RC, pulling the collector terminal voltage VCE downward. Hence, input and output are 180° out of phase.",
                "difficulty": "easy",
                "requires_diagram": False,
                "requires_numerical": False
            },
            {
                "id": f"{slip_id}-q2",
                "question_number": "2",
                "display_order": 2,
                "question_type": "mcq",
                "unit_topic": "Current Gain Relations",
                "learning_outcome": "CO3: Relate Alpha and Beta",
                "question_text": "The relationship connecting transistor DC current gains alpha (α) and beta (β) is:",
                "option_a": "β = α / (1 - α)",
                "option_b": "α = β / (1 - β)",
                "option_c": "β = α * (1 + α)",
                "option_d": "β = 1 / α",
                "correct_option": "A",
                "max_marks": 1.0,
                "model_answer": "Since total emitter current IE = IB + IC, dividing through by IC gives 1/α = 1/β + 1. Rearranging terms: 1/β = (1 - α)/α, which gives β = α / (1 - α). For example, if α = 0.99, β = 0.99 / 0.01 = 99.",
                "difficulty": "medium",
                "requires_diagram": False,
                "requires_numerical": False
            },
            {
                "id": f"{slip_id}-q3",
                "question_number": "3",
                "display_order": 3,
                "question_type": "mcq",
                "unit_topic": "Operating Regions",
                "learning_outcome": "CO3: Analyze BJT Regions",
                "question_text": "Which BJT operating region is utilized when the transistor operates as a linear faithful amplifier?",
                "option_a": "Saturation Region",
                "option_b": "Active Region",
                "option_c": "Cut-off Region",
                "option_d": "Inverted Breakdown Region",
                "correct_option": "B",
                "max_marks": 1.0,
                "model_answer": "To achieve distortion-free linear amplification, the base-emitter junction must be forward biased and the collector-base junction must be reverse biased. This condition defines the Active Region of operation.",
                "difficulty": "easy",
                "requires_diagram": False,
                "requires_numerical": False
            },
            {
                "id": f"{slip_id}-q4",
                "question_number": "4",
                "display_order": 4,
                "question_type": "mcq",
                "unit_topic": "CC Configuration",
                "learning_outcome": "CO3: Identify Emitter Follower",
                "question_text": "In a Common Collector (CC) amplifier (Emitter Follower), the voltage gain (Av) is approximately:",
                "option_a": "Much greater than 100",
                "option_b": "Zero",
                "option_c": "Slightly less than 1 (Unity Gain)",
                "option_d": "Negative infinity",
                "correct_option": "C",
                "max_marks": 1.0,
                "model_answer": "The Common Collector circuit has 100% negative voltage feedback, resulting in an output voltage that closely follows the input (Av ≈ 1). It offers high input impedance and low output impedance, making it ideal as a buffer stage.",
                "difficulty": "medium",
                "requires_diagram": False,
                "requires_numerical": False
            },
            {
                "id": f"{slip_id}-q5",
                "question_number": "5",
                "display_order": 5,
                "question_type": "mcq",
                "unit_topic": "Thermal Stability",
                "learning_outcome": "CO3: Explain Thermal Runaway",
                "question_text": "The primary cause of thermal runaway in a BJT is the uncontrolled cumulative increase in:",
                "option_a": "Emitter diffusion resistance",
                "option_b": "Reverse collector saturation leakage current (ICBO)",
                "option_c": "Base terminal width",
                "option_d": "Collector breakdown voltage",
                "correct_option": "B",
                "max_marks": 1.0,
                "model_answer": "ICBO is thermally generated and roughly doubles for every 10°C rise in temperature. Since IC = β*IB + (1+β)*ICBO, an increase in ICBO raises IC and junction power dissipation, leading to self-heating and thermal runaway unless negative feedback stabilization (like RE) is provided.",
                "difficulty": "medium",
                "requires_diagram": False,
                "requires_numerical": False
            }
        ]

        # 2. SLIP TEST: Handwritten descriptive answer script upload and evaluation
        _local_assessments.append({
            "id": unit_id,
            "title": "Slip Test 2: BJT Biasing & Small Signal Amplifiers",
            "assessment_type": "slip_test",
            "subject_code": "BEA (MEE-105)",
            "unit_topic": "Unit 3: Transistor Biasing",
            "assessment_date": str(date.today()),
            "deadline": f"{date.today()} 23:59:00",
            "max_marks": 20.0,
            "pass_percentage": 35.0,
            "instructions": "Answer all questions on paper. Draw clear labeled circuit schematics showing terminal currents and polarities. Upload clear handwritten script photos for evaluation.",
            "status": "draft",
            "allow_submissions": False,
            "results_published": True
        })

        _local_questions[unit_id] = [
            {
                "id": f"{unit_id}-q1",
                "question_number": "1",
                "display_order": 1,
                "question_type": "descriptive",
                "question_text": "Draw the circuit diagram of an NPN Transistor in Common Emitter (CE) configuration with input and output biasing circuits. Label all currents (IB, IC, IE) and voltages (VBE, VCE). Refer to the circuit diagram below if needed.",
                "max_marks": 5.0,
                "model_answer": "Circuit showing NPN transistor with emitter grounded/common. Base-emitter forward biased with VBB and resistor RB. Collector-emitter reverse biased with VCC and load resistor RC. Currents indicated: IE = IB + IC.",
                "acceptable_alternatives": "PNP transistor in CE with reversed polarities is also accepted.",
                "difficulty": "medium",
                "requires_diagram": True,
                "requires_numerical": False,
                "question_image_url": "/static/images/bjt_ce_circuit.svg",
                "criteria": [
                    {"criterion_title": "Correct NPN Transistor symbol & terminal identification", "max_marks": 1.5, "description": "Arrow outwards on emitter terminal."},
                    {"criterion_title": "Biasing polarities (Forward Base, Reverse Collector)", "max_marks": 1.5, "description": "VBB forward bias and VCC reverse bias."},
                    {"criterion_title": "Current directions and voltage labels (IB, IC, IE, VBE, VCE)", "max_marks": 2.0, "description": "Correct application of Kirchhoff's Current Law: IE = IB + IC."}
                ]
            },
            {
                "id": f"{unit_id}-q2",
                "question_number": "2",
                "display_order": 2,
                "question_type": "descriptive",
                "question_text": "Define Transistor alpha (α) and beta (β). Derive the mathematical relationship connecting α and β.",
                "max_marks": 5.0,
                "model_answer": "α = IC / IE (Common Base DC current gain). β = IC / IB (Common Emitter DC current gain). Since IE = IB + IC, divide by IC: IE/IC = IB/IC + 1 => 1/α = 1/β + 1 => 1/α = (1 + β)/β => α = β / (1 + β), or β = α / (1 - α).",
                "acceptable_alternatives": "Starting from β definition and substituting IC = α*IE is acceptable.",
                "difficulty": "easy",
                "requires_diagram": False,
                "requires_numerical": False,
                "criteria": [
                    {"criterion_title": "Definitions of α and β with typical ranges", "max_marks": 1.5, "description": "α (0.95 to 0.998) and β (20 to 500)."},
                    {"criterion_title": "Current relation IE = IB + IC", "max_marks": 1.0, "description": "Fundamental transistor current equation."},
                    {"criterion_title": "Step-by-step derivation to β = α / (1 - α)", "max_marks": 2.5, "description": "Algebraic steps clearly written."}
                ]
            },
            {
                "id": f"{unit_id}-q3",
                "question_number": "3",
                "display_order": 3,
                "question_type": "descriptive",
                "question_text": "In a CE transistor amplifier circuit, the base current IB = 25 µA and the collector current IC = 4.75 mA. Calculate: (i) Emitter current IE, and (ii) Current gain beta (β).",
                "max_marks": 5.0,
                "model_answer": "(i) IE = IB + IC = 0.025 mA + 4.75 mA = 4.775 mA. (ii) β = IC / IB = 4.75 mA / 0.025 mA = 190.",
                "acceptable_alternatives": "Calculating β first and then IE = (1+β)*IB is completely acceptable.",
                "difficulty": "medium",
                "requires_diagram": False,
                "requires_numerical": True,
                "criteria": [
                    {"criterion_title": "Formula for Emitter current IE", "max_marks": 1.0, "description": "IE = IB + IC."},
                    {"criterion_title": "Calculation of IE with correct units (4.775 mA)", "max_marks": 1.5, "description": "Accurate unit conversion (µA to mA)."},
                    {"criterion_title": "Formula for β and calculation (190, dimensionless)", "max_marks": 2.5, "description": "β = IC / IB = 190."}
                ]
            },
            {
                "id": f"{unit_id}-q4",
                "question_number": "4",
                "display_order": 4,
                "question_type": "descriptive",
                "question_text": "Sketch the input and output characteristic curves of a BJT in CE configuration. Identify the Cut-off, Active, and Saturation operating regions on the output characteristics.",
                "max_marks": 5.0,
                "model_answer": "Input characteristics: IB vs VBE (diode-like curve with knee voltage ~0.7V for Silicon). Output characteristics: IC vs VCE for different IB curves. Active region: VCE > VCE(sat), horizontal flat lines. Saturation region: steep vertical rise near origin. Cut-off region: region below IB = 0.",
                "acceptable_alternatives": "Germanium knee voltage ~0.3V accepted if noted.",
                "difficulty": "hard",
                "requires_diagram": True,
                "requires_numerical": False,
                "criteria": [
                    {"criterion_title": "Input characteristics curve (IB vs VBE)", "max_marks": 1.5, "description": "Proper axes and knee voltage shown."},
                    {"criterion_title": "Output characteristics family of curves (IC vs VCE)", "max_marks": 1.5, "description": "Multiple IB curves with units."},
                    {"criterion_title": "Identification of Cut-off, Active, and Saturation regions", "max_marks": 2.0, "description": "Clear demarcation of all 3 regions."}
                ]
            }
        ]
