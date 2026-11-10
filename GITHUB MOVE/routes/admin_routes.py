import os
import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from config import Config
from services.ai_service import AIService
from services.mcp_service import MCPService
from database.db_manager import db_manager

logger = logging.getLogger("bea.admin_routes")
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

def admin_required(f):
    """Enforces strict administrator privileges."""
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Administrator privileges required.", "warning")
            return redirect(url_for("teacher.login"))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

def admin_or_lecturer_required(f):
    """Enforces administrator or lecturer privileges."""
    def wrapper(*args, **kwargs):
        if session.get("role") not in ("admin", "lecturer"):
            flash("Administrator or Lecturer privileges required.", "warning")
            return redirect(url_for("teacher.login"))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

def is_valid_new_key(k: str) -> bool:
    """True if k is a real non-empty key, not a masked string with dots or asterisks."""
    if not k:
        return False
    if any(c in k for c in ("•", "*", "…", "●")):
        return False
    return True

@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_or_lecturer_required
def settings():
    """Admin Settings Wizard for Gemini API, MCP, and Supabase Database."""
    if request.method == "POST":
        updates = {}
        new_key = request.form.get("gemini_key", "").strip()
        if is_valid_new_key(new_key):
            updates["GEMINI_API_KEY"] = new_key

        new_provider = request.form.get("ai_provider", "").strip().lower() or "auto"
        updates["AI_PROVIDER"] = new_provider

        new_model = request.form.get("gemini_model", "").strip() or "auto"
        updates["GEMINI_MODEL"] = new_model

        custom_url = request.form.get("ai_custom_base_url", "").strip()
        if "ai_custom_base_url" in request.form:
            updates["AI_CUSTOM_BASE_URL"] = custom_url

        retry_limit = request.form.get("retry_limit", "").strip()
        if retry_limit:
            updates["AI_RETRY_LIMIT"] = retry_limit

        batch_size = request.form.get("batch_size", "").strip()
        if batch_size:
            updates["AI_BATCH_SIZE"] = batch_size

        mcp_url = request.form.get("mcp_url", "").strip()
        if mcp_url:
            updates["MCP_SERVER_URL"] = mcp_url

        mcp_token = request.form.get("mcp_token", "").strip()
        if is_valid_new_key(mcp_token):
            updates["MCP_AUTH_TOKEN"] = mcp_token

        Config.update_settings(updates)
        active_m = AIService.get_working_model()
        flash(f"✅ Settings saved! Gemini active model is '{active_m}' (Mode: {Config.GEMINI_MODEL}).", "success")
        return redirect(url_for("admin.settings"))

    supabase_report = db_manager.test_connection()
    mcp_status = MCPService.get_status()

    # Masked keys for display
    def mask_key(k: str) -> str:
        if not k or Config.is_placeholder(k):
            return ""
        if len(k) <= 8:
            return "••••••••"
        return f"{k[:4]}••••••••{k[-4:]}"

    masked_config = {
        "gemini_api_key_masked": mask_key(Config.GEMINI_API_KEY),
        "ai_provider": Config.AI_PROVIDER,
        "active_provider": AIService.get_active_provider(),
        "gemini_model": Config.GEMINI_MODEL,
        "active_model": AIService.get_working_model(),
        "ai_custom_base_url": Config.AI_CUSTOM_BASE_URL,
        "ai_retry_limit": Config.AI_RETRY_LIMIT,
        "ai_batch_size": Config.AI_BATCH_SIZE,
        "mcp_enabled": Config.MCP_ENABLED,
        "mcp_server_url": Config.MCP_SERVER_URL,
        "mcp_token_masked": mask_key(Config.MCP_AUTH_TOKEN),
        "supabase_url": Config.SUPABASE_URL,
        "supabase_key_masked": mask_key(Config.SUPABASE_KEY),
        "supabase_service_role_masked": mask_key(Config.SUPABASE_SERVICE_ROLE_KEY),
        "supabase_db_url_masked": mask_key(Config.SUPABASE_DB_URL)
    }

    return render_template(
        "admin/settings.html",
        config=masked_config,
        supabase_report=supabase_report,
        mcp_status=mcp_status
    )

@admin_bp.route("/api/save-settings", methods=["POST"])
@admin_required
def save_settings():
    """AJAX endpoint to save AI Provider, Model, and MCP settings immediately."""
    data = request.get_json() or {}
    updates = {}
    new_key = data.get("gemini_key", "").strip()
    if is_valid_new_key(new_key):
        updates["GEMINI_API_KEY"] = new_key

    new_provider = data.get("ai_provider", "").strip().lower() or "auto"
    updates["AI_PROVIDER"] = new_provider

    new_model = data.get("gemini_model", "").strip() or "auto"
    updates["GEMINI_MODEL"] = new_model

    if "ai_custom_base_url" in data:
        updates["AI_CUSTOM_BASE_URL"] = str(data["ai_custom_base_url"]).strip()

    if "retry_limit" in data and data["retry_limit"]:
        updates["AI_RETRY_LIMIT"] = str(data["retry_limit"])

    if "batch_size" in data and data["batch_size"]:
        updates["AI_BATCH_SIZE"] = str(data["batch_size"])

    if "mcp_url" in data:
        updates["MCP_SERVER_URL"] = str(data["mcp_url"]).strip()

    if "mcp_token" in data:
        token = str(data["mcp_token"]).strip()
        if is_valid_new_key(token):
            updates["MCP_AUTH_TOKEN"] = token

    Config.update_settings(updates)
    active_m = AIService.get_working_model()
    active_p = AIService.get_active_provider()
    return jsonify({
        "success": True,
        "message": f"Settings saved! Active provider is '{active_p}' with model '{active_m}'.",
        "provider": Config.AI_PROVIDER,
        "active_provider": active_p,
        "model": Config.GEMINI_MODEL,
        "active_model": active_m,
        "has_api_key": not Config.is_placeholder(Config.GEMINI_API_KEY)
    })

@admin_bp.route("/api/test-gemini", methods=["POST"])
@admin_bp.route("/api/test-ai", methods=["POST"])
@admin_required
def test_gemini():
    """Tests AI connection with provided or existing key and auto-detects working model across all providers."""
    data = request.get_json() or {}
    key = data.get("api_key", "").strip()
    if not is_valid_new_key(key):
        key = Config.GEMINI_API_KEY
    provider = data.get("provider", "").strip().lower() or Config.AI_PROVIDER or "auto"
    model = data.get("model", "").strip() or Config.GEMINI_MODEL or "auto"

    success, msg, details = AIService.test_connection(api_key=key, model_name=model, provider=provider)
    active_m = AIService.get_working_model()
    return jsonify({
        "success": success,
        "message": msg,
        "active_model": active_m,
        "details": details
    })

@admin_bp.route("/api/test-mcp", methods=["POST"])
@admin_required
def test_mcp():
    """Tests connection to MCP server."""
    data = request.get_json() or {}
    url = data.get("server_url") or Config.MCP_SERVER_URL
    token = data.get("auth_token") or Config.MCP_AUTH_TOKEN

    success, info, msg = MCPService.test_connection(server_url=url, auth_token=token)
    return jsonify({"success": success, "info": info, "message": msg})

@admin_bp.route("/api/test-supabase", methods=["POST"])
@admin_required
def test_supabase():
    """Tests Supabase API and PostgreSQL connection and validates 14 tables."""
    report = db_manager.test_connection()
    return jsonify({"success": report["configured"] and (report["api_connected"] or report["db_connected"]), "report": report})

@admin_bp.route("/api/run-migration", methods=["POST"])
@admin_required
def run_migration():
    """Executes 001_initial_bea_schema.sql to provision tables and storage bucket."""
    migration_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "database", "migrations", "001_initial_bea_schema.sql")
    success, msg = db_manager.execute_migration_file(migration_file)
    return jsonify({"success": success, "message": msg})


# =====================================================
# D. Universal Multi-Server Storage Management
# =====================================================

# 1. GET /admin/servers - renders the multi-server management page
@admin_bp.route("/servers")
@admin_or_lecturer_required
def manage_servers():
    """Multi-server storage configuration dashboard."""
    from database.server_registry import db_server_manager
    from database.routing_manager import storage_routing_manager
    servers = db_server_manager.list_servers()
    routes = storage_routing_manager.get_all_routes()
    return render_template("admin/servers.html", servers=servers, routes=routes)

# 2. POST /admin/api/servers/register - register/update a server (JSON)
@admin_bp.route("/api/servers/register", methods=["POST"])
@admin_required
def api_register_server():
    from database.server_registry import db_server_manager
    data = request.get_json() or {}
    server_id = data.get("server_id", "").strip()
    if not server_id:
        return jsonify({"success": False, "message": "Server ID is required."}), 400
    server_name = data.get("server_name", "").strip() or server_id
    provider = data.get("provider", "").strip()
    if not provider:
        return jsonify({"success": False, "message": "Provider is required."}), 400
    config = {k: v for k, v in data.items() if k not in ("server_id", "server_name", "provider", "is_active", "is_default")}
    is_active = data.get("is_active", True)
    is_default = data.get("is_default", False)
    try:
        ok, msg = db_server_manager.register_or_update_server(
            server_id=server_id,
            server_name=server_name,
            provider=provider,
            config=config,
            is_active=is_active,
            is_default=is_default
        )
        return jsonify({"success": ok, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# 3. POST /admin/api/servers/<server_id>/test - test connection (JSON)
@admin_bp.route("/api/servers/<server_id>/test", methods=["POST"])
@admin_required
def api_test_server(server_id):
    from database.server_registry import db_server_manager
    try:
        result = db_server_manager.test_connection(server_id)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# 4. POST /admin/api/servers/<server_id>/delete - delete a server (JSON)
@admin_bp.route("/api/servers/<server_id>/delete", methods=["POST"])
@admin_required
def api_delete_server(server_id):
    from database.server_registry import db_server_manager
    try:
        ok, msg = db_server_manager.delete_server(server_id)
        return jsonify({"success": ok, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# 5. GET /admin/api/routing/matrix - get full routing matrix (JSON)
@admin_bp.route("/api/routing/matrix")
@admin_required
def api_get_routing_matrix():
    from database.routing_manager import storage_routing_manager
    routes = storage_routing_manager.get_all_routes()
    return jsonify({"success": True, "routes": routes})

# 6. POST /admin/api/routing/set-default - set category default server (JSON)
@admin_bp.route("/api/routing/set-default", methods=["POST"])
@admin_required
def api_set_category_default():
    from database.routing_manager import storage_routing_manager
    data = request.get_json() or {}
    category = data.get("category", "").strip()
    server_id = data.get("server_id", "").strip()
    if not category or not server_id:
        return jsonify({"success": False, "message": "Category and server_id are required."}), 400
    try:
        ok, msg = storage_routing_manager.set_category_default(category, server_id)
        return jsonify({"success": ok, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# 7. POST /admin/api/routing/add-override - add a routing override (JSON)
@admin_bp.route("/api/routing/add-override", methods=["POST"])
@admin_required
def api_add_routing_override():
    from database.routing_manager import storage_routing_manager
    data = request.get_json() or {}
    try:
        ok, msg, rule = storage_routing_manager.add_override(
            category=data.get("category", ""),
            server_id=data.get("server_id", ""),
            test_id=data.get("test_id"),
            subject_code=data.get("subject_code"),
            department=data.get("department"),
            semester=data.get("semester"),
            academic_year=data.get("academic_year"),
            material_category=data.get("material_category"),
        )
        return jsonify({"success": ok, "rule_id": rule.get("id", ""), "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# 8. POST /admin/api/routing/delete-override - delete a routing override (JSON)
@admin_bp.route("/api/routing/delete-override", methods=["POST"])
@admin_required
def api_delete_routing_override():
    from database.routing_manager import storage_routing_manager
    data = request.get_json() or {}
    rule_id = data.get("rule_id", "").strip()
    if not rule_id:
        return jsonify({"success": False, "message": "rule_id is required."}), 400
    try:
        ok, msg = storage_routing_manager.delete_override(rule_id)
        return jsonify({"success": ok, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# 9. POST /admin/students/import-csv - import students from CSV
@admin_bp.route("/students/import-csv", methods=["POST"])
@admin_required
def import_students_csv():
    from services.academic_repositories import student_repository
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("Please select a CSV file.", "warning")
        return redirect(url_for("admin.manage_servers"))
    try:
        csv_bytes = file.read()
        imported, skipped, errors = student_repository.import_from_csv(csv_bytes)
        msg = f"Successfully imported {imported} student records from CSV."
        if skipped:
            msg += f" ({skipped} rows skipped)"
        if errors:
            msg += f" Errors: {'; '.join(errors[:5])}"
        flash(msg, "success" if imported > 0 else "warning")
    except Exception as e:
        flash(f"CSV import failed: {str(e)}", "danger")
    return redirect(url_for("admin.manage_servers"))

# 10. POST /admin/students/import-excel - import students from Excel
@admin_bp.route("/students/import-excel", methods=["POST"])
@admin_required
def import_students_excel():
    from services.academic_repositories import student_repository
    file = request.files.get("excel_file")
    if not file or not file.filename:
        flash("Please select an Excel file.", "warning")
        return redirect(url_for("admin.manage_servers"))
    try:
        excel_bytes = file.read()
        imported, skipped, errors = student_repository.import_from_excel(excel_bytes)
        msg = f"Successfully imported {imported} student records from Excel."
        if skipped:
            msg += f" ({skipped} rows skipped)"
        if errors:
            msg += f" Errors: {'; '.join(errors[:5])}"
        flash(msg, "success" if imported > 0 else "warning")
    except Exception as e:
        flash(f"Excel import failed: {str(e)}", "danger")
    return redirect(url_for("admin.manage_servers"))

# 11. GET /admin/search - universal data search
@admin_bp.route("/search")
@admin_required
def universal_search():
    from database.universal_data_manager import universal_data_manager
    query = request.args.get("q", "").strip()
    categories = request.args.getlist("categories") or None
    if not query:
        return render_template("admin/servers.html", servers=[], routes={}, search_results=None, search_query="")
    results = universal_data_manager.search(
        query=query,
        categories=categories,
        user_role=session.get("role", "lecturer"),
        user_id=session.get("teacher_id"),
    )
    total = sum(len(v) for v in results.values())
    if request.headers.get("Accept") == "application/json":
        return jsonify({"success": True, "query": query, "total": total, "results": results})
    flash(f"Found {total} results for '{query}'.", "info")
    return redirect(url_for("admin.manage_servers"))

# 12. GET /admin/api/system-status - overall system health summary (JSON)
@admin_bp.route("/api/system-status")
@admin_or_lecturer_required
def api_system_status():
    from database.universal_data_manager import universal_data_manager
    status = universal_data_manager.get_system_status()
    return jsonify({"success": True, "status": status})


# =====================================================
# E. Multi-Tenant Lecturer Management (Admin Superuser Only)
# =====================================================

@admin_bp.route("/lecturers")
@admin_required
def manage_lecturers():
    """Admin page to view and approve/reject lecturer registrations."""
    from services.lecturer_service import LecturerService
    all_lecturers = LecturerService.list_all_lecturers()
    pending = [l for l in all_lecturers if not l.get("is_approved")]
    approved = [l for l in all_lecturers if l.get("is_approved")]
    return render_template("admin/lecturers.html", pending=pending, approved=approved, all_lecturers=all_lecturers)

@admin_bp.route("/api/lecturers/<lecturer_id>/approve", methods=["POST"])
@admin_required
def approve_lecturer(lecturer_id):
    """Approves a pending lecturer registration."""
    from services.lecturer_service import LecturerService
    success, msg = LecturerService.approve_lecturer(lecturer_id)
    if request.headers.get("Accept") == "application/json":
        return jsonify({"success": success, "message": msg})
    flash(msg, "success" if success else "danger")
    return redirect(url_for("admin.manage_lecturers"))

@admin_bp.route("/api/lecturers/<lecturer_id>/reject", methods=["POST"])
@admin_required
def reject_lecturer(lecturer_id):
    """Rejects a pending lecturer registration."""
    from services.lecturer_service import LecturerService
    success, msg = LecturerService.reject_lecturer(lecturer_id)
    if request.headers.get("Accept") == "application/json":
        return jsonify({"success": success, "message": msg})
    flash(msg, "success" if success else "danger")
    return redirect(url_for("admin.manage_lecturers"))

@admin_bp.route("/api/lecturers/<lecturer_id>/delete", methods=["POST"])
@admin_required
def delete_lecturer(lecturer_id):
    """Removes a lecturer and their tenant reference."""
    from services.lecturer_service import LecturerService
    success, msg = LecturerService.delete_lecturer(lecturer_id)
    if request.headers.get("Accept") == "application/json":
        return jsonify({"success": success, "message": msg})
    flash(msg, "success" if success else "danger")
    return redirect(url_for("admin.manage_lecturers"))

@admin_bp.route("/api/lecturers/<lecturer_id>/config", methods=["POST"])
@admin_required
def update_lecturer_config(lecturer_id):
    """Updates a lecturer's database and API credentials from admin panel."""
    from services.lecturer_service import LecturerService
    data = request.get_json() or {}
    success, msg = LecturerService.update_lecturer_config(lecturer_id, data)
    return jsonify({"success": success, "message": msg})
