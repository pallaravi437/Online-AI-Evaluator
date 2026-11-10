import logging
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from services.auth_service import AuthService
from services.assessment_service import AssessmentService
from services.submission_service import SubmissionService
from services.image_service import ImageService
from database.db_manager import db_manager

logger = logging.getLogger("bea.student_routes")

def _extract_mcq_answers(form) -> dict:
    """Parses all MCQ answer fields from a submitted form."""
    return {
        key[len("mcq_answer_"):]: val.strip().upper()
        for key, val in form.items()
        if key.startswith("mcq_answer_")
    }

student_bp = Blueprint("student", __name__, url_prefix="/student")

def student_required(f):
    """Decorator to enforce student session."""
    def wrapper(*args, **kwargs):
        if session.get("role") != "student" or not session.get("student_id"):
            flash("Please enter your Name and PIN to log in.", "warning")
            return redirect(url_for("student.login"))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

@student_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = (request.form.get("name") or request.form.get("student_name") or "").strip()
        pin = (request.form.get("pin") or request.form.get("student_pin") or "").strip()
        client_ip = request.remote_addr or ""

        success, student_data, message = AuthService.authenticate_student(name, pin, client_ip)
        if success and student_data:
            session.clear()
            session["role"] = "student"
            session["student_id"] = student_data["id"]
            session["student_name"] = student_data["full_name"]
            session["student_pin"] = student_data["pin"]
            session["student_branch"] = student_data.get("branch", "ECE")
            session.permanent = True
            flash(f"Welcome, {student_data['full_name']}!", "success")
            return redirect(url_for("student.dashboard"))
        else:
            flash(message, "danger")

    recent_submissions = SubmissionService.get_recent_submissions()
    return render_template("login.html", active_tab="student", recent_submissions=recent_submissions)

@student_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been securely logged out.", "info")
    return redirect(url_for("student.login"))

@student_bp.route("/dashboard")
@student_required
def dashboard():
    assessments = AssessmentService.get_all_assessments()
    student_id = session.get("student_id")
    
    # Attach submission status for this student
    for asm in assessments:
        draft = SubmissionService.get_or_create_draft_submission(asm["id"], student_id)
        asm["user_submission_status"] = draft.get("status", "not_started")
        asm["user_submission_id"] = draft.get("id")
        asm["is_locked"] = draft.get("is_locked", False)
        asm["receipt_code"] = draft.get("submission_receipt_code")

    return render_template("student/dashboard.html", assessments=assessments)

@student_bp.route("/assessment/<assessment_id>/upload")
@student_required
def upload_answers(assessment_id):
    """Slip Test handwritten answer script upload interface."""
    student_id = session.get("student_id")
    assessment = AssessmentService.get_assessment_by_id(assessment_id)
    if not assessment:
        flash("Assessment not found.", "danger")
        return redirect(url_for("student.dashboard"))

    # If this is an MCQ Test, redirect to the MCQ test interface
    if assessment.get("assessment_type") == "mcq_test":
        return redirect(url_for("student.take_mcq_test", assessment_id=assessment_id))

    submission = SubmissionService.get_or_create_draft_submission(assessment_id, student_id)
    if submission.get("is_locked", False):
        flash("This Slip Test was already submitted and locked. You can view your receipt or results.", "info")
        return redirect(url_for("student.submission_receipt", submission_id=submission["id"]))

    pages = SubmissionService.get_pages_for_submission(submission["id"])
    
    # Organize pages by question_id
    question_pages = {}
    for p in pages:
        qid = p["question_id"]
        question_pages.setdefault(qid, []).append(p)

    return render_template(
        "student/upload.html",
        assessment=assessment,
        submission=submission,
        question_pages=question_pages
    )

@student_bp.route("/assessment/<assessment_id>/mcq-test")
@student_required
def take_mcq_test(assessment_id):
    """MCQ Test workflow: questions presented one at a time, automatic zero-AI grading."""
    student_id = session.get("student_id")
    assessment = AssessmentService.get_assessment_by_id(assessment_id)
    if not assessment:
        flash("Assessment not found.", "danger")
        return redirect(url_for("student.dashboard"))

    submission = SubmissionService.get_or_create_draft_submission(assessment_id, student_id)
    if submission.get("is_locked", False):
        flash("You have already submitted this MCQ Test. Your score and answer review are below.", "info")
        return redirect(url_for("student.mcq_results", assessment_id=assessment_id))

    return render_template(
        "student/mcq_test.html",
        assessment=assessment,
        submission=submission
    )

@student_bp.route("/assessment/<assessment_id>/submit-mcq", methods=["POST"])
@student_required
def submit_mcq_test(assessment_id):
    """Submits MCQ Test and calculates score automatically using stored database answers (Zero AI / Gemini API)."""
    submission_id = request.form.get("submission_id")
    client_ip = request.remote_addr or ""
    user_agent = request.headers.get("User-Agent", "")

    mcq_answers = _extract_mcq_answers(request.form)

    success, sub, msg = SubmissionService.finalize_submission(
        submission_id=submission_id,
        client_ip=client_ip,
        user_agent=user_agent,
        mcq_answers=mcq_answers
    )
    if success and sub:
        flash("MCQ Test submitted successfully! Your score and answer review are shown below.", "success")
        return redirect(url_for("student.mcq_results", assessment_id=assessment_id))
    else:
        flash(f"Submission error: {msg}", "danger")
        return redirect(url_for("student.dashboard"))

@student_bp.route("/assessment/<assessment_id>/mcq-results")
@student_required
def mcq_results(assessment_id):
    """Immediate MCQ Test Result & Question-by-Question Review with Visual Indicators."""
    student_id = session.get("student_id")
    assessment = AssessmentService.get_assessment_by_id(assessment_id)
    if not assessment:
        flash("Assessment not found.", "danger")
        return redirect(url_for("student.dashboard"))

    sub = SubmissionService.get_or_create_draft_submission(assessment_id, student_id)
    evaluations = SubmissionService.get_evaluations_for_submission(sub["id"])

    # Compute breakdown statistics for immediate result display
    total_q = len(assessment.get("questions", []))
    correct_count = 0
    incorrect_count = 0
    unanswered_count = 0

    for idx, q in enumerate(assessment.get("questions", []), start=1):
        qid = q.get("id") or str(idx)
        ev = evaluations.get(qid, {})
        student_choice = ev.get("student_selected_option", "")
        correct_opt = (q.get("correct_option") or ev.get("correct_option") or "A").strip().upper()
        if not student_choice:
            unanswered_count += 1
        elif student_choice == correct_opt:
            correct_count += 1
        else:
            incorrect_count += 1

    stats = {
        "total_questions": total_q,
        "correct_answers": correct_count,
        "incorrect_answers": incorrect_count,
        "unanswered": unanswered_count
    }

    return render_template(
        "student/mcq_results.html",
        assessment=assessment,
        submission=sub,
        evaluations=evaluations,
        stats=stats
    )

@student_bp.route("/assessment/<assessment_id>/slip-test")
@student_required
def take_slip_test(assessment_id):
    """Preserves existing Slip Test entrypoint, redirecting to the question-wise script upload module."""
    return redirect(url_for("student.upload_answers", assessment_id=assessment_id))

@student_bp.route("/assessment/<assessment_id>/submit-slip-test", methods=["POST"])
@student_required
def submit_slip_test(assessment_id):
    """Alias for finalize_submission for Slip Test scripts."""
    return redirect(url_for("student.finalize_submission"), code=307)


@student_bp.route("/api/upload-page", methods=["POST"])
@student_required
def api_upload_page():
    """AJAX endpoint for question-wise script page upload & optimization."""
    submission_id = request.form.get("submission_id")
    question_id = request.form.get("question_id")
    page_number = int(request.form.get("page_number", 1))
    file = request.files.get("file")

    if not file or not submission_id or not question_id:
        return jsonify({"success": False, "message": "Missing file or required parameters."}), 400

    # Parse optional crop bounds from client Canvas
    crop_data = None
    if "crop_x" in request.form and "crop_w" in request.form:
        try:
            crop_data = {
                "x": float(request.form.get("crop_x")),
                "y": float(request.form.get("crop_y")),
                "width": float(request.form.get("crop_w")),
                "height": float(request.form.get("crop_h"))
            }
        except ValueError:
            crop_data = None

    valid, opt_bytes, metadata, msg = ImageService.validate_and_process_upload(file, crop_data)
    if not valid or not opt_bytes:
        return jsonify({"success": False, "message": msg}), 400

    mime_type = metadata.get("mime_type", "image/jpeg")
    saved, path, save_msg = SubmissionService.save_answer_page(
        submission_id=submission_id,
        question_id=question_id,
        page_number=page_number,
        file_bytes=opt_bytes,
        mime_type=mime_type,
        metadata=metadata
    )

    if not saved:
        return jsonify({"success": False, "message": save_msg}), 400

    return jsonify({
        "success": True,
        "message": "Page saved and compressed successfully.",
        "metadata": metadata,
        "storage_path": path,
        "question_id": question_id,
        "page_number": page_number
    })

@student_bp.route("/submit", methods=["POST"])
@student_required
def finalize_submission():
    submission_id = request.form.get("submission_id")
    client_ip = request.remote_addr or ""
    user_agent = request.headers.get("User-Agent", "")

    mcq_answers = _extract_mcq_answers(request.form)

    success, sub, msg = SubmissionService.finalize_submission(
        submission_id=submission_id,
        client_ip=client_ip,
        user_agent=user_agent,
        mcq_answers=mcq_answers
    )
    if success and sub:
        flash("Your assessment answer script has been officially submitted!", "success")
        return redirect(url_for("student.submission_receipt", submission_id=sub["id"]))
    else:
        flash(f"Submission error: {msg}", "danger")
        return redirect(url_for("student.dashboard"))

@student_bp.route("/receipt/<submission_id>")
@student_required
def submission_receipt(submission_id):
    student_id = session.get("student_id")
    sub = SubmissionService.get_submission_by_id(submission_id)
    if not sub:
        flash("Submission record not found.", "danger")
        return redirect(url_for("student.dashboard"))

    # Security: Verify ownership
    if sub.get("student_id") != student_id:
        flash("Unauthorized access. You can only view your own submission receipt.", "danger")
        return redirect(url_for("student.dashboard"))

    pages = SubmissionService.get_pages_for_submission(submission_id)
    return render_template("student/submission_receipt.html", submission=sub, pages=pages)

@student_bp.route("/results/<assessment_id>")
@student_required
def results(assessment_id):
    student_id = session.get("student_id")
    assessment = AssessmentService.get_assessment_by_id(assessment_id)
    if not assessment:
        flash("Assessment not found.", "danger")
        return redirect(url_for("student.dashboard"))

    # Fetch this student's submission
    sub = SubmissionService.get_or_create_draft_submission(assessment_id, student_id)
    
    # Fetch questions and evaluations
    supabase = db_manager.get_supabase(use_service_role=True)
    evaluations = SubmissionService.get_evaluations_for_submission(sub["id"])
    rechecks = {}

    if supabase:
        try:
            ev_res = supabase.table("question_evaluations").select("*").eq("submission_id", sub["id"]).execute()
            for ev in (ev_res.data or []):
                evaluations[ev["question_id"]] = ev

            rc_res = supabase.table("recheck_requests").select("*").eq("submission_id", sub["id"]).execute()
            for rc in (rc_res.data or []):
                rechecks[rc["question_id"]] = rc
        except Exception as e:
            logger.error(f"Error fetching student results: {e}")
    else:
        # Local mode: pull objections raised for this submission
        from services.submission_service import _local_rechecks
        for rc in _local_rechecks.values():
            if rc.get("submission_id") == sub["id"]:
                rechecks[rc["question_id"]] = rc

    return render_template(
        "student/results.html",
        assessment=assessment,
        submission=sub,
        evaluations=evaluations,
        rechecks=rechecks
    )

@student_bp.route("/recheck", methods=["POST"])
@student_required
def submit_recheck():
    """Allows student to raise an objection on a specific question evaluation."""
    submission_id = request.form.get("submission_id")
    question_id = request.form.get("question_id")
    student_reason = request.form.get("reason", "").strip()
    student_id = session.get("student_id")

    if not student_reason or len(student_reason) < 10:
        flash("Please provide a detailed explanation of your objection (at least 10 characters).", "warning")
        return redirect(request.referrer or url_for("student.dashboard"))

    supabase = db_manager.get_supabase(use_service_role=True)
    if not supabase:
        # Local mode: record the objection in the in-memory store
        from services.submission_service import _local_evaluations, _local_rechecks
        evals = _local_evaluations.get(submission_id, {})
        ev = evals.get(question_id)
        if not ev:
            flash("Evaluation record not found for this question.", "danger")
            return redirect(request.referrer or url_for("student.dashboard"))

        current_marks = ev.get("approved_marks") or ev.get("ai_marks_awarded") or 0.0
        _local_rechecks[f"{submission_id}_{question_id}"] = {
            "submission_id": submission_id,
            "question_id": question_id,
            "student_id": student_id,
            "student_objection_reason": student_reason,
            "marks_before_recheck": current_marks,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        flash("Your recheck objection has been submitted to the lecturer for review.", "success")
        return redirect(request.referrer or url_for("student.dashboard"))

    if supabase:
        try:
            # Check question evaluation
            ev_res = supabase.table("question_evaluations").select("id, approved_marks, ai_marks_awarded").eq(
                "submission_id", submission_id
            ).eq("question_id", question_id).execute()

            if not ev_res.data:
                flash("Evaluation record not found for this question.", "danger")
                return redirect(request.referrer)

            ev = ev_res.data[0]
            current_marks = ev.get("approved_marks") or ev.get("ai_marks_awarded") or 0.0

            recheck_record = {
                "submission_id": submission_id,
                "question_id": question_id,
                "question_evaluation_id": ev["id"],
                "student_id": student_id,
                "student_objection_reason": student_reason,
                "marks_before_recheck": current_marks,
                "status": "pending"
            }

            supabase.table("recheck_requests").upsert(
                recheck_record,
                on_conflict="submission_id,question_id"
            ).execute()

            flash("Your recheck objection has been submitted to the lecturer for review.", "success")
        except Exception as e:
            logger.error(f"Error submitting recheck: {e}")
            flash(f"Error submitting recheck: {str(e)}", "danger")

    return redirect(request.referrer or url_for("student.dashboard"))
