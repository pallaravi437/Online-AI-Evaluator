"""
Unit & Integration Tests for Universal Academic Data Storage System
===================================================================
Tests multi-server registry, storage routing hierarchy, universal file manager,
repositories (Students, Syllabus, Materials), and universal multi-category search.
"""

import pytest
import io
import json
from database.server_registry import DatabaseServerManager
from database.routing_manager import StorageRoutingManager
from database.file_storage_manager import FileStorageManager
from database.universal_data_manager import universal_data_manager
from services.academic_repositories import (
    StudentRepository,
    SyllabusRepository,
    MaterialRepository,
    QuestionRepository,
    EvaluationRepository,
)
from services.universal_search_service import universal_search_service


def test_server_manager_initialization_and_listing():
    mgr = DatabaseServerManager()
    servers = mgr.list_servers()
    assert len(servers) >= 4
    server_ids = [s["server_id"] for s in servers]
    assert "supabase_main" in server_ids
    assert "mysql_primary" in server_ids
    assert "firebase_primary" in server_ids
    assert "local_default" in server_ids


def test_server_registration_and_masking():
    mgr = DatabaseServerManager()
    success, msg = mgr.register_or_update_server(
        server_id="test_custom_server",
        server_name="Test Custom Server",
        provider="mysql",
        config={"host": "192.168.1.100", "password": "super_secret_password", "database": "test_db"},
        is_active=True
    )
    assert success is True
    server = mgr.get_server("test_custom_server")
    assert server is not None
    assert server["name"] == "Test Custom Server"
    # Verify password masking
    listed = [s for s in mgr.list_servers() if s["server_id"] == "test_custom_server"][0]
    assert "••••" in listed["masked_config"]["password"]

    # Cleanup
    mgr.delete_server("test_custom_server")
    assert mgr.get_server("test_custom_server") is None


def test_routing_manager_hierarchy():
    rm = StorageRoutingManager()

    # 1. Global category default
    srv = rm.resolve_server_for_data("students")
    assert srv == "mysql_primary"

    srv_mat = rm.resolve_server_for_data("materials")
    assert srv_mat == "supabase_main"

    # 2. Add an override rule for a specific Test ID
    ok, msg, rule = rm.add_override(
        server_id="firebase_primary",
        category="materials",
        test_id="test_fet_01"
    )
    assert ok is True
    assert rule["id"] is not None

    # Matching test_id should route to firebase_primary
    resolved = rm.resolve_server_for_data("materials", test_id="test_fet_01")
    assert resolved == "firebase_primary"

    # Non-matching test_id should fall back to category default
    resolved_other = rm.resolve_server_for_data("materials", test_id="test_other_99")
    assert resolved_other == "supabase_main"

    # Cleanup override
    rm.delete_override(rule["id"])


def test_universal_file_storage_local_fallback():
    fsm = FileStorageManager()
    content = b"Mock PDF or Textbook file content for testing"
    upload_res = fsm.upload_file(
        server_id="local_default",
        file_bytes=content,
        filename="test_lecture_notes.pdf",
        folder="materials"
    )
    assert isinstance(upload_res, dict)
    path = upload_res["storage_path"]
    assert upload_res["storage_server_id"] == "local_default"

    # Download back
    dl_ok, dl_bytes, dl_mime = fsm.download_file("local_default", path)
    assert dl_ok is True
    assert dl_bytes == content

    # Delete
    del_ok, _ = fsm.delete_file("local_default", path)
    assert del_ok is True


def test_student_repository_upsert_and_csv_import():
    repo = StudentRepository()

    # 1. Upsert single student
    ok, stu, msg = repo.upsert_student(
        student_pin="TEST-PIN-001",
        full_name="Antigravity Test Student",
        department="ECE",
        semester="4",
        academic_year="2026-27"
    )
    assert ok is True
    assert stu["student_pin"] == "TEST-PIN-001"

    # 2. Duplicate prevention on re-upsert
    ok2, stu2, _ = repo.upsert_student(
        student_pin="TEST-PIN-001",
        full_name="Antigravity Updated Name",
        department="ECE",
        semester="5"
    )
    assert ok2 is True
    assert stu2["full_name"] == "Antigravity Updated Name"
    assert stu2["semester"] == "5"

    # 3. CSV Import
    csv_data = (
        "student_pin,student_name,department,semester\n"
        "TEST-PIN-002,Batch Student Two,MECH,3\n"
        "TEST-PIN-003,Batch Student Three,CSE,5\n"
    )
    imported, skipped, errors = repo.import_from_csv(csv_data)
    assert imported >= 2
    assert repo.get_student_by_pin("TEST-PIN-002") is not None
    assert repo.get_student_by_pin("TEST-PIN-003") is not None


def test_syllabus_repository_versioning():
    repo = SyllabusRepository()

    # Save version 1
    ok1, syl1, _ = repo.save_syllabus(
        title="Linear Integrated Circuits",
        course_code="EC-401",
        department="ECE",
        semester="4",
        academic_year="2026-27",
        version="1.0",
        units=[{"unit_num": 1, "title": "Operational Amplifiers"}],
        course_outcomes=["CO1: Analyze op-amp feedback circuits"]
    )
    assert ok1 is True
    assert syl1["version"] == "1.0"

    # Save version 2 (updated)
    ok2, syl2, _ = repo.save_syllabus(
        title="Linear Integrated Circuits",
        course_code="EC-401",
        department="ECE",
        semester="4",
        academic_year="2026-27",
        version="2.0",
        units=[
            {"unit_num": 1, "title": "Operational Amplifiers"},
            {"unit_num": 2, "title": "Active Filters"}
        ],
        course_outcomes=["CO1: Analyze op-amp feedback circuits", "CO2: Design Butterworth filters"],
        previous_version_id=syl1["id"]
    )
    assert ok2 is True
    assert syl2["version"] == "2.0"


def test_universal_search_across_categories():
    results = universal_search_service.search_all(
        query="ECE",
        categories=["students", "syllabi"],
        user_role="admin"
    )
    assert "students" in results
    assert "syllabi" in results
    # Found student from earlier test
    pins = [s["student_pin"] for s in results["students"]]
    assert "TEST-PIN-001" in pins


def test_universal_data_manager_facade():
    udm = universal_data_manager
    assert udm.server_manager is not None
    assert udm.routing is not None
    assert udm.file_storage is not None
    assert udm.students is not None
    assert udm.syllabi is not None
    assert udm.materials is not None

    status = udm.get_system_status()
    assert status["servers"]["total"] >= 4
    assert status["routing"]["rules"] >= 1
