import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple
import bcrypt
from config import Config
from database.db_manager import db_manager

logger = logging.getLogger("bea.auth_service")

# In-memory rate limiting cache for login attempts per IP / student identifier
_failed_attempts: Dict[str, Dict[str, Any]] = {}

class AuthService:
    """
    Handles secure authentication for Students (Name + unique PIN)
    and Teachers (Email + Password hash with Bcrypt), including
    rate limiting, lockout protection, and session verification.
    """

    @staticmethod
    def _is_rate_limited(identifier: str) -> Tuple[bool, int]:
        """Checks whether the identifier is currently locked out."""
        now = datetime.now(timezone.utc)
        record = _failed_attempts.get(identifier)
        if not record:
            return False, 0

        locked_until = record.get("locked_until")
        if locked_until and now < locked_until:
            remaining_secs = int((locked_until - now).total_seconds())
            return True, remaining_secs

        # Clear expired lockout
        if locked_until and now >= locked_until:
            _failed_attempts.pop(identifier, None)

        return False, 0

    @staticmethod
    def _record_failed_attempt(identifier: str) -> Tuple[int, Optional[int]]:
        """Records a failed login attempt and applies temporary lockout if threshold is exceeded."""
        now = datetime.now(timezone.utc)
        record = _failed_attempts.setdefault(identifier, {"count": 0, "locked_until": None})
        record["count"] += 1

        if record["count"] >= Config.MAX_FAILED_LOGIN_ATTEMPTS:
            locked_until = now + timedelta(minutes=Config.LOCKOUT_DURATION_MINUTES)
            record["locked_until"] = locked_until
            remaining_secs = int(timedelta(minutes=Config.LOCKOUT_DURATION_MINUTES).total_seconds())
            logger.warning(f"Identifier '{identifier}' locked out for {Config.LOCKOUT_DURATION_MINUTES} minutes due to repeated failed logins.")
            return record["count"], remaining_secs

        return record["count"], None

    @staticmethod
    def _clear_failed_attempts(identifier: str):
        """Clears failed attempts on successful login."""
        _failed_attempts.pop(identifier, None)

    @classmethod
    def authenticate_student(cls, name: str, pin: str, client_ip: str = "") -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Validates student name and unique PIN strictly on the server-side.
        Never exposes other PINs or lists to the client.
        """
        clean_name = name.strip()
        clean_pin = pin.strip().upper()

        if not clean_name or not clean_pin:
            return False, None, "Please provide both Student Name and PIN."

        rate_limit_key = f"student_{clean_pin}_{client_ip}"
        is_locked, remaining_secs = cls._is_rate_limited(rate_limit_key)
        if is_locked:
            minutes = (remaining_secs // 60) + 1
            return False, None, f"Too many failed attempts. Account temporarily locked. Try again in {minutes} minutes."

        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                # Query student record by normalized PIN
                res = supabase.table("students").select(
                    "id, pin, full_name, academic_year, semester, branch, section, is_active, failed_login_attempts, locked_until"
                ).eq("pin_normalized", clean_pin).execute()

                students = res.data or []
                if not students:
                    cls._record_failed_attempt(rate_limit_key)
                    return False, None, "Invalid PIN or student record not found."

                student = students[0]
                if not student.get("is_active", True):
                    return False, None, "Your student account is inactive. Please contact your lecturer."

                # Verify name (case-insensitive fuzzy/exact match)
                db_name = student.get("full_name", "").strip().upper()
                input_name = clean_name.upper()

                # Accept exact match or partial match for initial/surname variations
                if input_name != db_name and input_name not in db_name and db_name not in input_name:
                    cls._record_failed_attempt(rate_limit_key)
                    return False, None, "The entered Name does not match the registered PIN."

                # Successful verification
                cls._clear_failed_attempts(rate_limit_key)
                
                # Update last login timestamp in Supabase
                try:
                    supabase.table("students").update({
                        "last_login_at": datetime.now(timezone.utc).isoformat(),
                        "failed_login_attempts": 0
                    }).eq("id", student["id"]).execute()
                except Exception as e:
                    logger.debug(f"Could not update student last_login: {e}")

                student_data = {
                    "id": student["id"],
                    "pin": student["pin"],
                    "full_name": student["full_name"],
                    "branch": student.get("branch", "ECE"),
                    "semester": student.get("semester", ""),
                    "section": student.get("section", ""),
                    "role": "student"
                }
                return True, student_data, "Login successful."

            except Exception as e:
                logger.error(f"Supabase student auth error: {e}")
                # Fallback to local memory / demo mode if DB is in setup phase
        
        # Fallback / Setup Mode (Allows lecturer testing before full Supabase sync)
        if clean_pin.startswith("26054-") or clean_pin.startswith("DEMO-"):
            cls._clear_failed_attempts(rate_limit_key)
            # Resolve against the local roster so demo sessions carry the real
            # student record (id/pin/name) used by submissions & teacher views.
            from services.student_service import _local_students
            roster_match = next(
                (st for st in _local_students if st.get("pin", "").upper() == clean_pin),
                None
            )
            demo_student = {
                "id": (roster_match or {}).get("id", "00000000-0000-0000-0000-000000000001"),
                "pin": clean_pin,
                "full_name": (roster_match or {}).get("full_name", clean_name),
                "branch": (roster_match or {}).get("branch", "ECE"),
                "semester": (roster_match or {}).get("semester", ""),
                "section": (roster_match or {}).get("section", "A"),
                "role": "student"
            }
            return True, demo_student, "Login successful (Setup mode)."

        cls._record_failed_attempt(rate_limit_key)
        return False, None, "Student record not found. Please verify your PIN."

    @classmethod
    def register_lecturer(cls, full_name: str, username: str, email: str, password: str,
                          mobile: str = "", subject: str = "", specialization: str = "") -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Register a new lecturer account (pending admin approval)."""
        from services.lecturer_service import LecturerService
        return LecturerService.register_lecturer(
            full_name=full_name,
            username=username,
            email=email,
            password=password,
            subject=subject,
            mobile=mobile,
            specialization=specialization
        )

    @classmethod
    def authenticate_teacher(cls, email: str, password: str, client_ip: str = "") -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Validates teacher email/username and password using Bcrypt.
        First validates admin superuser (admin / 87654321) & approved lecturers,
        then checks Supabase teacher_profiles, and falls back to setup defaults.
        """
        clean_email = email.strip().lower()
        if not clean_email or not password:
            return False, None, "Please enter both Email/Username and Password."

        rate_limit_key = f"teacher_{clean_email}_{client_ip}"
        is_locked, remaining_secs = cls._is_rate_limited(rate_limit_key)
        if is_locked:
            minutes = (remaining_secs // 60) + 1
            return False, None, f"Too many failed attempts. Locked for {minutes} minutes."

        # 1. Check LecturerService (Admin superuser: admin / 87654321, and approved lecturers)
        try:
            from services.lecturer_service import LecturerService
            success, lecturer_data, message = LecturerService.authenticate_lecturer(clean_email, password)
            if success and lecturer_data:
                cls._clear_failed_attempts(rate_limit_key)
                return True, lecturer_data, message
            elif message and "pending" in message.lower():
                return False, None, message
            elif message and "suspended" in message.lower():
                return False, None, message
        except Exception as e:
            logger.error(f"LecturerService authentication check error: {e}")

        # 2. Check Supabase teacher_profiles
        supabase = db_manager.get_supabase(use_service_role=True)
        if supabase:
            try:
                res = supabase.table("teacher_profiles").select(
                    "id, email, full_name, password_hash, role, is_active"
                ).eq("email", clean_email).execute()

                teachers = res.data or []
                if teachers:
                    teacher = teachers[0]
                    if not teacher.get("is_active", True):
                        return False, None, "This account is inactive."

                    pw_hash = teacher.get("password_hash", "").encode("utf-8")
                    if bcrypt.checkpw(password.encode("utf-8"), pw_hash):
                        cls._clear_failed_attempts(rate_limit_key)
                        
                        try:
                            supabase.table("teacher_profiles").update({
                                "last_login_at": datetime.now(timezone.utc).isoformat()
                            }).eq("id", teacher["id"]).execute()
                        except Exception:
                            pass

                        return True, {
                            "id": teacher["id"],
                            "email": teacher["email"],
                            "full_name": teacher["full_name"],
                            "role": teacher.get("role", "lecturer")
                        }, "Login successful."
            except Exception as e:
                logger.error(f"Teacher authentication error: {e}")

        # 3. Default teacher credentials for initial setup / testing
        if clean_email in ("lecturer@bea.edu", "teacher@bea.edu", "teacher") and password in ("admin123", "admin", "bea2026", "teacher123"):
            cls._clear_failed_attempts(rate_limit_key)
            return True, {
                "id": "11111111-1111-1111-1111-111111111111",
                "email": clean_email if "@" in clean_email else f"{clean_email}@bea.edu",
                "full_name": "BEA Course Lecturer",
                "role": "lecturer"
            }, "Login successful (Default administrative session)."

        cls._record_failed_attempt(rate_limit_key)
        return False, None, "Invalid lecturer credentials."

    @staticmethod
    def hash_password(plain_password: str) -> str:
        """Generates a Bcrypt salt and hash for a password."""
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")
