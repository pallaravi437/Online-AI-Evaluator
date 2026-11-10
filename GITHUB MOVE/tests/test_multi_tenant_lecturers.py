import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import shutil
from app import create_app
from services.lecturer_service import LecturerService, TENANTS_DIR, LECTURERS_FILE
from services.auth_service import AuthService
from services.assessment_service import AssessmentService
from services.student_service import StudentService

@pytest.fixture(autouse=True)
def clean_test_lecturers():
    """Ensure clean lecturer records before and after each test."""
    lecturers = LecturerService._load_lecturers()
    cleaned = [l for l in lecturers if not l.get("username", "").startswith("test_")]
    LecturerService._save_lecturers(cleaned)
    yield
    lecturers = LecturerService._load_lecturers()
    cleaned = [l for l in lecturers if not l.get("username", "").startswith("test_")]
    LecturerService._save_lecturers(cleaned)

@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app

@pytest.fixture
def client(app):
    return app.test_client()

def test_admin_superuser_credentials():
    """Verify admin superuser authenticates with username admin and password 87654321."""
    success, data, msg = AuthService.authenticate_teacher("admin", "87654321")
    assert success is True
    assert data["role"] == "admin"
    assert data["username"] == "admin"
    assert "admin" in data["id"]

def test_admin_wrong_password():
    """Verify admin login fails with incorrect password."""
    success, data, msg = AuthService.authenticate_teacher("admin", "wrong_password")
    assert success is False
    assert data is None

def test_lecturer_signup_approval_and_isolation(client):
    """
    Complete lifecycle test:
    1. Lecturer A registers -> is_approved is False -> cannot log in yet
    2. Admin approves Lecturer A
    3. Lecturer A logs in successfully
    4. Lecturer B registers and gets approved
    5. Tenant data isolation:
       - Lecturer A creates an assessment & imports students
       - Lecturer B creates an assessment & imports students
       - Lecturer A cannot see Lecturer B's data
       - Lecturer B cannot see Lecturer A's data
    """
    # 1. Register Lecturer A
    suc_a, data_a, msg_a = LecturerService.register_lecturer(
        full_name="Prof. Alice Smith",
        username="test_alice_ece",
        email="test_alice@test.edu",
        password="Password123!",
        subject="Signals & Systems"
    )
    assert suc_a is True, f"Registration A failed: {msg_a}"
    lec_a_id = data_a["id"]

    # Before approval, login should fail
    auth_fail, _, fail_msg = LecturerService.authenticate_lecturer("test_alice_ece", "Password123!")
    assert auth_fail is False
    assert "pending" in fail_msg.lower()

    # 2. Register Lecturer B
    suc_b, data_b, msg_b = LecturerService.register_lecturer(
        full_name="Dr. Bob Johnson",
        username="test_bob_vlsi",
        email="test_bob@test.edu",
        password="Password456!",
        subject="VLSI Design"
    )
    assert suc_b is True, f"Registration B failed: {msg_b}"
    lec_b_id = data_b["id"]

    # 3. Admin approves Lecturer A and Lecturer B
    appr_a, _ = LecturerService.approve_lecturer(lec_a_id)
    assert appr_a is True
    appr_b, _ = LecturerService.approve_lecturer(lec_b_id)
    assert appr_b is True

    # 4. Now both can authenticate
    auth_a, user_a, _ = LecturerService.authenticate_lecturer("test_alice_ece", "Password123!")
    assert auth_a is True
    assert user_a["id"] == lec_a_id

    auth_b, user_b, _ = LecturerService.authenticate_lecturer("test_bob_vlsi", "Password456!")
    assert auth_b is True
    assert user_b["id"] == lec_b_id

    # 5. Data Isolation Test - Assessments
    # Alice creates assessment
    ok_asm_a, id_asm_a, _ = AssessmentService.create_assessment(
        title="Signals Test 1",
        assessment_type="slip_test",
        max_marks=20,
        assessment_date="2026-11-10",
        created_by_id=lec_a_id,
        subject_code="EC301"
    )
    assert ok_asm_a is True

    # Bob creates assessment
    ok_asm_b, id_asm_b, _ = AssessmentService.create_assessment(
        title="VLSI Design Assignment",
        assessment_type="assignment",
        max_marks=50,
        assessment_date="2026-11-10",
        created_by_id=lec_b_id,
        subject_code="EC502"
    )
    assert ok_asm_b is True

    # Check Alice's assessments view
    alice_asms = AssessmentService.get_all_assessments(teacher_id=lec_a_id)
    alice_titles = [a["title"] for a in alice_asms]
    assert "Signals Test 1" in alice_titles
    assert "VLSI Design Assignment" not in alice_titles

    # Check Bob's assessments view
    bob_asms = AssessmentService.get_all_assessments(teacher_id=lec_b_id)
    bob_titles = [a["title"] for a in bob_asms]
    assert "VLSI Design Assignment" in bob_titles
    assert "Signals Test 1" not in bob_titles

    # 6. Data Isolation Test - Students
    # Alice imports students
    StudentService.import_students([
        {"pin": "ALICE-001", "full_name": "Alice Student 1", "branch": "ECE"},
        {"pin": "ALICE-002", "full_name": "Alice Student 2", "branch": "ECE"}
    ], teacher_id=lec_a_id)

    # Bob imports students
    StudentService.import_students([
        {"pin": "BOB-001", "full_name": "Bob Student 1", "branch": "VLSI"}
    ], teacher_id=lec_b_id)

    # Alice should only see Alice's students
    alice_students = StudentService.get_all_students(teacher_id=lec_a_id)
    alice_pins = [s["pin"] for s in alice_students]
    assert "ALICE-001" in alice_pins
    assert "ALICE-002" in alice_pins
    assert "BOB-001" not in alice_pins

    # Bob should only see Bob's students
    bob_students = StudentService.get_all_students(teacher_id=lec_b_id)
    bob_pins = [s["pin"] for s in bob_students]
    assert "BOB-001" in bob_pins
    assert "ALICE-001" not in bob_pins

    # 7. Dedicated Config Isolation Test
    LecturerService.update_lecturer_config(lec_a_id, {
        "gemini_api_key": "AIzaSyAlicePrivateGeminiKey123",
        "database_type": "mysql",
        "mysql_database": "alice_private_db"
    })
    cfg_a = LecturerService.get_lecturer_config(lec_a_id)
    assert cfg_a["gemini_api_key"] == "AIzaSyAlicePrivateGeminiKey123"
    assert cfg_a["mysql_database"] == "alice_private_db"

    cfg_b = LecturerService.get_lecturer_config(lec_b_id)
    assert cfg_b.get("gemini_api_key") != "AIzaSyAlicePrivateGeminiKey123"

def test_admin_routes_access(client):
    """Test admin routes authorization: admin can access /admin/lecturers, non-admin is blocked."""
    # Anonymous cannot access
    res = client.get("/admin/lecturers")
    assert res.status_code == 302

    # Login as admin
    login_res = client.post("/teacher/login", data={
        "email": "admin",
        "password": "87654321"
    }, follow_redirects=True)
    assert login_res.status_code == 200

    # Admin accesses /admin/lecturers
    admin_page = client.get("/admin/lecturers")
    assert admin_page.status_code == 200
    assert b"Lecturer Management" in admin_page.data
