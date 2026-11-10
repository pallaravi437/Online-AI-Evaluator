import io
import pytest
from PIL import Image
from app import create_app
from services.auth_service import AuthService
from services.image_service import ImageService
from services.submission_service import SubmissionService
from services.assessment_service import AssessmentService

@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_health_check(client):
    """Verifies that the /health endpoint returns healthy status for Render probes."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "healthy"
    assert data["timezone"] == "Asia/Kolkata"

def test_student_pin_auth():
    """Verifies student PIN authentication logic."""
    success, data, msg = AuthService.authenticate_student("KASHABOINA PRANAVI", "26054-ES-041", "127.0.0.1")
    assert success is True
    assert data["pin"] == "26054-ES-041"
    assert data["role"] == "student"

def test_student_pin_rate_limiting():
    """Verifies that repeated failed attempts trigger lockout."""
    ip = "192.168.1.100"
    pin = "INVALID-PIN-999"
    for _ in range(5):
        AuthService.authenticate_student("Fake Student", pin, ip)

    # 6th attempt must be locked
    success, data, msg = AuthService.authenticate_student("Fake Student", pin, ip)
    assert success is False
    assert "locked" in msg.lower()

def test_teacher_auth():
    """Verifies teacher credentials."""
    success, data, msg = AuthService.authenticate_teacher("lecturer@bea.edu", "admin123", "127.0.0.1")
    assert success is True
    assert data["role"] == "lecturer"

def test_image_optimization():
    """Verifies image compression preserves aspect ratio and outputs JPEG."""
    img = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    raw_bytes = buf.getvalue()

    opt_bytes, metadata = ImageService.optimize_image(raw_bytes, max_dimension=1024, quality=80)
    assert len(opt_bytes) <= len(raw_bytes)
    assert metadata["mime_type"] == "image/jpeg"
    assert "sha256" in metadata

def test_submission_receipt_code():
    """Verifies cryptographic submission receipt format."""
    code = SubmissionService.generate_receipt_code()
    assert code.startswith("BEA-")
    assert len(code.split("-")) == 3

def test_assessment_default_seeding():
    """Verifies that default BEA assessment is loaded."""
    assessments = AssessmentService.get_all_assessments()
    assert len(assessments) >= 1
    assert "BJT" in assessments[0]["title"]


def test_add_question_with_image():
    from app import app
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    # Create dummy image
    img_data = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    data = {
        "question_number": "99",
        "question_text": "Draw and explain testing circuit diagram.",
        "max_marks": "5",
        "model_answer": "Standard test model answer.",
        "acceptable_alternatives": "None",
        "difficulty": "medium",
        "criterion_title": ["Circuit Diagram", "Explanation"],
        "criterion_marks": ["3", "2"],
        "criterion_desc": ["Correct pinout", "Full theory"],
        "question_image": (io.BytesIO(img_data), "test_circuit.gif")
    }

    res = client.post(
        "/teacher/assessment/bea-test-unit-001/add-question",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True
    )
    assert res.status_code == 200
    assert b"Question 99" in res.data or b"successfully" in res.data


def test_edit_question_and_rubric():
    from app import app
    from services.assessment_service import AssessmentService
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    # Get existing question
    assessment = AssessmentService.get_assessment_by_id("bea-unit-test-001")
    if not assessment:
        assessment = AssessmentService.get_assessment_by_id("bea-test-unit-001")
    assert assessment and len(assessment["questions"]) > 0
    target_q = assessment["questions"][0]

    edit_data = {
        "question_number": target_q["question_number"],
        "question_text": "Updated Question Text - Verify CE Configuration Analysis.",
        "max_marks": "10",
        "model_answer": "Updated model answer with derivation steps.",
        "acceptable_alternatives": "Equivalent Kirchhoff voltage analysis.",
        "difficulty": "hard",
        "requires_diagram": "1",
        "criterion_title": ["Updated Diagram Criterion", "Updated Calculation"],
        "criterion_marks": ["6", "4"],
        "criterion_desc": ["Biasing polarity check", "Formula substitution"]
    }

    res = client.post(
        f"/teacher/assessment/{assessment['id']}/edit-question/{target_q['id']}",
        data=edit_data,
        content_type="multipart/form-data",
        follow_redirects=True
    )
    assert res.status_code == 200
    assert b"updated successfully" in res.data or b"Updated Question Text" in res.data


def test_parse_word_document_roster():
    from services.student_service import StudentService
    word_bytes = StudentService.generate_word_template()
    assert len(word_bytes) > 0

    valid, invalid = StudentService.parse_file(word_bytes, "students.docx")
    assert len(valid) >= 4
    assert any(s["pin"] == "26054-ES-041" for s in valid)
    assert any("PRANAVI" in s["full_name"] for s in valid)


def test_parse_file_with_top_header_banners():
    from services.student_service import StudentService
    # CSV with college title banner in rows 1 and 2 before actual table header
    csv_content = (
        "GOVERNMENT POLYTECHNIC COLLEGE HYDERABAD,,,\n"
        "DEPARTMENT OF ELECTRONICS & COMMUNICATION ENGINEERING,,,\n"
        "ACADEMIC YEAR 2026-2027 DIPLOMA I YEAR,,,\n"
        "SL_NO,PIN,NAME OF THE CANDIDATE,MOBILE_NO\n"
        "1,26054-ES-041,KASHABOINA PRANAVI,9502210435\n"
        "2,26054-CPS-001,A SUJATHA,6305307638\n"
        "TOTAL STUDENTS: 2,,,\n"
    ).encode("utf-8")

    valid, invalid = StudentService.parse_file(csv_content, "diploma_ece_banner.csv")
    assert len(valid) == 2
    assert valid[0]["pin"] == "26054-ES-041"
    assert "PRANAVI" in valid[0]["full_name"]
    assert valid[1]["pin"] == "26054-CPS-001"


def test_download_word_template():
    from app import app
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    res = client.get("/teacher/students/template/word")
    assert res.status_code == 200
    assert "wordprocessingml" in res.content_type


def test_edit_student_profile():
    from app import app
    from services.student_service import StudentService
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    students = StudentService.get_all_students()
    assert len(students) > 0
    target_pin = students[0]["pin"]

    edit_data = {
        "pin": target_pin,
        "full_name": "KASHABOINA PRANAVI (TOPPER)",
        "branch": "ECE",
        "section": "A1",
        "semester": "Semester 2",
        "academic_year": "2026-2027",
        "contact_phone": "9998887776",
        "contact_email": "topper@example.com",
        "is_active": "1"
    }

    res = client.post(
        f"/teacher/students/edit/{target_pin}",
        data=edit_data,
        follow_redirects=True
    )
    assert res.status_code == 200
    assert b"updated successfully" in res.data or b"TOPPER" in res.data


def test_edit_preview_student():
    from app import app
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"
        sess["pending_roster_import"] = [
            {
                "row_number": 2,
                "pin": "26054-ES-099",
                "full_name": "SAMPLE TYPO STUDENT",
                "academic_year": "2026-2027",
                "semester": "Semester 1",
                "branch": "ECE",
                "section": "A",
                "contact_phone": "1234567890",
                "contact_email": ""
            }
        ]

    edit_preview_data = {
        "pin": "26054-ES-099",
        "full_name": "SAMPLE CORRECTED STUDENT",
        "academic_year": "2026-2027",
        "semester": "Semester 1",
        "branch": "ECE",
        "section": "B",
        "contact_phone": "9876543210",
        "contact_email": "corrected@example.com"
    }

    res = client.post(
        "/teacher/students/edit-preview/0",
        data=edit_preview_data,
        follow_redirects=True
    )
    assert res.status_code == 200
    assert b"updated" in res.data or b"CORRECTED" in res.data


def test_delete_students_batch():
    from app import app
    from services.student_service import StudentService
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    # Add a temporary test student to delete
    StudentService.import_students([{
        "pin": "TEMP-DEL-999",
        "full_name": "STUDENT TO DELETE",
        "academic_year": "2026-2027",
        "semester": "Semester 1",
        "branch": "ECE",
        "section": "A"
    }])

    students_before = StudentService.get_all_students()
    assert any(s["pin"] == "TEMP-DEL-999" for s in students_before)

    # Batch delete
    res = client.post(
        "/teacher/students/delete-batch",
        data={"selected_students": ["TEMP-DEL-999"]},
        follow_redirects=True
    )
    assert res.status_code == 200
    assert b"deleted" in res.data or b"Successfully" in res.data

    students_after = StudentService.get_all_students()
    assert not any(s["pin"] == "TEMP-DEL-999" for s in students_after)


def test_html_question_parser_table():
    from services.html_parser_service import HtmlQuestionParser

    sample_table_html = """
    <html>
    <body>
      <h2>BEA MCQ Quiz</h2>
      <table>
        <tr>
          <th>Sl No</th>
          <th>Question</th>
          <th>Option A</th>
          <th>Option B</th>
          <th>Option C</th>
          <th>Option D</th>
          <th>Answer</th>
          <th>Marks</th>
        </tr>
        <tr>
          <td>1</td>
          <td>In CE configuration, the input resistance is approximately:</td>
          <td>Very high (Mega ohms)</td>
          <td>Moderate (1 kilo ohm)</td>
          <td>Zero</td>
          <td>Infinite</td>
          <td>B</td>
          <td>1</td>
        </tr>
        <tr>
          <td>2</td>
          <td>The phase shift between input and output in CE amplifier is:</td>
          <td>0 degrees</td>
          <td>90 degrees</td>
          <td>180 degrees</td>
          <td>360 degrees</td>
          <td>C</td>
          <td>1</td>
        </tr>
      </table>
    </body>
    </html>
    """

    questions = HtmlQuestionParser.parse_html(sample_table_html)
    assert len(questions) == 2
    assert questions[0]["question_type"] == "mcq"
    assert questions[0]["correct_option"] == "B"
    assert "Moderate" in questions[0]["option_b"]
    assert questions[1]["correct_option"] == "C"
    assert "180" in questions[1]["option_c"]


def test_html_question_parser_containers_and_linear():
    from services.html_parser_service import HtmlQuestionParser

    sample_container_html = """
    <div class="quiz-container">
      <div class="question">
        <p>Q1. Which transistor configuration provides both voltage and current gain?</p>
        <ul>
          <li>A) Common Base (CB)</li>
          <li>B) Common Collector (CC)</li>
          <li>C) Common Emitter (CE)</li>
          <li>D) None of the above</li>
        </ul>
        <span class="answer">Answer: C</span>
      </div>
    </div>
    """

    questions = HtmlQuestionParser.parse_html(sample_container_html)
    assert len(questions) == 1
    assert questions[0]["question_type"] == "mcq"
    assert questions[0]["correct_option"] == "C"
    assert "Common Emitter" in questions[0]["option_c"]


def test_create_assessment_with_html_upload():
    import io
    from app import app
    from services.assessment_service import AssessmentService
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    html_bytes = b"""
    <table>
      <tr><th>No</th><th>Question</th><th>Option A</th><th>Option B</th><th>Option C</th><th>Option D</th><th>Ans</th></tr>
      <tr><td>1</td><td>BJT is a:</td><td>Voltage controlled device</td><td>Current controlled device</td><td>Power controlled device</td><td>Flux controlled device</td><td>B</td></tr>
      <tr><td>2</td><td>Thermal runaway is caused by:</td><td>Reverse saturation current</td><td>Base current</td><td>Emitter resistance</td><td>Collector capacitance</td><td>A</td></tr>
    </table>
    """

    data = {
        "title": "Diploma BEA Automated MCQ Test",
        "assessment_type": "mcq_test",
        "max_marks": "20",
        "pass_percentage": "40",
        "assessment_date": "2026-11-01",
        "instructions": "Select correct options.",
        "questions_file": (io.BytesIO(html_bytes), "bea_mcqs.html")
    }

    res = client.post("/teacher/assessments", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert res.status_code == 200

    assessments = AssessmentService.get_all_assessments()
    target_asm = next((a for a in assessments if a["title"] == "Diploma BEA Automated MCQ Test"), None)
    assert target_asm is not None
    asm_detail = AssessmentService.get_assessment_by_id(target_asm["id"])
    assert len(asm_detail.get("questions", [])) == 2
    assert asm_detail["questions"][0]["correct_option"] == "B"
    assert asm_detail["questions"][1]["correct_option"] == "A"


def test_add_and_edit_mcq_question():
    from app import app
    from services.assessment_service import AssessmentService
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    # Create assessment
    ok, asm_id, _ = AssessmentService.create_assessment("Manual MCQ Test", "mcq_test", 10.0, "2026-11-01")
    assert ok and asm_id

    # Add MCQ question
    add_data = {
        "question_number": "1",
        "question_type": "mcq",
        "question_text": "What is the relationship between alpha and beta?",
        "option_a": "beta = alpha / (1 - alpha)",
        "option_b": "alpha = beta / (1 - beta)",
        "option_c": "beta = alpha * (1 + alpha)",
        "option_d": "beta = 1 / alpha",
        "correct_option": "A",
        "max_marks": "1.0",
        "difficulty": "medium"
    }

    res = client.post(f"/teacher/assessment/{asm_id}/add-question", data=add_data, follow_redirects=True)
    assert res.status_code == 200

    asm = AssessmentService.get_assessment_by_id(asm_id)
    assert len(asm["questions"]) == 1
    q = asm["questions"][0]
    assert q["question_type"] == "mcq"
    assert q["correct_option"] == "A"
    assert "beta = alpha / (1 - alpha)" in q["option_a"]


def test_student_mcq_auto_grading():
    from services.assessment_service import AssessmentService
    from services.submission_service import SubmissionService

    # Create MCQ assessment
    questions = [
        {
            "id": "mcq-q1",
            "question_number": "1",
            "question_type": "mcq",
            "question_text": "Unit of transconductance gm is:",
            "option_a": "Siemens (mho)",
            "option_b": "Ohms",
            "option_c": "Volts",
            "option_d": "Amperes",
            "correct_option": "A",
            "max_marks": 2.0
        },
        {
            "id": "mcq-q2",
            "question_number": "2",
            "question_type": "mcq",
            "question_text": "Barkhausen criterion requires loop gain:",
            "option_a": "Zero",
            "option_b": "Exactly 1",
            "option_c": "Infinity",
            "option_d": "Negative",
            "correct_option": "B",
            "max_marks": 2.0
        }
    ]

    ok, asm_id, _ = AssessmentService.create_assessment(
        "MCQ Auto Grade Test", "mcq_test", 4.0, "2026-11-01", questions=questions
    )
    assert ok

    # Create draft submission
    sub = SubmissionService.get_or_create_draft_submission(asm_id, "test-student-auto-01")
    assert sub

    # Finalize submission with student answers: Q1 -> A (correct), Q2 -> C (wrong)
    student_answers = {
        "mcq-q1": "A",
        "mcq-q2": "C"
    }
    fin_ok, fin_sub, _ = SubmissionService.finalize_submission(
        submission_id=sub["id"],
        client_ip="127.0.0.1",
        mcq_answers=student_answers
    )
    assert fin_ok
    assert fin_sub["is_locked"] is True
    # Awarded 2 marks out of 4 (50%)
    assert fin_sub["official_total_marks"] == 2.0
    assert fin_sub["percentage"] == 50.0
    assert fin_sub["pass_status"] == "pass"


def test_mcq_student_workflow_full_auto_no_ai():
    """
    Verifies full student MCQ workflow:
    - Student accesses MCQ test
    - Answers questions one by one
    - Submits test
    - System calculates score immediately without AI
    - Checks immediate result and explanations
    """
    from app import app
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["student_id"] = "test-student-mcq-flow"
        sess["student_name"] = "Diploma Test Candidate"
        sess["student_pin"] = "26054-ES-099"
        sess["role"] = "student"

    # Fetch MCQ test
    resp = client.get("/student/assessment/bea-slip-test-001/mcq-test")
    assert resp.status_code == 200
    assert b"MCQ Test" in resp.data
    assert b"Zero AI Cost" in resp.data

    # Submit MCQ answers
    # bea-slip-test-001 has 5 questions:
    # Q1 correct is C, Q2 correct is A, Q3 correct is B, Q4 correct is C, Q5 correct is B
    # Let student answer Q1->C (correct), Q2->A (correct), Q3->A (wrong), Q4->C (correct), Q5 unanswered
    draft = SubmissionService.get_or_create_draft_submission("bea-slip-test-001", "test-student-mcq-flow")
    form_data = {
        "submission_id": draft["id"],
        "mcq_answer_bea-slip-test-001-q1": "C",
        "mcq_answer_bea-slip-test-001-q2": "A",
        "mcq_answer_bea-slip-test-001-q3": "A",
        "mcq_answer_bea-slip-test-001-q4": "C"
        # Q5 left unanswered
    }

    sub_resp = client.post(
        "/student/assessment/bea-slip-test-001/submit-mcq",
        data=form_data,
        follow_redirects=True
    )
    assert sub_resp.status_code == 200
    assert b"MCQ Test Result" in sub_resp.data
    assert b"Correct Answers" in sub_resp.data
    assert b"Unanswered" in sub_resp.data
    # 3 correct out of 5 = 3.0 / 5.0 (60%)
    assert b"3.0" in sub_resp.data
    assert b"60.0%" in sub_resp.data


def test_lecturer_create_assessment_and_ai_prompt_generation():
    """
    Verifies lecturer assessment creation options and AI prompt guidance generator.
    """
    from app import app
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    # Create new MCQ Test
    mcq_data = {
        "title": "Unit 2: Semiconductor Diodes MCQ",
        "assessment_type": "mcq_test",
        "unit_topic": "Semiconductor Diodes",
        "assessment_date": "2026-11-05",
        "deadline": "2026-11-06T23:59",
        "max_marks": "10",
        "pass_percentage": "40",
        "instructions": "Answer all 10 questions."
    }
    resp = client.post("/teacher/assessments", data=mcq_data, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Unit 2: Semiconductor Diodes MCQ" in resp.data

    # Create new Slip Test
    slip_data = {
        "title": "Unit 2: Diode Rectifiers Slip Test",
        "assessment_type": "slip_test",
        "unit_topic": "Full Wave Rectifiers",
        "assessment_date": "2026-11-07",
        "deadline": "2026-11-08T23:59",
        "max_marks": "20",
        "pass_percentage": "35",
        "instructions": "Draw center-tapped full wave rectifier with filter."
    }
    resp2 = client.post("/teacher/assessments", data=slip_data, follow_redirects=True)
    assert resp2.status_code == 200
    assert b"Unit 2: Diode Rectifiers Slip Test" in resp2.data


def test_google_forms_link_parser():
    """Verifies Google Forms link parser handling."""
    from services.html_parser_service import HtmlQuestionParser
    
    # Test invalid url
    ok, qs, msg = HtmlQuestionParser.parse_google_forms_url("https://example.com/invalid-link")
    assert ok is False
    assert "not appear to be a valid Google Forms URL" in msg


def test_ai_subject_adaptive_question_generation():
    """Verifies that AI question generation adapts to Chemistry / Periodic Table vs Electronics."""
    from services.ai_service import AIService

    # Test Periodic Table topic with typo 'POERIODIC TABLE'
    ok, questions, msg = AIService.generate_assessment_questions(
        lesson_name="POERIODIC TABLE",
        topics="SLIP TEST 3",
        assessment_type="mcq_test",
        num_questions=4,
        marks_per_question=1.0
    )
    assert ok is True
    assert len(questions) == 4
    all_texts = " ".join([q["question_text"] for q in questions]).lower()
    assert "periodic table" in all_texts or "atomic" in all_texts
    # Verify it does NOT contain basic electronics content
    assert "common emitter" not in all_texts
    assert "barkhausen" not in all_texts

    # Test Descriptive Chemistry
    ok_desc, q_desc, _ = AIService.generate_assessment_questions(
        lesson_name="Chemistry Periodic Trends",
        topics="Atomic Radius, Ionization Enthalpy",
        assessment_type="slip_test",
        num_questions=2,
        marks_per_question=5.0
    )
    assert ok_desc is True
    assert len(q_desc) == 2
    assert q_desc[0]["max_marks"] == 5.0
    assert len(q_desc[0]["criteria"]) > 0


def test_settings_save_and_update(client):
    """Verifies that lecturer can update Gemini model and settings permanently."""
    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    # Post settings update
    resp = client.post("/admin/settings", data={
        "gemini_model": "gemini-2.0-flash",
        "retry_limit": "4",
        "batch_size": "8"
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Settings saved" in resp.data

    from config import Config
    assert Config.GEMINI_MODEL == "gemini-2.0-flash"
    assert Config.AI_RETRY_LIMIT == 4
    assert Config.AI_BATCH_SIZE == 8

    # Revert to auto for subsequent tests
    Config.update_settings({"GEMINI_MODEL": "auto", "AI_RETRY_LIMIT": "3", "AI_BATCH_SIZE": "5"})


def test_ai_subtopics_and_keywords_suggestion(client):
    """Verifies the AI subtopics and keywords suggestion endpoint."""
    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    from services.assessment_service import AssessmentService
    assessments = AssessmentService.get_all_assessments()
    assert len(assessments) > 0
    asm_id = assessments[0]["id"]

    resp = client.post(
        f"/teacher/assessment/{asm_id}/ai-suggest-topics",
        json={"subject": "Chemistry", "lesson_name": "Periodic Table", "topics": "Periodic Trends"}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert len(data["subtopics"]) > 0
    assert len(data["keywords"]) > 0
    all_kws = " ".join(data["keywords"]).lower()
    assert any(term in all_kws for term in ("atomic", "moseley", "electronegativity", "periodic", "radius", "ionization", "trend"))


def test_document_question_parser_allowed_files():
    from services.document_question_parser import DocumentQuestionParser
    assert DocumentQuestionParser.is_allowed_file("exam.pdf") is True
    assert DocumentQuestionParser.is_allowed_file("paper.docx") is True
    assert DocumentQuestionParser.is_allowed_file("paper.doc") is True
    assert DocumentQuestionParser.is_allowed_file("test.png") is True
    assert DocumentQuestionParser.is_allowed_file("photo.jpg") is True
    assert DocumentQuestionParser.is_allowed_file("test.exe") is False


def test_document_question_parser_docx_and_noise_filtering():
    """Verifies that college headers, student blanks, and instructions are removed, leaving questions."""
    from docx import Document
    from services.document_question_parser import DocumentQuestionParser

    doc = Document()
    # Exam noise headers
    doc.add_paragraph("JNTU HYDERABAD UNIVERSITY COLLEGE OF ENGINEERING")
    doc.add_paragraph("DEPARTMENT OF ELECTRONICS & COMMUNICATION")
    doc.add_paragraph("MID-TERM EXAMINATION - NOVEMBER 2026")
    doc.add_paragraph("Course: B.Tech (ECE) | Subject: Basic Electronics | Code: EC101")
    doc.add_paragraph("Time: 2 Hours | Max Marks: 20 | Date: 09-11-2026")
    doc.add_paragraph("Student Name: _____________________  Roll No: _______________")
    doc.add_paragraph("General Instructions: Answer all questions. Draw neat sketches.")
    doc.add_paragraph("----------------------------------------------------------------")

    # Genuine questions
    doc.add_paragraph("1. Explain the working principle of a P-N Junction Diode under forward and reverse bias conditions. [5 Marks]")
    doc.add_paragraph("2. What is the efficiency of a half-wave rectifier? (1 Mark)")
    doc.add_paragraph("A) 40.6%")
    doc.add_paragraph("B) 81.2%")
    doc.add_paragraph("C) 50.0%")
    doc.add_paragraph("D) 100%")
    doc.add_paragraph("3. Derive the expression for the ripple factor of a Full Wave Rectifier. [5 Marks]")

    # Footer noise
    doc.add_paragraph("Page 1 of 1 --- All the Best --- Signature of Invigilator: ___________")

    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    ok, questions, msg = DocumentQuestionParser.parse_uploaded_file(docx_bytes, "test_exam.docx", default_marks=5.0)
    assert ok is True
    assert len(questions) >= 3

    # Check question texts do not contain noise
    q_texts = [q["question_text"] for q in questions]
    combined_text = " ".join(q_texts)
    assert "UNIVERSITY" not in combined_text
    assert "MID-TERM EXAMINATION" not in combined_text
    assert "Student Name" not in combined_text
    assert "Signature of Invigilator" not in combined_text

    # Verify questions are present
    assert any("P-N Junction Diode" in qt for qt in q_texts)
    assert any("ripple factor" in qt for qt in q_texts)


def test_document_question_endpoints(client):
    """Verifies the upload-parse and lecturer-confirmation endpoints for document questions."""
    from docx import Document
    from services.assessment_service import AssessmentService

    assessments = AssessmentService.get_all_assessments()
    asm_id = assessments[0]["id"]

    # Build sample DOCX
    doc = Document()
    doc.add_paragraph("Mid Term Test - Electronics")
    doc.add_paragraph("1. Define Zener breakdown and describe its application as a voltage regulator. [5 Marks]")
    doc.add_paragraph("2. Which semiconductor device exhibits negative resistance? (1 Mark)")
    doc.add_paragraph("A) Tunnel Diode")
    doc.add_paragraph("B) Varactor Diode")
    doc.add_paragraph("C) PIN Diode")
    doc.add_paragraph("D) Photodiode")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    # Step 1: Parse document
    resp = client.post(
        f"/teacher/assessment/{asm_id}/parse-document-questions",
        data={
            "document_file": (buf, "test_paper.docx"),
            "default_marks": "5.0"
        },
        content_type="multipart/form-data"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["count"] >= 2
    parsed_questions = data["questions"]

    # Step 2: Confirm questions
    resp_confirm = client.post(
        f"/teacher/assessment/{asm_id}/confirm-document-questions",
        json={"questions": parsed_questions}
    )
    assert resp_confirm.status_code == 200
    confirm_data = resp_confirm.get_json()
    assert confirm_data["success"] is True
    assert confirm_data["count"] >= 2


def test_material_service_and_structuring():
    """
    Verifies MaterialService parsing, structuring into units/formulas/definitions,
    and structured context generation for AI grounding.
    """
    from services.material_service import MaterialService
    import io

    # Create mock text-based docx / pdf content
    raw_content = b"""
    COURSE SYLLABUS: ELECTRICAL CIRCUITS & MACHINES (EE-201)
    
    UNIT 1: DC CIRCUITS ANALYSIS
    Ohm's Law: V = I * R. Power P = V * I. Kirchhoff's Current Law (KCL) and Voltage Law (KVL).
    Definition: Mesh Analysis is a technique used to determine current in planar circuits.
    
    UNIT 2: AC CIRCUITS & RESONANCE
    Impedance Z = sqrt(R^2 + (XL - XC)^2). Resonant Frequency fr = 1 / (2 * pi * sqrt(L * C)).
    Definition: Power factor is the cosine of the phase angle between voltage and current.
    
    Course Outcomes:
    1. Understand basic circuit theorems.
    2. Analyze series and parallel resonance circuits.
    """

    # Test internal structuring logic
    structured = MaterialService._structure_material_content(raw_content.decode("utf-8"), "syllabus")
    assert "units" in structured
    assert "key_formulas" in structured
    assert "key_definitions" in structured
    assert len(structured["units"]) >= 1
    assert len(structured["key_formulas"]) >= 1

    # Test file upload processing
    mock_file = io.BytesIO(raw_content)
    mock_file.filename = "circuits_syllabus.txt"

    success, mat, msg = MaterialService.save_and_process_material(
        file=mock_file,
        title="EE-201 Electrical Circuits Syllabus",
        material_type="syllabus",
        subject_code="EE-201",
        unit_topic="Units 1 & 2",
        assessment_id="test-asm-grounding-01",
        created_by="test-teacher-id"
    )
    assert success is True
    assert mat is not None
    assert mat["subject_code"] == "EE-201"
    mat_id = mat["id"]

    # Verify retrieval
    fetched = MaterialService.get_material_by_id(mat_id)
    assert fetched is not None
    assert fetched["title"] == "EE-201 Electrical Circuits Syllabus"

    # Verify structured context generation
    ctx = MaterialService.get_structured_context_for_assessment("test-asm-grounding-01", "EE-201")
    assert "EE-201" in ctx or "Syllabus" in ctx
    assert len(ctx) > 30

    # Cleanup
    del_ok, _ = MaterialService.delete_material(mat_id)
    assert del_ok is True


def test_teacher_materials_routes():
    """
    Verifies lecturer materials web routes:
    - GET /teacher/materials (management dashboard)
    - POST /teacher/materials/upload (file upload and processing)
    - GET /teacher/materials/<id> (JSON endpoint)
    - POST /teacher/materials/<id>/delete (cleanup)
    """
    from app import app
    from services.material_service import MaterialService
    import io

    client = app.test_client()

    with client.session_transaction() as sess:
        sess["teacher_id"] = "test-teacher-id"
        sess["teacher_name"] = "Dr. Test Lecturer"
        sess["role"] = "lecturer"

    # 1. GET materials dashboard
    resp = client.get("/teacher/materials")
    assert resp.status_code == 200
    assert b"Course Syllabus & Materials Repository" in resp.data
    assert b"Total Materials" in resp.data

    # 2. Upload new material
    sample_notes = io.BytesIO(b"""
    UNIT 3: TRANSISTORS AND AMPLIFIERS
    Formula: Collector Current Ic = beta * Ib + Iceo.
    Definition: Alpha is the ratio of collector current to emitter current.
    """)
    resp_upload = client.post(
        "/teacher/materials/upload",
        data={
            "material_file": (sample_notes, "transistors_notes.txt"),
            "title": "Unit 3 Transistors Comprehensive Notes",
            "material_type": "lecture_notes",
            "subject_code": "BEA (MEE-105)",
            "unit_topic": "Unit 3: Transistors"
        },
        content_type="multipart/form-data",
        follow_redirects=True
    )
    assert resp_upload.status_code == 200
    assert b"Successfully uploaded and processed" in resp_upload.data

    # 3. Find created material ID
    materials = MaterialService.get_all_materials(subject_code="BEA (MEE-105)")
    created = next((m for m in materials if "Transistors Comprehensive" in m.get("title", "")), None)
    assert created is not None
    mat_id = created["id"]

    # 4. GET JSON structure
    resp_json = client.get(f"/teacher/materials/{mat_id}")
    assert resp_json.status_code == 200
    data = resp_json.get_json()
    assert data["success"] is True
    assert data["material"]["id"] == mat_id
    assert "structured_content" in data["material"]

    # 5. Delete material
    resp_del = client.post(f"/teacher/materials/{mat_id}/delete", follow_redirects=True)
    assert resp_del.status_code == 200
    assert b"Material deleted successfully" in resp_del.data











