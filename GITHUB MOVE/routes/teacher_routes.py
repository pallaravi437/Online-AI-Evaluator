import os
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import io
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from services.auth_service import AuthService
from services.assessment_service import AssessmentService
from services.submission_service import SubmissionService
from services.student_service import StudentService
from services.image_service import ImageService
from services.ai_service import AIService
from services.analytics_service import AnalyticsService
from services.html_parser_service import HtmlQuestionParser
from services.document_question_parser import DocumentQuestionParser
from services.material_service import MaterialService
from database.db_manager import db_manager
from config import Config

logger = logging.getLogger("bea.teacher_routes")
teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")

def teacher_required(f):
    """Enforces active teacher session."""
    def wrapper(*args, **kwargs):
        if session.get("role") not in ("lecturer", "admin") or not session.get("teacher_id"):
            flash("Please sign in with lecturer credentials.", "warning")
            return redirect(url_for("teacher.login"))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

@teacher_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        client_ip = request.remote_addr or ""

        success, teacher_data, message = AuthService.authenticate_teacher(email, password, client_ip)
        if success and teacher_data:
            session.clear()
            session["role"] = teacher_data.get("role", "lecturer")
            session["teacher_id"] = teacher_data["id"]
            session["teacher_name"] = teacher_data["full_name"]
            session["teacher_email"] = teacher_data["email"]
            session.permanent = True
            flash(f"Welcome back, {teacher_data['full_name']}!", "success")
            return redirect(url_for("teacher.dashboard"))
        else:
            flash(message, "danger")

    recent_submissions = SubmissionService.get_recent_submissions()
    return render_template("login.html", active_tab="teacher", recent_submissions=recent_submissions)

@teacher_bp.route("/logout")
def logout():
    session.clear()
    flash("Lecturer session ended.", "info")
    return redirect(url_for("teacher.login"))

@teacher_bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Lecturer registration — pending admin approval."""
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        subject = request.form.get("subject", "").strip()
        mobile = request.form.get("mobile", "").strip()

        if password != confirm_password:
            flash("Passwords do not match. Please verify.", "danger")
            return render_template("teacher/signup.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("teacher/signup.html")

        from services.lecturer_service import LecturerService
        success, data, message = LecturerService.register_lecturer(
            full_name=full_name,
            username=username,
            email=email,
            password=password,
            subject=subject,
            mobile=mobile
        )
        if success:
            flash(message, "success")
            return redirect(url_for("teacher.login"))
        else:
            flash(message, "danger")

    return render_template("teacher/signup.html")

@teacher_bp.route("/my-settings", methods=["GET", "POST"])
@teacher_required
def my_settings():
    """Lecturer-specific settings for their own API key, model, and dedicated database."""
    from services.lecturer_service import LecturerService
    teacher_id = session.get("teacher_id")

    if request.method == "POST":
        updates = {
            "ai_provider": request.form.get("ai_provider", "gemini").strip(),
            "ai_model": request.form.get("ai_model", "gemini-3.1-flash-lite").strip(),
            "database_type": request.form.get("database_type", "local").strip(),
            "supabase_url": request.form.get("supabase_url", "").strip(),
            "mysql_host": request.form.get("mysql_host", "").strip(),
            "mysql_port": request.form.get("mysql_port", 3306),
            "mysql_user": request.form.get("mysql_user", "").strip(),
            "mysql_database": request.form.get("mysql_database", "").strip(),
        }

        new_key = request.form.get("gemini_api_key", "").strip()
        if new_key and not new_key.startswith("••••"):
            updates["gemini_api_key"] = new_key

        new_sup_key = request.form.get("supabase_key", "").strip()
        if new_sup_key and not new_sup_key.startswith("••••"):
            updates["supabase_key"] = new_sup_key

        new_mysql_pw = request.form.get("mysql_password", "").strip()
        if new_mysql_pw and not new_mysql_pw.startswith("••••"):
            updates["mysql_password"] = new_mysql_pw

        success, msg = LecturerService.update_lecturer_config(teacher_id, updates)
        if success:
            flash("Your dedicated database & AI settings have been saved!", "success")
        else:
            flash(f"Could not save settings: {msg}", "danger")
        return redirect(url_for("teacher.my_settings"))

    config = LecturerService.get_lecturer_config(teacher_id)
    return render_template("teacher/my_settings.html", config=config)

@teacher_bp.route("/dashboard")
@teacher_required
def dashboard():
    """
    Core Teacher Dashboard with 3 primary operational pillars:
    1. Set Questions with Marks
    2. Evaluate Submissions
    3. Result Analysis Dashboard
    """
    teacher_id = session.get("teacher_id")
    assessments = AssessmentService.get_all_assessments(teacher_id=teacher_id)
    students = StudentService.get_all_students(teacher_id=teacher_id)
    
    # Calculate overview metrics across assessments
    total_students = len(students) or 60  # Default to ~60 Diploma batch if DB empty
    selected_asm_id = request.args.get("assessment_id") or (assessments[0]["id"] if assessments else None)
    
    # Compute submission tracker statistics for the selected assessment
    tracker = {
        "total_students": total_students,
        "submitted_count": 0,
        "not_submitted_count": total_students,
        "submission_percentage": 0.0,
        "evaluated_count": 0,
        "approved_count": 0
    }

    submissions_list = []
    selected_assessment = None

    if selected_asm_id:
        selected_assessment = AssessmentService.get_assessment_by_id(selected_asm_id)
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("answer_submissions").select(
                    "*, students(id, pin, full_name, branch, section)"
                ).eq("assessment_id", selected_asm_id).order("submitted_at", desc=True).execute()
                submissions_list = res.data or []
            except Exception as e:
                logger.error(f"Error fetching submissions: {e}")

        # Format timestamps to Asia/Kolkata
        sub_count = len([s for s in submissions_list if s.get("status") in ("submitted", "evaluated", "approved")])
        tracker["submitted_count"] = sub_count
        tracker["not_submitted_count"] = max(0, total_students - sub_count)
        tracker["submission_percentage"] = round((sub_count / max(1, total_students)) * 100, 1)
        tracker["evaluated_count"] = len([s for s in submissions_list if s.get("status") in ("evaluated", "approved")])
        tracker["approved_count"] = len([s for s in submissions_list if s.get("status") == "approved"])

        for s in submissions_list:
            if s.get("submitted_at"):
                try:
                    dt = datetime.fromisoformat(s["submitted_at"].replace("Z", "+00:00"))
                    s["submitted_at_formatted"] = dt.astimezone(Config.TIMEZONE).strftime("%d %b %Y, %I:%M %p")
                except Exception:
                    s["submitted_at_formatted"] = s["submitted_at"]

    return render_template(
        "teacher/dashboard.html",
        assessments=assessments,
        selected_assessment=selected_assessment,
        tracker=tracker,
        submissions=submissions_list,
        total_students=total_students
    )

@teacher_bp.route("/assessments", methods=["GET", "POST"])
@teacher_required
def manage_assessments():
    """Create and manage assessments and questions."""
    filter_type = request.args.get("type")

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        assessment_type = request.form.get("assessment_type", "slip_test")
        unit_topic = request.form.get("unit_topic", "General").strip()
        deadline = request.form.get("deadline") or None
        try:
            max_marks = float(request.form.get("max_marks", 20.0))
        except (ValueError, TypeError):
            max_marks = 20.0
        assessment_date = request.form.get("assessment_date", "")
        instructions = request.form.get("instructions", "")
        try:
            pass_pct = float(request.form.get("pass_percentage", 35.0))
        except (ValueError, TypeError):
            pass_pct = 35.0
        teacher_id = session.get("teacher_id")

        # Check if an HTML questions file was uploaded to auto-populate questions
        parsed_questions = None
        q_file = request.files.get("questions_file")
        if q_file and q_file.filename:
            try:
                raw_bytes = q_file.read()
                html_text = raw_bytes.decode("utf-8", errors="ignore")
                parsed_questions = HtmlQuestionParser.parse_html(html_text)
                if parsed_questions:
                    total_q_marks = sum(float(q.get("max_marks", 1.0)) for q in parsed_questions)
                    if total_q_marks > 0:
                        max_marks = total_q_marks
                    # If MCQ was chosen or questions have options, enforce mcq_test
                    if any(q.get("question_type") == "mcq" for q in parsed_questions):
                        if assessment_type in ("slip_test", "assignment"):
                            assessment_type = "mcq_test"
            except Exception as e:
                logger.error(f"Error parsing HTML questions file: {e}")
                flash(f"Warning: Could not parse HTML file: {e}", "warning")

        success, new_id, msg = AssessmentService.create_assessment(
            title=title,
            assessment_type=assessment_type,
            max_marks=max_marks,
            assessment_date=assessment_date,
            instructions=instructions,
            pass_percentage=pass_pct,
            questions=parsed_questions,
            created_by_id=teacher_id,
            unit_topic=unit_topic,
            deadline=deadline
        )
        if success:
            if parsed_questions:
                flash(f"Assessment '{title}' created successfully with {len(parsed_questions)} questions auto-imported from HTML file!", "success")
                return redirect(url_for("teacher.manage_questions", assessment_id=new_id))
            flash("Assessment created successfully!", "success")
            return redirect(url_for("teacher.manage_questions", assessment_id=new_id))
        else:
            flash(f"Failed to create assessment: {msg}", "danger")

    assessments = AssessmentService.get_all_assessments(teacher_id=session.get("teacher_id"))
    return render_template("teacher/assessments.html", assessments=assessments, filter_type=filter_type)

@teacher_bp.route("/assessment/<assessment_id>/toggle-publish", methods=["POST"])
@teacher_required
def toggle_assessment_publish(assessment_id):
    """Toggles assessment status between published and draft."""
    publish_val = request.form.get("publish") == "1"
    success, msg = AssessmentService.toggle_publish(assessment_id, publish_val)
    if success:
        flash(msg, "success")
    else:
        flash(f"Could not change publish status: {msg}", "danger")
    return redirect(request.referrer or url_for("teacher.manage_assessments"))

@teacher_bp.route("/assessment/<assessment_id>/delete", methods=["POST"])
@teacher_required
def delete_assessment(assessment_id):
    """Deletes an assessment if zero students have submitted."""
    success, msg = AssessmentService.delete_assessment(assessment_id)
    if success:
        flash("Assessment deleted successfully.", "info")
    else:
        flash(f"Could not delete assessment: {msg}", "danger")
    return redirect(url_for("teacher.manage_assessments"))

@teacher_bp.route("/assessment/<assessment_id>/questions")
@teacher_required
def manage_questions(assessment_id):
    """View and manage questions and marking rubrics for an assessment."""
    assessment = AssessmentService.get_assessment_by_id(assessment_id)
    if not assessment:
        flash("Assessment not found.", "danger")
        return redirect(url_for("teacher.manage_assessments"))
    materials = MaterialService.get_all_materials()
    return render_template("teacher/manage_questions.html", assessment=assessment, materials=materials)

def _save_question_image(img_file, assessment_id: str, q_num: str) -> str:
    """Validates, compresses and saves a question reference image. Returns URL or empty string."""
    if not img_file or not img_file.filename:
        return ""
    valid, opt_bytes, metadata, _ = ImageService.validate_and_process_upload(img_file)
    if not valid or not opt_bytes:
        return ""
    safe_num = "".join(c for c in q_num if c.isalnum()) or "1"
    mime = (metadata or {}).get("mime_type", "image/jpeg")
    ext = ".svg" if mime == "image/svg+xml" else (".png" if mime == "image/png" else ".jpg")
    filename = f"q_{assessment_id[:8]}_{safe_num}_{int(datetime.now().timestamp())}{ext}"
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads", "questions")
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, filename), "wb") as fh:
        fh.write(opt_bytes)
    return f"/static/uploads/questions/{filename}"


def _parse_question_form(assessment_id: str) -> dict:
    """Parses all question fields from request.form — shared by add and edit routes."""
    q_num = request.form.get("question_number", "").strip()
    q_type = request.form.get("question_type", "descriptive").strip().lower()
    q_text = request.form.get("question_text", "").strip()
    try:
        max_m = float(request.form.get("max_marks", 1.0 if q_type == "mcq" else 5.0))
    except (ValueError, TypeError):
        max_m = 1.0 if q_type == "mcq" else 5.0
    model_ans = request.form.get("model_answer", "").strip()
    alternatives = request.form.get("acceptable_alternatives", "").strip()
    difficulty = request.form.get("difficulty", "medium").strip().lower()
    req_diagram = bool(request.form.get("requires_diagram"))
    req_numerical = bool(request.form.get("requires_numerical"))

    # Extract dynamic rubric criteria
    crit_titles = request.form.getlist("criterion_title") or request.form.getlist("criterion_title[]")
    crit_marks = request.form.getlist("criterion_marks") or request.form.getlist("criterion_marks[]")
    descs = request.form.getlist("criterion_desc") or request.form.getlist("criterion_desc[]")

    criteria = []
    for i in range(len(crit_titles)):
        t = crit_titles[i].strip()
        if t:
            try:
                m = float(crit_marks[i]) if i < len(crit_marks) else 1.0
            except (IndexError, ValueError):
                m = 1.0
            d = descs[i].strip() if i < len(descs) else ""
            criteria.append({"criterion_title": t, "max_marks": m, "description": d})

    # MCQ options
    option_a = request.form.get("option_a", "").strip()
    option_b = request.form.get("option_b", "").strip()
    option_c = request.form.get("option_c", "").strip()
    option_d = request.form.get("option_d", "").strip()
    correct_option = (request.form.get("correct_option") or "A").strip().upper()

    image_url = _save_question_image(request.files.get("question_image"), assessment_id, q_num)

    q_data = {
        "question_number": q_num,
        "question_type": q_type,
        "question_text": q_text,
        "max_marks": max_m,
        "model_answer": model_ans,
        "acceptable_alternatives": alternatives,
        "difficulty": difficulty,
        "requires_diagram": req_diagram,
        "requires_numerical": req_numerical,
        "option_a": option_a,
        "option_b": option_b,
        "option_c": option_c,
        "option_d": option_d,
        "correct_option": correct_option,
        "criteria": criteria
    }
    if image_url:
        q_data["question_image_url"] = image_url

    return q_data


@teacher_bp.route("/assessment/<assessment_id>/add-question", methods=["POST"])
@teacher_required
def add_question(assessment_id):
    """Adds a new question with marking criteria to an assessment."""
    q_data = _parse_question_form(assessment_id)
    success, q_id, msg = AssessmentService.add_question(assessment_id, q_data)
    if success:
        q_num = q_data.get("question_number") or "?"
        flash(f"Question {q_num} and its marking rubric added successfully!", "success")
    else:
        flash(f"Failed to add question: {msg}", "danger")
    return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))


@teacher_bp.route("/assessment/<assessment_id>/edit-question/<question_id>", methods=["POST"])
@teacher_required
def edit_question(assessment_id, question_id):
    """Updates an existing question, attached diagram/figure, and rubric criteria."""
    q_data = _parse_question_form(assessment_id)
    success, msg = AssessmentService.update_question(assessment_id, question_id, q_data)
    if success:
        q_num = q_data.get("question_number") or "?"
        flash(f"Question {q_num} and its marking rubric updated successfully!", "success")
    else:
        flash(f"Failed to update question: {msg}", "danger")
    return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))


@teacher_bp.route("/assessment/<assessment_id>/upload-html-questions", methods=["POST"])
@teacher_required
def upload_html_questions(assessment_id):
    """Bulk imports questions from an uploaded HTML file or pasted HTML text into an existing assessment."""
    file = request.files.get("questions_file")
    pasted_html = request.form.get("pasted_html", "").strip()

    content = ""
    if file and file.filename:
        try:
            content = file.read().decode("utf-8", errors="ignore")
        except Exception as e:
            flash(f"Error reading uploaded file: {e}", "danger")
            return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))
    elif pasted_html:
        content = pasted_html
    else:
        flash("Please upload an HTML file or paste HTML content containing questions.", "warning")
        return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))

    try:
        parsed = HtmlQuestionParser.parse_html(content)
        if not parsed:
            flash("Could not detect any questions in the provided HTML. Ensure questions are numbered (e.g. '1. Define...') or structured with HTML tags (div class='question'). Supports both MCQ and descriptive formats.", "danger")
            return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))

        ok, count, msg = AssessmentService.add_questions_bulk(assessment_id, parsed)
        if ok:
            flash(f"Successfully auto-imported {count} questions from HTML! You can review or edit them below.", "success")
        else:
            flash(f"Error importing questions: {msg}", "danger")
    except Exception as e:
        logger.error(f"Error importing HTML questions: {e}")
        flash(f"Failed to parse HTML: {str(e)}", "danger")

    return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))


@teacher_bp.route("/assessment/<assessment_id>/ai-generate-questions", methods=["POST"])
@teacher_required
def ai_generate_questions(assessment_id):
    """Automatically generates questions based on subject, lesson name, topics, keywords, count, and marks, and imports them."""
    subject = request.form.get("subject", "").strip()
    lesson_name = request.form.get("lesson_name", "").strip()
    topics = request.form.get("topics", "").strip()
    keywords = request.form.get("keywords", "").strip()
    q_type = request.form.get("question_type", "descriptive")
    difficulty = request.form.get("difficulty", "medium")
    material_id = request.form.get("material_id", "").strip()

    try:
        num_questions = int(request.form.get("num_questions", 5))
    except (ValueError, TypeError):
        num_questions = 5

    try:
        marks_per_q = float(request.form.get("marks_per_question", 5.0))
    except (ValueError, TypeError):
        marks_per_q = 5.0

    material_context = ""
    if material_id:
        mat = MaterialService.get_material_by_id(material_id)
        if mat:
            s_cont = mat.get("structured_content") or {}
            parts = [f"### Reference Material: {mat.get('title', '')} (Type: {mat.get('material_type', 'syllabus')})"]
            if s_cont.get("summary"):
                parts.append(f"Summary: {s_cont['summary']}")
            if s_cont.get("units"):
                parts.append(f"Units/Topics: {', '.join(str(u) for u in s_cont['units'])}")
            if s_cont.get("key_definitions"):
                parts.append(f"Key Definitions: {', '.join(str(d) for d in s_cont['key_definitions'][:10])}")
            if s_cont.get("key_formulas"):
                parts.append(f"Formulas: {', '.join(str(f) for f in s_cont['key_formulas'][:10])}")
            if s_cont.get("learning_outcomes"):
                parts.append(f"Learning Outcomes: {', '.join(str(o) for o in s_cont['learning_outcomes'][:6])}")
            snippet = (mat.get("extracted_text") or "")[:2000]
            if snippet:
                parts.append(f"Content excerpt:\n{snippet}")
            material_context = "\n".join(parts)

    if not material_context:
        material_context = MaterialService.get_structured_context_for_assessment(
            assessment_id=assessment_id,
            subject_code=subject,
            unit_topic=lesson_name
        )

    if not lesson_name and not topics and not subject and not material_context:
        flash("Please enter at least a Subject, Lesson / Unit Name, Topic, or select a Syllabus/Material to generate questions.", "warning")
        return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))

    success, questions, msg = AIService.generate_assessment_questions(
        lesson_name=lesson_name,
        topics=topics,
        assessment_type=q_type,
        num_questions=num_questions,
        marks_per_question=marks_per_q,
        difficulty=difficulty,
        subject=subject,
        keywords=keywords,
        material_context=material_context
    )

    if not success or not questions:
        flash(f"Could not generate questions: {msg}", "danger")
        return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))

    ok, count, save_msg = AssessmentService.add_questions_bulk(assessment_id, questions)
    if ok:
        flash(f"✨ Successfully generated and added {count} questions for '{lesson_name or topics}'!", "success")
    else:
        flash(f"Questions generated but could not save: {save_msg}", "danger")

    return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))


@teacher_bp.route("/assessment/<assessment_id>/ai-suggest-topics", methods=["POST"])
@teacher_required
def ai_suggest_topics(assessment_id):
    """Suggests subtopics, keywords, and clarifying questions for the lecturer."""
    data = request.get_json() or {}
    subject = data.get("subject", "").strip()
    lesson_name = data.get("lesson_name", "").strip()
    topics = data.get("topics", "").strip()

    result = AIService.suggest_subtopics_and_keywords(
        subject=subject,
        lesson_name=lesson_name,
        topics=topics
    )
    return jsonify(result)


@teacher_bp.route("/assessment/<assessment_id>/import-google-forms", methods=["POST"])
@teacher_required
def import_google_forms(assessment_id):
    """Extracts questions and options from a Google Forms URL for lecturer verification."""
    form_url = request.form.get("form_url", "").strip()
    if not form_url:
        flash("Please enter a valid Google Forms URL.", "warning")
        return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))

    success, questions, msg = HtmlQuestionParser.parse_google_forms_url(form_url)
    if not success or not questions:
        flash(msg, "danger")
        return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))

    ok, count, save_msg = AssessmentService.add_questions_bulk(assessment_id, questions)
    if ok:
        flash(f"{msg} ({count} questions successfully imported into this assessment).", "success")
    else:
        flash(f"Extracted questions but could not save: {save_msg}", "danger")

    return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))


@teacher_bp.route("/assessment/<assessment_id>/delete-question/<question_id>", methods=["POST"])
@teacher_required
def delete_question(assessment_id, question_id):
    """Deletes a question from an assessment."""
    success, msg = AssessmentService.delete_question(assessment_id, question_id)
    if success:
        flash("Question deleted.", "info")
    else:
        flash(f"Could not delete question: {msg}", "danger")
    return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))


@teacher_bp.route("/assessment/<assessment_id>/parse-document-questions", methods=["POST"])
@teacher_required
def parse_document_questions(assessment_id):
    """
    Parses questions from an uploaded document (PDF, Word) or image (JPG, PNG, WEBP).
    Filters out noise (college headers, exam instructions, student fields) and returns
    structured questions for lecturer confirmation.
    """
    file = request.files.get("document_file")
    if not file or not file.filename:
        return jsonify({"success": False, "message": "No file was uploaded."}), 400

    filename = file.filename
    if not DocumentQuestionParser.is_allowed_file(filename):
        return jsonify({
            "success": False,
            "message": f"Unsupported file type '{filename}'. Please upload a PDF, Word document (.docx/.doc), or Image (.jpg, .png, .webp)."
        }), 400

    try:
        file_bytes = file.read()
        default_marks = float(request.form.get("default_marks", 5.0) or 5.0)
    except (ValueError, TypeError):
        default_marks = 5.0

    subject = request.form.get("subject", "").strip()
    if not subject:
        asm = AssessmentService.get_assessment_by_id(assessment_id)
        if asm:
            subject = f"{asm.get('subject_code', '')} {asm.get('title', '')}".strip()

    success, questions, msg = DocumentQuestionParser.parse_uploaded_file(
        file_bytes=file_bytes,
        filename=filename,
        default_marks=default_marks,
        subject=subject
    )

    return jsonify({
        "success": success,
        "filename": filename,
        "questions": questions,
        "count": len(questions),
        "message": msg
    })


@teacher_bp.route("/assessment/<assessment_id>/confirm-document-questions", methods=["POST"])
@teacher_required
def confirm_document_questions(assessment_id):
    """
    Saves the questions confirmed/edited by the lecturer into the assessment.
    """
    data = request.get_json() or {}
    questions = data.get("questions", [])
    if not questions or not isinstance(questions, list):
        return jsonify({"success": False, "message": "No questions provided to save."}), 400

    cleaned_questions = []
    for idx, q in enumerate(questions, 1):
        q_text = str(q.get("question_text", "")).strip()
        if not q_text:
            continue
        q_num = str(q.get("question_number", idx)).strip() or str(idx)
        q_type = str(q.get("question_type", "descriptive")).strip().lower()
        if q_type not in ("mcq", "descriptive"):
            q_type = "descriptive"

        try:
            max_m = float(q.get("max_marks", 1.0 if q_type == "mcq" else 5.0))
        except (ValueError, TypeError):
            max_m = 1.0 if q_type == "mcq" else 5.0

        item = {
            "question_number": q_num,
            "question_type": q_type,
            "question_text": q_text,
            "max_marks": max_m,
            "model_answer": str(q.get("model_answer", "")).strip(),
            "difficulty": str(q.get("difficulty", "medium")).strip().lower(),
            "requires_diagram": bool(q.get("requires_diagram", False)),
            "requires_numerical": bool(q.get("requires_numerical", False))
        }

        if q_type == "mcq":
            item["option_a"] = str(q.get("option_a", "")).strip()
            item["option_b"] = str(q.get("option_b", "")).strip()
            item["option_c"] = str(q.get("option_c", "")).strip()
            item["option_d"] = str(q.get("option_d", "")).strip()
            clean_opt = str(q.get("correct_option", "A")).strip().upper()
            item["correct_option"] = clean_opt if clean_opt in ("A", "B", "C", "D") else "A"
        else:
            criteria = q.get("criteria", [])
            if criteria and isinstance(criteria, list):
                item["criteria"] = criteria

        cleaned_questions.append(item)

    if not cleaned_questions:
        return jsonify({"success": False, "message": "No valid questions were submitted."}), 400

    ok, count, msg = AssessmentService.add_questions_bulk(assessment_id, cleaned_questions)
    return jsonify({
        "success": ok,
        "count": count,
        "message": msg or f"Successfully added {count} questions to the assessment."
    })


@teacher_bp.route("/students", methods=["GET", "POST"])
@teacher_required
def manage_students():
    """Student roster management with Excel/CSV preview & validation."""
    teacher_id = session.get("teacher_id")
    students = StudentService.get_all_students(teacher_id=teacher_id)
    preview_data = None
    invalid_rows = None

    if request.method == "POST":
        file = request.files.get("roster_file")
        if file and file.filename:
            try:
                content = file.read()
                valid, invalid = StudentService.parse_file(content, file.filename)
                session["pending_roster_import"] = valid
                preview_data = valid
                invalid_rows = invalid
                flash(f"Parsed {len(valid)} valid student records. Please review and confirm import.", "info")
            except Exception as e:
                flash(f"Error parsing student file: {str(e)}", "danger")

    return render_template(
        "teacher/students.html",
        students=students,
        preview_data=preview_data,
        invalid_rows=invalid_rows
    )

@teacher_bp.route("/students/confirm-import", methods=["POST"])
@teacher_required
def confirm_student_import():
    """Commits parsed preview students to Supabase or tenant database."""
    pending = session.pop("pending_roster_import", None)
    if not pending:
        flash("No pending student import found. Please upload the file again.", "warning")
        return redirect(url_for("teacher.manage_students"))

    teacher_id = session.get("teacher_id")
    summary = StudentService.import_students(pending, teacher_id=teacher_id)
    flash(f"Import complete! {summary['inserted']} students successfully saved.", "success")
    return redirect(url_for("teacher.manage_students"))

@teacher_bp.route("/students/edit/<student_id>", methods=["POST"])
@teacher_required
def edit_student(student_id):
    """Updates an existing student profile from the Registered Students Roster."""
    data = {
        "pin": request.form.get("pin", "").strip().upper(),
        "full_name": request.form.get("full_name", "").strip(),
        "academic_year": request.form.get("academic_year", "2026-2027").strip(),
        "semester": request.form.get("semester", "Semester 1").strip(),
        "branch": request.form.get("branch", "ECE").strip(),
        "section": request.form.get("section", "A").strip(),
        "contact_phone": request.form.get("contact_phone", "").strip(),
        "contact_email": request.form.get("contact_email", "").strip(),
        "is_active": request.form.get("is_active") == "1"
    }
    teacher_id = session.get("teacher_id")
    success, msg = StudentService.update_student(student_id, data, teacher_id=teacher_id)
    if success:
        flash(f"Student profile for {data['pin'] or student_id} updated successfully.", "success")
    else:
        flash(f"Failed to update student: {msg}", "danger")
    return redirect(url_for("teacher.manage_students"))
    if success:
        flash(f"Student profile for {data['pin'] or student_id} updated successfully.", "success")
    else:
        flash(f"Failed to update student: {msg}", "danger")
    return redirect(url_for("teacher.manage_students"))

@teacher_bp.route("/students/edit-preview/<int:row_idx>", methods=["POST"])
@teacher_required
def edit_preview_student(row_idx):
    """Updates a row in the pending import preview list before committing."""
    pending = session.get("pending_roster_import", [])
    if 0 <= row_idx < len(pending):
        pending[row_idx]["pin"] = request.form.get("pin", pending[row_idx].get("pin", "")).strip().upper()
        pending[row_idx]["full_name"] = request.form.get("full_name", pending[row_idx].get("full_name", "")).strip()
        pending[row_idx]["academic_year"] = request.form.get("academic_year", pending[row_idx].get("academic_year", "2026-2027")).strip()
        pending[row_idx]["semester"] = request.form.get("semester", pending[row_idx].get("semester", "Semester 1")).strip()
        pending[row_idx]["branch"] = request.form.get("branch", pending[row_idx].get("branch", "ECE")).strip()
        pending[row_idx]["section"] = request.form.get("section", pending[row_idx].get("section", "A")).strip()
        pending[row_idx]["contact_phone"] = request.form.get("contact_phone", pending[row_idx].get("contact_phone", "")).strip()
        pending[row_idx]["contact_email"] = request.form.get("contact_email", pending[row_idx].get("contact_email", "")).strip()
        session["pending_roster_import"] = pending
        session.modified = True
        flash(f"Preview record #{row_idx + 1} ({pending[row_idx]['pin']}) updated.", "info")
    else:
        flash("Invalid preview row index.", "warning")
    return redirect(url_for("teacher.manage_students"))

@teacher_bp.route("/students/delete-batch", methods=["POST"])
@teacher_required
def delete_students_batch():
    """Deletes one or multiple selected students from Registered Students Roster."""
    selected_ids = request.form.getlist("selected_students")
    if not selected_ids:
        single_id = request.form.get("student_id")
        if single_id:
            selected_ids = [single_id]

    if not selected_ids:
        flash("Please select at least one student to delete.", "warning")
        return redirect(url_for("teacher.manage_students"))

    teacher_id = session.get("teacher_id")
    success, count, msg = StudentService.delete_students(selected_ids, teacher_id=teacher_id)
    if success:
        flash(f"Successfully deleted {count} student(s) from roster.", "info")
    else:
        flash(f"Failed to delete students: {msg}", "danger")
    return redirect(url_for("teacher.manage_students"))

@teacher_bp.route("/students/template")
@teacher_required
def download_student_template():
    """Generates and downloads the official student roster Excel template."""
    content = StudentService.generate_excel_template()
    return send_file(
        io.BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="BEA_Student_Roster_Template.xlsx"
    )

@teacher_bp.route("/students/template/word")
@teacher_required
def download_student_word_template():
    """Generates and downloads the official student roster Word (.docx) template."""
    content = StudentService.generate_word_template()
    return send_file(
        io.BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name="BEA_Student_Roster_Template.docx"
    )

@teacher_bp.route("/evaluate/<submission_id>")
@teacher_required
def evaluate_submission(submission_id):
    """
    Displays the submission script pages side-by-side with
    the AI evaluation and mark approval interface.
    """
    sub = SubmissionService.get_submission_by_id(submission_id)
    if not sub:
        flash("Submission not found.", "danger")
        return redirect(url_for("teacher.dashboard"))

    assessment = AssessmentService.get_assessment_by_id(sub["assessment_id"])
    pages = SubmissionService.get_pages_for_submission(submission_id)

    # Fetch existing evaluations
    supabase = db_manager.get_supabase(use_service_role=True)
    evaluations = {}
    if supabase:
        try:
            ev_res = supabase.table("question_evaluations").select("*").eq("submission_id", submission_id).execute()
            for ev in (ev_res.data or []):
                evaluations[ev["question_id"]] = ev
        except Exception as e:
            logger.error(f"Error loading question evaluations: {e}")

    # Group pages by question
    question_pages = {}
    for p in pages:
        question_pages.setdefault(p["question_id"], []).append(p)

    return render_template(
        "teacher/evaluation_view.html",
        submission=sub,
        assessment=assessment,
        pages=pages,
        question_pages=question_pages,
        evaluations=evaluations
    )

@teacher_bp.route("/api/run-ai-evaluation", methods=["POST"])
@teacher_required
def api_run_ai_evaluation():
    """
    Triggers Gemini Multimodal AI evaluation for a submission or specific question.
    Only executes when teacher explicitly clicks EVALUATE.
    """
    data = request.get_json() or {}
    submission_id = data.get("submission_id")
    target_question_id = data.get("question_id")  # Optional: evaluate single question
    teacher_id = session.get("teacher_id")

    sub = SubmissionService.get_submission_by_id(submission_id)
    if not sub:
        return jsonify({"success": False, "message": "Submission record not found."}), 404

    assessment = AssessmentService.get_assessment_by_id(sub["assessment_id"])
    all_pages = SubmissionService.get_pages_for_submission(submission_id)

    questions_to_eval = assessment.get("questions", [])
    if target_question_id:
        questions_to_eval = [q for q in questions_to_eval if q["id"] == target_question_id]

    supabase = db_manager.get_supabase(use_service_role=True)
    results = {}
    total_ai_marks = 0.0

    material_ctx = ""
    try:
        material_ctx = MaterialService.get_structured_context_for_assessment(
            assessment_id=sub.get("assessment_id"),
            subject_code=assessment.get("subject_code") or assessment.get("subject"),
            unit_topic=assessment.get("unit_topic") or assessment.get("unit")
        )
    except Exception as e:
        logger.warning(f"Could not load material context for assessment: {e}")

    for q in questions_to_eval:
        qid = q["id"]
        q_pages = [p for p in all_pages if p.get("question_id") == qid]
        criteria = q.get("marking_criteria") or q.get("criteria", [])

        # Call Gemini AI
        eval_result = AIService.evaluate_question(
            question_data=q,
            marking_criteria=criteria,
            page_images=q_pages,
            material_context=material_ctx
        )

        awarded_m = float(eval_result.get("marks_awarded", 0.0))
        total_ai_marks += awarded_m

        # Save to question_evaluations table in Supabase
        if supabase:
            try:
                ev_row = {
                    "submission_id": submission_id,
                    "question_id": qid,
                    "ai_marks_awarded": awarded_m,
                    "max_marks": float(q.get("max_marks", 5.0)),
                    "ai_criterion_breakdown": eval_result.get("criterion_breakdown", []),
                    "ai_correct_concepts": eval_result.get("correct_concepts", []),
                    "ai_missing_concepts": eval_result.get("missing_concepts", []),
                    "ai_technical_mistakes": eval_result.get("technical_mistakes", []),
                    "ai_marks_deducted_reasons": eval_result.get("marks_deducted_reasons", []),
                    "ai_correct_explanation": eval_result.get("correct_explanation", ""),
                    "ai_model_answer_reference": eval_result.get("model_answer_reference", ""),
                    "ai_student_feedback": eval_result.get("student_feedback", ""),
                    "ai_confidence_level": eval_result.get("confidence_level", "high"),
                    "ai_lecturer_review_warning": eval_result.get("lecturer_review_warning", False),
                    "ai_warning_reason": eval_result.get("warning_reason"),
                    "approval_status": "pending_review",
                    "approved_marks": awarded_m  # Suggested marks pre-filled for teacher approval
                }
                supabase.table("question_evaluations").upsert(
                    ev_row,
                    on_conflict="submission_id,question_id"
                ).execute()
            except Exception as e:
                logger.error(f"Error saving question evaluation to Supabase: {e}")

        results[qid] = eval_result

    # Update submission status to 'evaluated' and update ai_total_marks
    if supabase:
        try:
            supabase.table("answer_submissions").update({
                "status": "evaluated",
                "ai_total_marks": total_ai_marks
            }).eq("id", submission_id).execute()
        except Exception:
            pass

    return jsonify({
        "success": True,
        "message": "AI Evaluation completed successfully.",
        "results": results,
        "total_ai_marks": total_ai_marks
    })

@teacher_bp.route("/api/approve-marks", methods=["POST"])
@teacher_required
def api_approve_marks():
    """
    Lecturer approves or modifies marks for a submission.
    Creates immutable audit log and populates official marks.
    """
    data = request.get_json() or {}
    submission_id = data.get("submission_id")
    question_marks = data.get("marks", {})  # {question_id: approved_mark}
    lecturer_comments = data.get("comments", {})
    overall_comment = data.get("overall_comment", "")
    teacher_id = session.get("teacher_id")

    supabase = db_manager.get_supabase(use_service_role=True)
    total_official_marks = 0.0

    if supabase:
        try:
            for qid, marks_val in question_marks.items():
                m_float = float(marks_val)
                total_official_marks += m_float
                comm = lecturer_comments.get(qid, "")

                # Fetch original AI evaluation
                prev_res = supabase.table("question_evaluations").select(
                    "id, ai_marks_awarded, approved_marks"
                ).eq("submission_id", submission_id).eq("question_id", qid).execute()

                prev_ev = prev_res.data[0] if prev_res.data else {}
                orig_ai = prev_ev.get("ai_marks_awarded", m_float)
                prev_off = prev_ev.get("approved_marks")

                is_modified = (m_float != orig_ai)

                # Update question_evaluations
                supabase.table("question_evaluations").update({
                    "approval_status": "modified_and_approved" if is_modified else "approved_as_is",
                    "approved_marks": m_float,
                    "lecturer_comments": comm,
                    "reviewed_by": teacher_id,
                    "reviewed_at": datetime.now(timezone.utc).isoformat()
                }).eq("submission_id", submission_id).eq("question_id", qid).execute()

                # Add entry into marks_audit_log
                supabase.table("marks_audit_log").insert({
                    "submission_id": submission_id,
                    "question_id": qid,
                    "question_evaluation_id": prev_ev.get("id"),
                    "action_type": "lecturer_modified" if is_modified else "lecturer_approved",
                    "original_ai_marks": orig_ai,
                    "previous_official_marks": prev_off,
                    "new_official_marks": m_float,
                    "reason": f"Teacher marked as {'modified' if is_modified else 'approved'}. {comm}".strip(),
                    "actor_lecturer_id": teacher_id
                }).execute()

            # Update submission record to 'approved' and calculate pass status
            sub_res = supabase.table("answer_submissions").select("assessment_id").eq("id", submission_id).execute()
            asm_id = sub_res.data[0]["assessment_id"] if sub_res.data else None
            asm = AssessmentService.get_assessment_by_id(asm_id) if asm_id else None
            max_m = float(asm.get("max_marks", 20.0)) if asm else 20.0
            pass_pct = float(asm.get("pass_percentage", 35.0)) if asm else 35.0
            pct = round((total_official_marks / max_m) * 100, 2)
            pass_status = "pass" if pct >= pass_pct else "fail"

            supabase.table("answer_submissions").update({
                "status": "approved",
                "official_total_marks": total_official_marks,
                "percentage": pct,
                "pass_status": pass_status,
                "approved_by": teacher_id,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "lecturer_overall_comment": overall_comment
            }).eq("id", submission_id).execute()

            return jsonify({
                "success": True,
                "message": "Marks approved and recorded in official audit ledger.",
                "total_official_marks": total_official_marks,
                "percentage": pct,
                "pass_status": pass_status
            })

        except Exception as e:
            logger.error(f"Error approving marks: {e}")
            return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({"success": True, "message": "Marks approved (Local mode).", "total_official_marks": total_official_marks})

@teacher_bp.route("/reopen-submission", methods=["POST"])
@teacher_required
def reopen_submission():
    """Allows lecturer to reopen a locked submission with an audit reason."""
    submission_id = request.form.get("submission_id")
    reason = request.form.get("reason", "").strip()
    teacher_id = session.get("teacher_id")

    success, msg = SubmissionService.reopen_submission(submission_id, teacher_id, reason)
    if success:
        flash("Submission has been reopened. The student may now update and resubmit.", "success")
    else:
        flash(f"Reopen failed: {msg}", "danger")

    return redirect(request.referrer or url_for("teacher.dashboard"))

@teacher_bp.route("/analytics/<assessment_id>")
@teacher_required
def result_analytics(assessment_id):
    """Result Analyser and Topic Analytics Dashboard."""
    assessment = AssessmentService.get_assessment_by_id(assessment_id)
    if not assessment:
        flash("Assessment not found.", "danger")
        return redirect(url_for("teacher.dashboard"))

    analytics = AnalyticsService.get_assessment_analytics(assessment_id)
    return render_template(
        "teacher/analytics.html",
        assessment=assessment,
        analytics=analytics
    )

# ==========================================
# Course Syllabus & Materials Management
# ==========================================

@teacher_bp.route("/materials")
@teacher_required
def manage_materials():
    """Lecturer materials and syllabus management dashboard."""
    materials = MaterialService.get_all_materials()
    assessments = AssessmentService.get_all_assessments()

    total_count = len(materials)
    syllabus_count = sum(1 for m in materials if m.get("material_type") == "syllabus")
    notes_count = sum(1 for m in materials if m.get("material_type") in ("lecture_notes", "reference_material"))
    
    total_formulas = 0
    total_definitions = 0
    total_units = 0
    for m in materials:
        sc = m.get("structured_content") or {}
        total_formulas += len(sc.get("key_formulas", []))
        total_definitions += len(sc.get("key_definitions", []))
        total_units += len(sc.get("units", []))

    stats = {
        "total": total_count,
        "syllabus": syllabus_count,
        "notes": notes_count,
        "formulas": total_formulas,
        "definitions": total_definitions,
        "units": total_units
    }

    return render_template(
        "teacher/materials.html",
        materials=materials,
        assessments=assessments,
        stats=stats
    )


@teacher_bp.route("/materials/upload", methods=["POST"])
@teacher_required
def upload_material():
    """Uploads, extracts, and structures syllabus/study materials."""
    file = request.files.get("material_file")
    if not file or not file.filename:
        flash("Please select a file to upload (.pdf, .docx, .doc, .png, .jpg).", "warning")
        return redirect(url_for("teacher.manage_materials"))

    title = request.form.get("title", "").strip() or file.filename
    material_type = request.form.get("material_type", "syllabus").strip()
    subject_code = request.form.get("subject_code", "").strip()
    unit_topic = request.form.get("unit_topic", "").strip()
    assessment_id = request.form.get("assessment_id", "").strip() or None
    teacher_id = session.get("teacher_id")

    success, material, msg = MaterialService.save_and_process_material(
        file=file,
        title=title,
        material_type=material_type,
        subject_code=subject_code,
        unit_topic=unit_topic,
        assessment_id=assessment_id,
        created_by=teacher_id
    )

    if success and material:
        sc = material.get("structured_content") or {}
        num_u = len(sc.get("units", []))
        num_f = len(sc.get("key_formulas", []))
        num_d = len(sc.get("key_definitions", []))
        flash(f"🎉 Successfully uploaded and processed '{material.get('title')}'! Extracted {num_u} units/topics, {num_f} formulas, and {num_d} key definitions.", "success")
    else:
        flash(f"Could not process material: {msg}", "danger")

    return redirect(url_for("teacher.manage_materials"))


@teacher_bp.route("/materials/<material_id>")
@teacher_required
def get_material_details(material_id):
    """Returns material details and structured content as JSON for the modal."""
    material = MaterialService.get_material_by_id(material_id)
    if not material:
        return jsonify({"success": False, "message": "Material not found"}), 404
    return jsonify({"success": True, "material": material})


@teacher_bp.route("/materials/<material_id>/download")
@teacher_required
def download_material(material_id):
    """Downloads the original material file."""
    material = MaterialService.get_material_by_id(material_id)
    if not material:
        flash("Material not found.", "danger")
        return redirect(url_for("teacher.manage_materials"))

    rel_path = material.get("file_path", "")
    full_path = os.path.join(Config.BASE_DIR, rel_path.lstrip("/\\"))
    if not os.path.exists(full_path):
        flash("File not found on server.", "danger")
        return redirect(url_for("teacher.manage_materials"))

    return send_file(
        full_path,
        as_attachment=True,
        download_name=material.get("file_name") or os.path.basename(full_path)
    )


@teacher_bp.route("/materials/<material_id>/delete", methods=["POST"])
@teacher_required
def delete_material(material_id):
    """Deletes material record and file."""
    success, msg = MaterialService.delete_material(material_id)
    if success:
        flash("Material deleted successfully.", "info")
    else:
        flash(f"Could not delete material: {msg}", "danger")
    return redirect(url_for("teacher.manage_materials"))


@teacher_bp.route("/assessment/<assessment_id>/attach-material", methods=["POST"])
@teacher_required
def attach_material(assessment_id):
    """Attaches an uploaded material to a specific assessment."""
    material_id = request.form.get("material_id")
    if not material_id:
        flash("No material selected.", "warning")
        return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))

    ok, msg = MaterialService.attach_to_assessment(material_id, assessment_id)
    if ok:
        flash("Material attached to assessment successfully! AI evaluation and question generation will use this material.", "success")
    else:
        flash(f"Could not attach material: {msg}", "danger")

    return redirect(url_for("teacher.manage_questions", assessment_id=assessment_id))

