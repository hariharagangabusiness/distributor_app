"""admin routes."""
import calendar
import db
import gst_reminders
import math
import mimetypes
import os
import re
import uuid
from core.auth import ACCESS_TABS, ACCESS_TAB_KEYS, ALL_ROLES, CONFIGURABLE_ROLES, admin_required, default_landing_url, get_current_user, get_role_permissions
from core.helpers import DEFAULT_LOCATION_WORKING_DAYS, FORM_FIELD_ALWAYS_VISIBLE, FORM_FIELD_MODULES, INDIAN_STATES, LOCATION_TRACKED_ROLES, LOCATION_WEEKDAY_ABBR, MODULE_COLUMNS, STATE_NAME_BY_CODE, UPLOAD_ROOT, _location_working_days, _now_ist, custom_field_attachment_module, direct_sales_today_for_employee, get_company_settings, get_custom_field_defs, get_effective_columns, get_effective_form_fields, save_custom_fields, today_str, upcoming_maintenance
from datetime import date, datetime
from flask import flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename



from flask import Blueprint

bp = Blueprint("admin", __name__)


# =======================================================================
# LOGIN  (individual staff accounts - see Users table in schema.sql)
# =======================================================================

# =======================================================================
# ACCESS CONTROL  (Staff / Supervisor / Manager / Admin hierarchy)
# =======================================================================


@bp.route("/users")
@admin_required
def users_list():
    users = db.query("""SELECT u.*, e.EmployeeName FROM Users u
                      LEFT JOIN Employees e ON e.EmployeeID = u.EmployeeID
                      ORDER BY u.Active DESC, u.Username""")
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    return render_template("users_list.html", users=users, all_roles=ALL_ROLES, employees=employees)


@bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_add():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        role = request.form.get("role") if request.form.get("role") in ALL_ROLES else "Staff"
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        employee_id = int(request.form["employee_id"]) if request.form.get("employee_id") else None
        if not username:
            flash("Username is required.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Password and confirmation don't match.", "error")
        else:
            try:
                db.execute(
                    "INSERT INTO Users (Username, PasswordHash, FullName, Role, Active, EmployeeID) VALUES (?,?,?,?,1,?)",
                    (username, generate_password_hash(password), full_name or username, role, employee_id),
                )
                flash(f"Login created for {username}.", "success")
                return redirect(url_for("users_list"))
            except Exception:
                flash(f"Could not create login — the username '{username}' may already be taken.", "error")
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    return render_template("user_form.html", all_roles=ALL_ROLES, employees=employees)


@bp.route("/users/<int:uid>/employee", methods=["POST"])
@admin_required
def user_employee_update(uid):
    """Links (or unlinks) this login to an Employee record, so a Staff/
    Supervisor's own Sales-tab entries can auto-pick and lock the
    Salesperson field to themselves instead of picking from a dropdown."""
    user = db.query("SELECT * FROM Users WHERE UserID=?", (uid,), one=True)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("users_list"))
    employee_id = int(request.form["employee_id"]) if request.form.get("employee_id") else None
    db.execute("UPDATE Users SET EmployeeID=? WHERE UserID=?", (employee_id, uid))
    if employee_id:
        emp = db.query("SELECT EmployeeName FROM Employees WHERE EmployeeID=?", (employee_id,), one=True)
        flash(f"{user['Username']} linked to salesperson {emp['EmployeeName'] if emp else ''}.", "success")
    else:
        flash(f"{user['Username']} unlinked from any salesperson.", "success")
    return redirect(url_for("users_list"))


@bp.route("/users/<int:uid>/role", methods=["POST"])
@admin_required
def user_role_update(uid):
    me = get_current_user()
    role = request.form.get("role")
    if role not in ALL_ROLES:
        flash("Not a recognized role.", "error")
        return redirect(url_for("users_list"))
    if uid == me["UserID"] and role != "Admin":
        flash("You can't demote your own account away from Admin while signed in as it.", "error")
        return redirect(url_for("users_list"))
    user = db.query("SELECT * FROM Users WHERE UserID=?", (uid,), one=True)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("users_list"))
    db.execute("UPDATE Users SET Role=? WHERE UserID=?", (role, uid))
    flash(f"{user['Username']}'s role changed to {role}.", "success")
    return redirect(url_for("users_list"))


@bp.route("/settings/access-control", methods=["GET", "POST"])
@admin_required
def access_control():
    if request.method == "POST":
        rows = []
        for key in ACCESS_TAB_KEYS:
            for role in CONFIGURABLE_ROLES:
                if request.form.get(f"perm__{role}__{key}"):
                    rows.append((role, key, 1))
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM RoleTabPermissions")
            conn.executemany(
                "INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES (?,?,?)", rows)
            conn.commit()
        finally:
            conn.close()
        flash("Access Control settings saved.", "success")
        return redirect(url_for("access_control"))

    perms = get_role_permissions()
    # Group tabs in display order for the matrix, same grouping as the sidebar.
    groups = []
    seen_groups = {}
    for key, label, group_label in ACCESS_TABS:
        if group_label not in seen_groups:
            seen_groups[group_label] = {"label": group_label, "tabs": []}
            groups.append(seen_groups[group_label])
        seen_groups[group_label]["tabs"].append({"key": key, "label": label})
    return render_template("access_control.html", groups=groups, perms=perms,
                            configurable_roles=CONFIGURABLE_ROLES)


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@admin_required
def user_toggle(uid):
    me = get_current_user()
    if uid == me["UserID"]:
        flash("You can't deactivate your own account while signed in as it.", "error")
        return redirect(url_for("users_list"))
    user = db.query("SELECT * FROM Users WHERE UserID=?", (uid,), one=True)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("users_list"))
    db.execute("UPDATE Users SET Active=? WHERE UserID=?", (0 if user["Active"] else 1, uid))
    flash(f"{user['Username']} {'deactivated' if user['Active'] else 'reactivated'}.", "success")
    return redirect(url_for("users_list"))


@bp.route("/users/<int:uid>/reset-password", methods=["GET", "POST"])
@admin_required
def user_reset_password(uid):
    user = db.query("SELECT * FROM Users WHERE UserID=?", (uid,), one=True)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("users_list"))
    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Password and confirmation don't match.", "error")
        else:
            db.execute("UPDATE Users SET PasswordHash=? WHERE UserID=?",
                       (generate_password_hash(password), uid))
            flash(f"Password reset for {user['Username']}.", "success")
            return redirect(url_for("users_list"))
    return render_template("user_reset_password.html", user=user)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def get_product_stock(product_id):
    row = db.query(
        "SELECT COALESCE(SUM(QtyChange),0) AS CurrentStock FROM InventoryTransactions WHERE ProductID=?",
        (product_id,), one=True)
    return row["CurrentStock"] if row else 0


# =======================================================================
# ATTACHMENTS  (generic — used by Employees, Advances, Vehicles,
# Purchases, Expenses)
# =======================================================================


ATTACHMENT_MODULES = ["Employee", "Advance", "Vehicle", "Purchase", "Expense"]


def is_valid_attachment_module(module):
    if module in ATTACHMENT_MODULES:
        return True
    if module.startswith("CustomField:"):
        try:
            field_id = int(module.split(":", 1)[1])
        except ValueError:
            return False
        d = db.query("SELECT FieldType FROM CustomFieldDefinitions WHERE FieldID=? AND Active=1",
                     (field_id,), one=True)
        return bool(d) and d["FieldType"] == "attachment"
    return False


def save_attachment(module, record_id, file_storage):
    if not file_storage or not file_storage.filename:
        return
    safe_module = module.replace(":", "_").replace("/", "_")  # ':' isn't a valid folder char on Windows
    folder = os.path.join(UPLOAD_ROOT, safe_module, str(record_id))
    os.makedirs(folder, exist_ok=True)
    safe_name = secure_filename(file_storage.filename) or f"file-{uuid.uuid4().hex[:8]}"
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    full_path = os.path.join(folder, stored_name)
    file_storage.save(full_path)
    size = os.path.getsize(full_path)
    rel_path = "/".join([safe_module, str(record_id), stored_name])
    content_type = file_storage.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    db.execute("""INSERT INTO Attachments (ModuleName, RecordID, FileName, StoredPath, FileSize, ContentType, UploadedAt)
                VALUES (?,?,?,?,?,?,?)""",
               (module, record_id, safe_name, rel_path, size, content_type,
                datetime.now().isoformat(timespec="seconds")))


@bp.route("/attachments/<module>/<int:record_id>/upload", methods=["POST"])
def attachment_upload(module, record_id):
    if not is_valid_attachment_module(module):
        flash("Unknown attachment area.", "error")
        return redirect(request.form.get("return_to") or url_for("dashboard"))
    files = request.files.getlist("files")
    count = 0
    for f in files:
        if f and f.filename:
            save_attachment(module, record_id, f)
            count += 1
    flash(f"{count} file(s) attached." if count else "No file selected.", "success" if count else "warning")
    return redirect(request.form.get("return_to") or url_for("dashboard"))


@bp.route("/attachments/<int:attachment_id>/download")
def attachment_download(attachment_id):
    att = db.query("SELECT * FROM Attachments WHERE AttachmentID=?", (attachment_id,), one=True)
    if not att:
        flash("File not found.", "error")
        return redirect(url_for("dashboard"))
    full_path = os.path.join(UPLOAD_ROOT, att["StoredPath"])
    directory = os.path.dirname(full_path)
    filename = os.path.basename(full_path)
    return send_from_directory(directory, filename, as_attachment=True, download_name=att["FileName"])


def delete_all_attachments_for_module(module):
    """Removes every stored file + Attachments row for a module (used when
    an attachment-type custom field definition itself is deleted, so its
    uploaded files don't get orphaned on disk)."""
    for att in db.query("SELECT * FROM Attachments WHERE ModuleName=?", (module,)):
        full_path = os.path.join(UPLOAD_ROOT, att["StoredPath"])
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except OSError:
            pass
    db.execute("DELETE FROM Attachments WHERE ModuleName=?", (module,))


@bp.route("/attachments/<int:attachment_id>/delete", methods=["POST"])
@admin_required
def attachment_delete(attachment_id):
    att = db.query("SELECT * FROM Attachments WHERE AttachmentID=?", (attachment_id,), one=True)
    if att:
        full_path = os.path.join(UPLOAD_ROOT, att["StoredPath"])
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except OSError:
            pass
        db.execute("DELETE FROM Attachments WHERE AttachmentID=?", (attachment_id,))
        flash("Attachment deleted.", "success")
    return redirect(request.form.get("return_to") or url_for("dashboard"))


# =======================================================================
# CUSTOM FIELDS  (generic — admin-defined extra fields on any module)
# =======================================================================


CUSTOM_FIELD_MODULES = [
    ("Product", "Products & Stock"),
    ("Supplier", "Suppliers"),
    ("Customer", "Customers"),
    ("Purchase", "Purchases"),
    ("Sale", "Sales"),
    ("Expense", "Operating Expenses"),
    ("Vehicle", "Vehicles"),
    ("Maintenance", "Vehicle Maintenance"),
    ("Employee", "Employees"),
    ("SalaryPayment", "Salary Payments"),
    ("Advance", "Advance Payments"),
]


CUSTOM_FIELD_MODULE_LABELS = dict(CUSTOM_FIELD_MODULES)


CUSTOM_FIELD_TYPES = ["text", "number", "date", "dropdown", "checkbox", "attachment"]


@bp.route("/settings/custom-fields")
def custom_fields_admin():
    module = request.args.get("module", CUSTOM_FIELD_MODULES[0][0])
    fields = db.query("SELECT * FROM CustomFieldDefinitions WHERE ModuleName=? ORDER BY DisplayOrder, FieldID", (module,))
    return render_template("custom_fields_admin.html", modules=CUSTOM_FIELD_MODULES, module=module,
                            module_label=CUSTOM_FIELD_MODULE_LABELS.get(module, module),
                            fields=fields, field_types=CUSTOM_FIELD_TYPES)


@bp.route("/settings/custom-fields/add", methods=["POST"])
def custom_field_add():
    f = request.form
    module = f["module"]
    label = f["field_label"].strip()
    key = re.sub(r"[^a-z0-9_]", "", label.lower().replace(" ", "_"))
    if not key:
        flash("Field name must contain at least one letter or number.", "error")
        return redirect(url_for("custom_fields_admin", module=module))
    max_order = db.query("SELECT COALESCE(MAX(DisplayOrder),0) AS mo FROM CustomFieldDefinitions WHERE ModuleName=?",
                         (module,), one=True)["mo"]
    try:
        db.execute("""INSERT INTO CustomFieldDefinitions (ModuleName, FieldLabel, FieldKey, FieldType,
                    DropdownOptions, DisplayOrder, Active) VALUES (?,?,?,?,?,?,1)""",
                   (module, label, key, f.get("field_type", "text"), f.get("dropdown_options", ""), max_order + 1))
        flash(f"Custom field '{label}' added to {CUSTOM_FIELD_MODULE_LABELS.get(module, module)}.", "success")
    except Exception:
        flash(f"Could not add field — a field named '{label}' may already exist on this tab.", "error")
    return redirect(url_for("custom_fields_admin", module=module))


@bp.route("/settings/custom-fields/<int:field_id>/toggle", methods=["POST"])
def custom_field_toggle(field_id):
    d = db.query("SELECT * FROM CustomFieldDefinitions WHERE FieldID=?", (field_id,), one=True)
    module = request.form.get("module", d["ModuleName"] if d else "")
    if d:
        db.execute("UPDATE CustomFieldDefinitions SET Active=? WHERE FieldID=?", (0 if d["Active"] else 1, field_id))
    return redirect(url_for("custom_fields_admin", module=module))


@bp.route("/settings/custom-fields/<int:field_id>/delete", methods=["POST"])
@admin_required
def custom_field_delete(field_id):
    d = db.query("SELECT * FROM CustomFieldDefinitions WHERE FieldID=?", (field_id,), one=True)
    module = request.form.get("module", d["ModuleName"] if d else "")
    if d and d["FieldType"] == "attachment":
        delete_all_attachments_for_module(custom_field_attachment_module(field_id))
    db.execute("DELETE FROM CustomFieldDefinitions WHERE FieldID=?", (field_id,))
    flash("Custom field deleted (and any values/files stored in it).", "success")
    return redirect(url_for("custom_fields_admin", module=module))


# ---------------------------------------------------------------------
# Zones: admin-managed list of sales-territory names used as the Customer
# form's Zone dropdown (requires migrate_zones.py to have been run once).
# ---------------------------------------------------------------------


@bp.route("/settings/zones")
@admin_required
def zones_admin():
    zones = db.query("SELECT * FROM Zones ORDER BY DisplayOrder, ZoneID")
    return render_template("zones_admin.html", zones=zones)


@bp.route("/settings/zones/add", methods=["POST"])
@admin_required
def zone_add():
    name = request.form.get("zone_name", "").strip()
    if not name:
        flash("Zone name is required.", "error")
        return redirect(url_for("zones_admin"))
    max_order = db.query("SELECT COALESCE(MAX(DisplayOrder),0) AS mo FROM Zones", one=True)["mo"]
    try:
        db.execute("INSERT INTO Zones (ZoneName, Active, DisplayOrder) VALUES (?,1,?)", (name, max_order + 1))
        flash(f"Zone '{name}' added.", "success")
    except Exception:
        flash(f"Could not add zone — '{name}' may already exist.", "error")
    return redirect(url_for("zones_admin"))


@bp.route("/settings/zones/<int:zone_id>/rename", methods=["POST"])
@admin_required
def zone_rename(zone_id):
    new_name = request.form.get("zone_name", "").strip()
    if not new_name:
        flash("Zone name is required.", "error")
        return redirect(url_for("zones_admin"))
    z = db.query("SELECT * FROM Zones WHERE ZoneID=?", (zone_id,), one=True)
    if not z:
        flash("Zone not found.", "error")
        return redirect(url_for("zones_admin"))
    old_name = z["ZoneName"]
    if new_name == old_name:
        return redirect(url_for("zones_admin"))
    try:
        db.execute("UPDATE Zones SET ZoneName=? WHERE ZoneID=?", (new_name, zone_id))
    except Exception:
        flash(f"Could not rename — '{new_name}' may already exist.", "error")
        return redirect(url_for("zones_admin"))
    # Keep existing customer records in sync with the renamed zone, since
    # Customers.Zone stores the text value directly rather than a foreign key.
    db.execute("UPDATE Customers SET Zone=? WHERE Zone=?", (new_name, old_name))
    flash(f"Zone renamed from '{old_name}' to '{new_name}' (existing customers updated).", "success")
    return redirect(url_for("zones_admin"))


@bp.route("/settings/zones/<int:zone_id>/toggle", methods=["POST"])
@admin_required
def zone_toggle(zone_id):
    z = db.query("SELECT * FROM Zones WHERE ZoneID=?", (zone_id,), one=True)
    if z:
        db.execute("UPDATE Zones SET Active=? WHERE ZoneID=?", (0 if z["Active"] else 1, zone_id))
    return redirect(url_for("zones_admin"))


@bp.route("/settings/zones/<int:zone_id>/delete", methods=["POST"])
@admin_required
def zone_delete(zone_id):
    db.execute("DELETE FROM Zones WHERE ZoneID=?", (zone_id,))
    flash("Zone deleted. Any customers already set to that zone keep the text value on their record.", "success")
    return redirect(url_for("zones_admin"))


# Display-only labels for the Customize Columns page, for modules that aren't part of the
# custom-fields system (CUSTOM_FIELD_MODULE_LABELS) but still have customizable list columns.
EXTRA_COLUMN_MODULE_LABELS = {"SalesLive": "Live Sales Monitor"}


FORM_FIELD_MODULE_LABELS = {"SaleFormHeader": "New/Edit Sale — Header Fields",
                             "SaleFormLine": "New/Edit Sale — Product Line Columns"}


@bp.route("/form-fields/<module>/customize", methods=["GET", "POST"])
@admin_required
def form_fields_customize(module):
    if module not in FORM_FIELD_MODULES:
        flash("Unknown form.", "error")
        return redirect(url_for("dashboard"))
    fields = get_effective_form_fields(module)
    always_visible = FORM_FIELD_ALWAYS_VISIBLE.get(module, set())
    if request.method == "POST":
        for f in fields:
            visible = 1 if (f["key"] in always_visible or request.form.get(f"visible_{f['key']}")) else 0
            try:
                order = int(request.form.get(f"order_{f['key']}", 0))
            except ValueError:
                order = 0
            db.execute("""INSERT INTO FormFieldLayout (ModuleName, FieldKey, Visible, DisplayOrder) VALUES (?,?,?,?)
                        ON CONFLICT(ModuleName, FieldKey) DO UPDATE SET Visible=excluded.Visible, DisplayOrder=excluded.DisplayOrder""",
                       (module, f["key"], visible, order))
        flash("Field layout saved.", "success")
        return redirect(request.form.get("return_to") or url_for("sale_form"))
    return render_template("form_fields_customize.html", module=module, fields=fields,
                            always_visible=always_visible,
                            module_label=FORM_FIELD_MODULE_LABELS.get(module, module))


@bp.route("/columns/<module>/customize", methods=["GET", "POST"])
def columns_customize(module):
    if module not in MODULE_COLUMNS:
        flash("Unknown list.", "error")
        return redirect(url_for("dashboard"))
    columns = get_effective_columns(module)
    if request.method == "POST":
        for col in columns:
            visible = 1 if request.form.get(f"visible_{col['key']}") else 0
            try:
                order = int(request.form.get(f"order_{col['key']}", 0))
            except ValueError:
                order = 0
            db.execute("""INSERT INTO ListViewColumns (ModuleName, ColumnKey, Visible, DisplayOrder) VALUES (?,?,?,?)
                        ON CONFLICT(ModuleName, ColumnKey) DO UPDATE SET Visible=excluded.Visible, DisplayOrder=excluded.DisplayOrder""",
                       (module, col["key"], visible, order))
        flash("Column layout saved.", "success")
        return redirect(request.form.get("return_to") or url_for("dashboard"))
    return render_template("columns_customize.html", module=module, columns=columns,
                            module_label=CUSTOM_FIELD_MODULE_LABELS.get(
                                module, EXTRA_COLUMN_MODULE_LABELS.get(module, module)))


@bp.route("/api/product/<int:pid>/gst")
def api_product_gst(pid):
    p = db.query("SELECT ProductID, ProductName, HSNCode, GSTRate, SellingPrice FROM Products WHERE ProductID=?",
                 (pid,), one=True)
    if not p:
        return jsonify({}), 404
    return jsonify(dict(hsn_code=p["HSNCode"] or "", gst_rate=p["GSTRate"] or 0, selling_price=p["SellingPrice"]))


@bp.route("/api/employee/<int:employee_id>/direct-sales")
def api_employee_direct_sales(employee_id):
    """JSON used by the New Stock Issue form: once a Salesperson + Date are both picked
    (before the Issue itself exists yet, so the server-side warning in stock_issue_add_lines()
    can't run), fetch whatever this employee already sold straight from the warehouse that
    date, bypassing a Stock Issue - see direct_sales_today_for_employee()."""
    sale_date = (request.args.get("date") or "").strip()
    if not sale_date:
        return jsonify([])
    rows = direct_sales_today_for_employee(employee_id, sale_date)
    return jsonify([{"product": r["ProductName"], "qty": r["Qty"]} for r in rows])


@bp.route("/api/customer/<int:cid>")
def api_customer(cid):
    c = db.query("SELECT CustomerID, CustomerName, GSTIN, State, StateCode, Address FROM Customers WHERE CustomerID=?",
                 (cid,), one=True)
    if not c:
        return jsonify({}), 404
    return jsonify(dict(c))


_SQL_QUERY_BLOCKLIST = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex)\b", re.IGNORECASE)


@bp.route("/admin/query", methods=["GET", "POST"])
@admin_required
def admin_query():
    """Admin-only ad hoc data lookup, straight from the browser - so a question like
    "what's in invoice X" or "how many of product Y sold today" can be answered here
    instead of writing a one-off script and running it via `railway ssh` every time.

    Only a single SELECT statement is allowed. This is enforced two ways: a keyword
    blocklist rejects anything containing a write/DDL keyword before it's even run, and
    the query itself executes against a connection opened read-only at the SQLite engine
    level (file:...?mode=ro) - so even if the blocklist were somehow bypassed, the
    database file cannot be modified through this page. Results are capped at 500 rows."""
    sql_text = ""
    columns = None
    rows = None
    error = None
    row_count = None
    if request.method == "POST":
        sql_text = (request.form.get("sql") or "").strip()
        stripped = sql_text.rstrip(";").strip()
        if not stripped:
            error = "Enter a query."
        elif not re.match(r"(?is)^\s*select\b", stripped):
            error = "Only SELECT queries are allowed."
        elif ";" in stripped:
            error = "Only a single statement is allowed (no semicolons inside the query)."
        elif _SQL_QUERY_BLOCKLIST.search(stripped):
            error = "Only read-only SELECT queries are allowed - that keyword isn't permitted."
        else:
            import sqlite3
            try:
                ro_conn = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True, timeout=5)
                ro_conn.row_factory = sqlite3.Row
                try:
                    cur = ro_conn.execute(stripped)
                    fetched = cur.fetchmany(500)
                    columns = [d[0] for d in cur.description] if cur.description else []
                    rows = [list(r) for r in fetched]
                    row_count = len(rows)
                finally:
                    ro_conn.close()
            except sqlite3.Error as e:
                error = f"Query error: {e}"
    return render_template("admin_query.html", sql_text=sql_text, columns=columns, rows=rows,
                            error=error, row_count=row_count)


# ---------------------------------------------------------------------
# Field Location Tracking - captures a Staff/Supervisor/Manager's device
# location every 30 min while they have the app open, during working hours,
# so their movement through the day can be reviewed on a map. This only
# works while the browser tab is open and the user has granted location
# permission - there is no way for a website to track location once the tab
# is closed or the phone is locked, so this is a best-effort "while working
# in the app" trail, not a full-time GPS tracker.
# ---------------------------------------------------------------------


LOCATION_WORK_START_HOUR = 9    # 9 AM


LOCATION_WORK_END_HOUR = 20     # 8 PM


DEFAULT_LOCATION_CONSENT_TEXT = (
    "As part of your role, this application records your device's GPS location periodically "
    "during the working days and hours configured by your employer. Location is captured only "
    "while you are logged in and this browser tab remains open - it is never tracked outside "
    "configured working hours, on a configured day off, or after you log out.\n\n"
    "This is used for operational purposes: confirming field visits, route verification, and "
    "locating you in the event of a work-related emergency during working hours.\n\n"
    "By clicking \"I Accept\", you acknowledge and consent to this location tracking for the "
    "duration of this login session. You may decline by logging out instead, though continued "
    "use of this application during working hours requires accepting this notice at every login."
)


def _within_location_tracking_hours(dt, company=None):
    today_abbr = LOCATION_WEEKDAY_ABBR[dt.weekday()]
    return today_abbr in _location_working_days(company) and LOCATION_WORK_START_HOUR <= dt.hour < LOCATION_WORK_END_HOUR


@bp.route("/api/location-ping", methods=["POST"])
def api_location_ping():
    """Called every ~30 min by the location-tracking JS (see base.html) from
    any tracked-role user's open browser tab. Re-checks role and working
    hours server-side using the server clock - a device's local clock isn't
    trusted for the actual gate, only used client-side to decide when to
    bother trying."""
    user = get_current_user()
    if not user or user["Role"] not in LOCATION_TRACKED_ROLES:
        return jsonify(ok=False, reason="not-tracked"), 200
    now = _now_ist()
    if not _within_location_tracking_hours(now):
        return jsonify(ok=False, reason="outside-hours"), 200
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
    except (TypeError, ValueError):
        return jsonify(ok=False, reason="bad-coords"), 200
    accuracy = data.get("accuracy")
    try:
        accuracy = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy = None
    db.execute(
        "INSERT INTO LocationLogs (UserID, EmployeeID, Role, Latitude, Longitude, Accuracy, RecordedAt, RecordedDate) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (user["UserID"], user["EmployeeID"], user["Role"], lat, lng, accuracy,
         now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d")))
    return jsonify(ok=True)


@bp.route("/location-consent", methods=["GET", "POST"])
def location_consent():
    """Mandatory accept-to-continue screen, re-shown at every login for a
    tracked role (require_login() redirects here until session['location_consent_ok']
    is set, and that flag is wiped by session.clear() at login - see login())."""
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    if user["Role"] not in LOCATION_TRACKED_ROLES:
        return redirect(default_landing_url(user))  # not a tracked role - nothing to consent to
    nxt = request.values.get("next", "")
    if request.method == "POST":
        session["location_consent_ok"] = True
        db.execute("INSERT INTO LocationConsentLog (UserID) VALUES (?)", (user["UserID"],))
        return redirect(nxt if nxt.startswith("/") else default_landing_url(user))
    company = get_company_settings()
    consent_text = company["LocationConsentText"] or DEFAULT_LOCATION_CONSENT_TEXT
    return render_template("location_consent.html", consent_text=consent_text, next=nxt)


def get_visit_plan(employee_id, visit_date):
    """The Visit Plan (if any) for this employee/date, with its stops resolved - an existing
    Customer's name/location always comes live from Customers (so a later correction there is
    reflected automatically, and a customer that's since lost its saved location shows that),
    while an ad-hoc stop carries its own name/location. Returns None if no plan has been
    created yet for that employee/date (distinct from a plan that exists with zero stops)."""
    plan = db.query("SELECT * FROM VisitPlans WHERE EmployeeID=? AND VisitDate=?",
                     (employee_id, visit_date), one=True)
    if not plan:
        return None
    stops = db.query("""
        SELECT vps.StopID, vps.CustomerID, vps.VisitedAt, vps.VisitedByUsername, vps.VisitedDistanceMeters,
               COALESCE(c.CustomerName, vps.StopName) AS Name,
               COALESCE(c.Latitude, vps.Latitude) AS Latitude,
               COALESCE(c.Longitude, vps.Longitude) AS Longitude,
               (c.CustomerID IS NOT NULL AND (c.Latitude IS NULL OR c.Longitude IS NULL)) AS CustomerLostLocation
        FROM VisitPlanStops vps
        LEFT JOIN Customers c ON c.CustomerID = vps.CustomerID
        WHERE vps.VisitPlanID=? ORDER BY vps.SortOrder, vps.StopID
    """, (plan["VisitPlanID"],))
    return {"plan": plan, "stops": [dict(s) for s in stops]}


def _visit_plan_manage_check():
    """Admin/Manager only - building/editing a Visit Plan is a planning
    action, not something a Staff rep does for themselves (they can still
    view their own and mark stops visited - see visit_plan_stop_toggle())."""
    user = get_current_user()
    if not user or user["Role"] not in ("Admin", "Manager"):
        flash("Only Admin/Manager accounts can manage a Visit Plan.", "error")
        return None
    return user


def _haversine_meters(lat1, lng1, lat2, lng2):
    """Great-circle distance between two lat/lng points, in meters - used by the Visit Plan
    geofence check (visit_plan_stop_toggle()) to verify a rep is actually near the stop they're
    marking visited, not an exact road distance."""
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def get_visit_plan_template(template_id):
    """A saved template with its stops resolved - same live-vs-snapshot shape as
    get_visit_plan() above (a Customer stop's name/location always comes live from
    Customers; an ad-hoc stop carries its own). Returns None if the template doesn't
    exist."""
    template = db.query("SELECT * FROM VisitPlanTemplates WHERE TemplateID=?", (template_id,), one=True)
    if not template:
        return None
    stops = db.query("""
        SELECT vpts.TemplateStopID, vpts.CustomerID,
               COALESCE(c.CustomerName, vpts.StopName) AS Name,
               COALESCE(c.Latitude, vpts.Latitude) AS Latitude,
               COALESCE(c.Longitude, vpts.Longitude) AS Longitude,
               (c.CustomerID IS NOT NULL AND (c.Latitude IS NULL OR c.Longitude IS NULL)) AS CustomerLostLocation
        FROM VisitPlanTemplateStops vpts
        LEFT JOIN Customers c ON c.CustomerID = vpts.CustomerID
        WHERE vpts.TemplateID=? ORDER BY vpts.SortOrder, vpts.TemplateStopID
    """, (template_id,))
    return {"template": template, "stops": [dict(s) for s in stops]}


@bp.route("/visit-plans/<int:employee_id>/<date_str>/add-stop", methods=["POST"])
def visit_plan_add_stop(employee_id, date_str):
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))

    # Validate first, and only create the Visit Plan row (if one doesn't already exist for
    # this employee/date) once there's an actual valid stop to insert into it - an Admin
    # mis-picking an unlocated customer, or leaving an ad-hoc stop's location uncaptured,
    # should never leave behind an empty, orphaned plan.
    mode = request.form.get("mode", "customer")
    customer_id = None
    customer_name = None
    stop_name = None
    lat = lng = None
    if mode == "customer":
        customer_id = request.form.get("customer_id", type=int)
        customer = db.query("SELECT CustomerID, CustomerName, Latitude, Longitude FROM Customers WHERE CustomerID=?",
                             (customer_id,), one=True) if customer_id else None
        if not customer or customer["Latitude"] is None or customer["Longitude"] is None:
            flash("That customer doesn't have a saved map location yet - set one on their Customer page first.", "error")
            return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))
        customer_name = customer["CustomerName"]
    else:
        stop_name = (request.form.get("stop_name") or "").strip()
        try:
            lat = float(request.form.get("latitude"))
            lng = float(request.form.get("longitude"))
        except (TypeError, ValueError):
            lat = lng = None
        if not stop_name or lat is None or lng is None:
            flash("A custom stop needs a name and a captured location.", "error")
            return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))

    plan = db.query("SELECT VisitPlanID FROM VisitPlans WHERE EmployeeID=? AND VisitDate=?",
                     (employee_id, date_str), one=True)
    plan_id = plan["VisitPlanID"] if plan else db.execute(
        "INSERT INTO VisitPlans (EmployeeID, VisitDate, CreatedByUserID, CreatedByUsername) VALUES (?,?,?,?)",
        (employee_id, date_str, user["UserID"], user["Username"]))
    next_sort = db.query("SELECT COALESCE(MAX(SortOrder),0)+1 n FROM VisitPlanStops WHERE VisitPlanID=?",
                          (plan_id,), one=True)["n"]

    if mode == "customer":
        db.execute("INSERT INTO VisitPlanStops (VisitPlanID, CustomerID, SortOrder) VALUES (?,?,?)",
                   (plan_id, customer_id, next_sort))
        flash(f"Added {customer_name} to today's visit plan.", "success")
    else:
        db.execute("""INSERT INTO VisitPlanStops (VisitPlanID, StopName, Latitude, Longitude, SortOrder)
                    VALUES (?,?,?,?,?)""", (plan_id, stop_name, lat, lng, next_sort))
        flash(f"Added '{stop_name}' to today's visit plan.", "success")
    return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))


@bp.route("/visit-plans/stops/<int:stop_id>/toggle-visited", methods=["POST"])
def visit_plan_stop_toggle(stop_id):
    user = get_current_user()
    stop = db.query("""
        SELECT vps.StopID, vps.VisitedAt, vp.EmployeeID, vp.VisitDate,
               COALESCE(c.Latitude, vps.Latitude) AS Latitude, COALESCE(c.Longitude, vps.Longitude) AS Longitude
        FROM VisitPlanStops vps JOIN VisitPlans vp ON vp.VisitPlanID = vps.VisitPlanID
             LEFT JOIN Customers c ON c.CustomerID = vps.CustomerID
        WHERE vps.StopID=?""", (stop_id,), one=True)
    if not stop:
        flash("Visit plan stop not found.", "error")
        return redirect(url_for("location_tracking_view"))
    is_own_stop = bool(user and user["EmployeeID"] is not None and user["EmployeeID"] == stop["EmployeeID"])
    allowed = user and (user["Role"] in ("Admin", "Manager") or is_own_stop)
    if not allowed:
        flash("You can't update this visit plan.", "error")
        return redirect(url_for("location_tracking_view", date=stop["VisitDate"], employee_id=stop["EmployeeID"]))

    if stop["VisitedAt"]:
        db.execute("""UPDATE VisitPlanStops SET VisitedAt=NULL, VisitedByUserID=NULL, VisitedByUsername=NULL,
                    VisitedDistanceMeters=NULL WHERE StopID=?""", (stop_id,))
        return redirect(url_for("location_tracking_view", date=stop["VisitDate"], employee_id=stop["EmployeeID"]))

    # Marking visited: a rep confirming their OWN stop has to prove (via the browser's current
    # location, captured client-side right before this submits) that they're actually within the
    # configured radius of it - this is the geofence check. An Admin/Manager marking it on behalf
    # of someone else's stop is an override and skips this (they're not expected to be standing
    # there themselves).
    distance_m = None
    if is_own_stop:
        if stop["Latitude"] is None or stop["Longitude"] is None:
            flash("This stop has no location to check against - ask an Admin/Manager to mark it visited.", "error")
            return redirect(url_for("location_tracking_view", date=stop["VisitDate"], employee_id=stop["EmployeeID"]))
        try:
            user_lat = float(request.form.get("latitude"))
            user_lng = float(request.form.get("longitude"))
        except (TypeError, ValueError):
            flash("Couldn't get your current location - check the browser's location permission and try again.",
                  "error")
            return redirect(url_for("location_tracking_view", date=stop["VisitDate"], employee_id=stop["EmployeeID"]))
        distance_m = _haversine_meters(user_lat, user_lng, stop["Latitude"], stop["Longitude"])
        radius_m = get_company_settings()["VisitGeofenceRadiusMeters"] or 200
        if distance_m > radius_m:
            flash(f"You're {distance_m:.0f}m away from this stop - get within {radius_m}m to mark it visited.",
                  "error")
            return redirect(url_for("location_tracking_view", date=stop["VisitDate"], employee_id=stop["EmployeeID"]))

    db.execute("""UPDATE VisitPlanStops SET VisitedAt=?, VisitedByUserID=?, VisitedByUsername=?,
                VisitedDistanceMeters=? WHERE StopID=?""",
               (_now_ist().strftime("%Y-%m-%d %H:%M:%S"), user["UserID"], user["Username"], distance_m, stop_id))
    return redirect(url_for("location_tracking_view", date=stop["VisitDate"], employee_id=stop["EmployeeID"]))


@bp.route("/visit-plans/stops/<int:stop_id>/delete", methods=["POST"])
def visit_plan_stop_delete(stop_id):
    stop = db.query("""SELECT vps.StopID, vp.EmployeeID, vp.VisitDate FROM VisitPlanStops vps
                     JOIN VisitPlans vp ON vp.VisitPlanID = vps.VisitPlanID WHERE vps.StopID=?""",
                     (stop_id,), one=True)
    if not stop:
        flash("Visit plan stop not found.", "error")
        return redirect(url_for("location_tracking_view"))
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view", date=stop["VisitDate"], employee_id=stop["EmployeeID"]))
    db.execute("DELETE FROM VisitPlanStops WHERE StopID=?", (stop_id,))
    flash("Removed from the visit plan.", "success")
    return redirect(url_for("location_tracking_view", date=stop["VisitDate"], employee_id=stop["EmployeeID"]))


@bp.route("/visit-plans/<int:employee_id>/<date_str>/save-as-template", methods=["POST"])
def visit_plan_save_as_template(employee_id, date_str):
    """Snapshot a day's Visit Plan stops into a named, reusable template -
    not tied to the employee/date it was saved from, so it can be applied
    to any employee/date later (see visit_plan_apply_template)."""
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))

    name = (request.form.get("name") or "").strip()
    plan = get_visit_plan(employee_id, date_str)
    if not name or not plan or not plan["stops"]:
        flash("Can't save an empty visit plan as a template - add some stops first.", "error")
        return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))

    with db.transaction() as conn:
        template_id = conn.execute(
            "INSERT INTO VisitPlanTemplates (Name, CreatedByUserID, CreatedByUsername) VALUES (?,?,?)",
            (name, user["UserID"], user["Username"])).lastrowid
        for i, s in enumerate(plan["stops"], start=1):
            conn.execute("""INSERT INTO VisitPlanTemplateStops
                          (TemplateID, CustomerID, StopName, Latitude, Longitude, SortOrder)
                          VALUES (?,?,?,?,?,?)""",
                         (template_id, s["CustomerID"], None if s["CustomerID"] else s["Name"],
                          s["Latitude"], s["Longitude"], i))
    flash(f"Saved today's visit plan as template '{name}'.", "success")
    return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))


@bp.route("/visit-plans/templates")
def visit_plan_templates_list():
    """Admin/Manager landing page for managing saved templates - separate from any one
    employee/date's Visit Plan, since a template isn't tied to either."""
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view"))
    templates = db.query("""SELECT t.*, (SELECT COUNT(*) FROM VisitPlanTemplateStops s
                           WHERE s.TemplateID=t.TemplateID) AS StopCount
                           FROM VisitPlanTemplates t ORDER BY t.Name""")
    return render_template("visit_plan_templates_list.html", templates=templates)


@bp.route("/visit-plans/templates/<int:template_id>")
def visit_plan_template_detail(template_id):
    """One template's stops, in order, with controls to reorder/remove them or rename/delete
    the template itself."""
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view"))
    template = get_visit_plan_template(template_id)
    if not template:
        flash("Template not found.", "error")
        return redirect(url_for("visit_plan_templates_list"))
    return render_template("visit_plan_template_detail.html", template=template)


@bp.route("/visit-plans/templates/<int:template_id>/rename", methods=["POST"])
def visit_plan_template_rename(template_id):
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view"))
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Template name can't be blank.", "error")
        return redirect(url_for("visit_plan_template_detail", template_id=template_id))
    db.execute("UPDATE VisitPlanTemplates SET Name=? WHERE TemplateID=?", (name, template_id))
    flash("Template renamed.", "success")
    return redirect(url_for("visit_plan_template_detail", template_id=template_id))


def _visit_plan_template_stop_move(template_stop_id, direction):
    """Swaps this stop's SortOrder with its neighbor (direction=-1 for up, +1 for down) within
    the same template - a no-op at either end of the list. Returns the TemplateID (for the
    redirect) or None if the stop doesn't exist."""
    stop = db.query("SELECT * FROM VisitPlanTemplateStops WHERE TemplateStopID=?",
                     (template_stop_id,), one=True)
    if not stop:
        return None
    ordered = db.query("SELECT TemplateStopID, SortOrder FROM VisitPlanTemplateStops "
                        "WHERE TemplateID=? ORDER BY SortOrder, TemplateStopID", (stop["TemplateID"],))
    idx = next(i for i, s in enumerate(ordered) if s["TemplateStopID"] == template_stop_id)
    neighbor_idx = idx + direction
    if 0 <= neighbor_idx < len(ordered):
        neighbor = ordered[neighbor_idx]
        with db.transaction() as conn:
            conn.execute("UPDATE VisitPlanTemplateStops SET SortOrder=? WHERE TemplateStopID=?",
                         (neighbor["SortOrder"], template_stop_id))
            conn.execute("UPDATE VisitPlanTemplateStops SET SortOrder=? WHERE TemplateStopID=?",
                         (ordered[idx]["SortOrder"], neighbor["TemplateStopID"]))
    return stop["TemplateID"]


@bp.route("/visit-plans/templates/stops/<int:template_stop_id>/move-up", methods=["POST"])
def visit_plan_template_stop_move_up(template_stop_id):
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view"))
    template_id = _visit_plan_template_stop_move(template_stop_id, -1)
    if not template_id:
        flash("Template stop not found.", "error")
        return redirect(url_for("visit_plan_templates_list"))
    return redirect(url_for("visit_plan_template_detail", template_id=template_id))


@bp.route("/visit-plans/templates/stops/<int:template_stop_id>/move-down", methods=["POST"])
def visit_plan_template_stop_move_down(template_stop_id):
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view"))
    template_id = _visit_plan_template_stop_move(template_stop_id, 1)
    if not template_id:
        flash("Template stop not found.", "error")
        return redirect(url_for("visit_plan_templates_list"))
    return redirect(url_for("visit_plan_template_detail", template_id=template_id))


@bp.route("/visit-plans/templates/stops/<int:template_stop_id>/delete", methods=["POST"])
def visit_plan_template_stop_delete(template_stop_id):
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view"))
    stop = db.query("SELECT TemplateID FROM VisitPlanTemplateStops WHERE TemplateStopID=?",
                     (template_stop_id,), one=True)
    if not stop:
        flash("Template stop not found.", "error")
        return redirect(url_for("visit_plan_templates_list"))
    db.execute("DELETE FROM VisitPlanTemplateStops WHERE TemplateStopID=?", (template_stop_id,))
    flash("Removed from the template.", "success")
    return redirect(url_for("visit_plan_template_detail", template_id=stop["TemplateID"]))


@bp.route("/visit-plans/templates/<int:template_id>/delete", methods=["POST"])
def visit_plan_template_delete(template_id):
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view"))
    db.execute("DELETE FROM VisitPlanTemplates WHERE TemplateID=?", (template_id,))
    flash("Template deleted.", "success")
    next_url = request.referrer or url_for("location_tracking_view")
    if next_url.rstrip("/").endswith(f"/visit-plans/templates/{template_id}"):
        next_url = url_for("visit_plan_templates_list")
    return redirect(next_url)


@bp.route("/visit-plans/<int:employee_id>/<date_str>/apply-template/<int:template_id>", methods=["POST"])
def visit_plan_apply_template(employee_id, date_str, template_id):
    """Copies a saved template's stops onto this employee/date's Visit Plan - a Customer stop
    is skipped if that customer has since lost its saved location, or is already on this plan
    (same dedup as the Add Stop dropdown's candidate list); an ad-hoc stop is always copied as-is."""
    user = _visit_plan_manage_check()
    if not user:
        return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))

    template = db.query("SELECT * FROM VisitPlanTemplates WHERE TemplateID=?", (template_id,), one=True)
    if not template:
        flash("Template not found.", "error")
        return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))
    template_stops = db.query(
        "SELECT * FROM VisitPlanTemplateStops WHERE TemplateID=? ORDER BY SortOrder, TemplateStopID",
        (template_id,))

    existing_plan = get_visit_plan(employee_id, date_str)
    existing_customer_ids = {s["CustomerID"] for s in existing_plan["stops"] if s["CustomerID"]} if existing_plan else set()

    plan = db.query("SELECT VisitPlanID FROM VisitPlans WHERE EmployeeID=? AND VisitDate=?",
                     (employee_id, date_str), one=True)
    plan_id = plan["VisitPlanID"] if plan else None
    next_sort = (db.query("SELECT COALESCE(MAX(SortOrder),0) n FROM VisitPlanStops WHERE VisitPlanID=?",
                           (plan_id,), one=True)["n"] if plan_id else 0) + 1

    added = skipped = 0
    for ts in template_stops:
        if ts["CustomerID"]:
            if ts["CustomerID"] in existing_customer_ids:
                skipped += 1
                continue
            customer = db.query("SELECT CustomerID FROM Customers WHERE CustomerID=? AND Latitude IS NOT NULL "
                                 "AND Longitude IS NOT NULL", (ts["CustomerID"],), one=True)
            if not customer:
                skipped += 1
                continue
        if plan_id is None:
            plan_id = db.execute(
                "INSERT INTO VisitPlans (EmployeeID, VisitDate, CreatedByUserID, CreatedByUsername) VALUES (?,?,?,?)",
                (employee_id, date_str, user["UserID"], user["Username"]))
        db.execute("""INSERT INTO VisitPlanStops (VisitPlanID, CustomerID, StopName, Latitude, Longitude, SortOrder)
                    VALUES (?,?,?,?,?,?)""",
                   (plan_id, ts["CustomerID"], ts["StopName"], ts["Latitude"], ts["Longitude"], next_sort))
        next_sort += 1
        added += 1
        if ts["CustomerID"]:
            existing_customer_ids.add(ts["CustomerID"])

    if added:
        msg = f"Applied template '{template['Name']}': added {added} stop(s)."
        if skipped:
            msg += f" Skipped {skipped} (already on this plan or missing a saved location)."
        flash(msg, "success")
    else:
        flash(f"Nothing to add from '{template['Name']}' - every stop is already on this plan "
              "or no longer has a saved location.", "error")
    return redirect(url_for("location_tracking_view", date=date_str, employee_id=employee_id))


@bp.route("/location-tracking")
def location_tracking_view():
    date_str = request.args.get("date") or today_str()
    employee_id = request.args.get("employee_id", type=int)

    tracked_users = db.query(
        "SELECT u.UserID, u.FullName, u.Username, u.Role, u.EmployeeID, e.EmployeeName "
        "FROM Users u LEFT JOIN Employees e ON e.EmployeeID = u.EmployeeID "
        "WHERE u.Role IN ('Staff','Supervisor','Manager') AND u.Active=1 ORDER BY u.FullName")

    params = [date_str]
    emp_filter_sql = ""
    if employee_id:
        emp_filter_sql = " AND l.UserID IN (SELECT UserID FROM Users WHERE EmployeeID=?)"
        params.append(employee_id)
    # Sale-tagged points (RefType='Sale') get the invoice/customer/amount they belong to,
    # so the map popup can show what was actually sold there - see log_sale_location().
    points = db.query(
        "SELECT l.*, u.FullName, u.Username, s.InvoiceNumber, s.TotalAmount, c.CustomerName "
        "FROM LocationLogs l JOIN Users u ON u.UserID = l.UserID "
        "LEFT JOIN Sales s ON l.RefType='Sale' AND s.SaleID = l.RefID "
        "LEFT JOIN Customers c ON c.CustomerID = s.CustomerID "
        "WHERE l.RecordedDate=?" + emp_filter_sql + " ORDER BY u.FullName, l.RecordedAt", tuple(params))

    by_user = {}
    for p in points:
        by_user.setdefault(p["UserID"], {"name": p["FullName"] or p["Username"], "points": []})
        point = {
            "lat": p["Latitude"], "lng": p["Longitude"], "accuracy": p["Accuracy"],
            "time": p["RecordedAt"][11:16] if len(p["RecordedAt"]) >= 16 else p["RecordedAt"],
            "is_sale": p["RefType"] == "Sale",
        }
        if point["is_sale"]:
            point["sale_id"] = p["RefID"]
            point["invoice"] = p["InvoiceNumber"]
            point["customer"] = p["CustomerName"]
            point["amount"] = p["TotalAmount"]
        by_user[p["UserID"]]["points"].append(point)

    is_today = date_str == today_str()
    tracking_active_now = is_today and _within_location_tracking_hours(_now_ist())
    company = get_company_settings()
    configured_days = _location_working_days(company)
    working_days_display = ", ".join(d for d in LOCATION_WEEKDAY_ABBR if d in configured_days)

    # Today's Visit Plan - only meaningful once a specific person is picked (it's one plan per
    # employee/date; "All tracked people" has no single plan to show).
    current = get_current_user()
    visit_plan = None
    can_manage_visit_plan = False
    visit_plan_candidates = []
    customers_without_location_count = 0
    visit_plan_templates = []
    if employee_id:
        visit_plan = get_visit_plan(employee_id, date_str)
        can_manage_visit_plan = bool(current and current["Role"] in ("Admin", "Manager"))
        if can_manage_visit_plan:
            existing_customer_ids = {s["CustomerID"] for s in visit_plan["stops"] if s["CustomerID"]} if visit_plan else set()
            all_located = db.query("""SELECT CustomerID, CustomerName FROM Customers
                                    WHERE Active=1 AND Latitude IS NOT NULL AND Longitude IS NOT NULL
                                    ORDER BY CustomerName""")
            visit_plan_candidates = [c for c in all_located if c["CustomerID"] not in existing_customer_ids]
            customers_without_location_count = db.query(
                """SELECT COUNT(*) c FROM Customers WHERE Active=1
                   AND (Latitude IS NULL OR Longitude IS NULL)""", one=True)["c"]
            visit_plan_templates = db.query("SELECT TemplateID, Name FROM VisitPlanTemplates ORDER BY Name")

    return render_template("location_tracking.html", date_str=date_str, today=today_str(),
                            tracked_users=tracked_users, employee_id=employee_id,
                            by_user=by_user, point_count=len(points),
                            tracking_active_now=tracking_active_now,
                            working_days_display=working_days_display,
                            ping_interval_minutes=company["LocationPingIntervalMinutes"] or 30,
                            visit_plan=visit_plan, can_manage_visit_plan=can_manage_visit_plan,
                            visit_plan_candidates=visit_plan_candidates,
                            customers_without_location_count=customers_without_location_count,
                            visit_plan_templates=visit_plan_templates,
                            can_mark_visited=bool(current and (can_manage_visit_plan
                                or current["EmployeeID"] == employee_id)),
                            is_self_marking=bool(current and current["EmployeeID"] is not None
                                and current["EmployeeID"] == employee_id),
                            visit_geofence_radius_meters=company["VisitGeofenceRadiusMeters"] or 200)


# ---------------------------------------------------------------------
# Stock Returns Variance - per-salesperson, per-month view of the existing
# Unaccounted figure (Qty Issued - Qty Sold - Qty Returned - Qty Free) from
# Reconciled Stock Issues, in units and valued at cost. Dashboard widget +
# drill-down popup, "similar to Sales Target" per the request that added it.
# ---------------------------------------------------------------------


def get_stock_returns_detail(employee_id, year, month):
    """Line-level detail behind get_stock_returns_summary() for ONE salesperson/month - which
    Stock Issues/products contributed to that employee's total. Lines that balanced exactly
    (variance 0) are left out - the drill-down is for investigating what's off, not a full
    reconciliation reprint (that's what the issue's own detail page is for)."""
    month_start = date(year, month, 1).isoformat()
    month_end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    rows = db.query("""
        SELECT si.IssueID, si.IssueNumber, si.IssueDate, pr.ProductName, pr.Unit,
               sil.QtyIssued, COALESCE(sil.QtySold,0) AS QtySold,
               COALESCE(sil.QtyReturned,0) AS QtyReturned, COALESCE(sil.QtyFree,0) AS QtyFree,
               pr.CostPrice
        FROM StockIssues si
        JOIN StockIssueLines sil ON sil.IssueID = si.IssueID
        JOIN Products pr ON pr.ProductID = sil.ProductID
        WHERE si.Status='Reconciled' AND si.EmployeeID=? AND si.IssueDate BETWEEN ? AND ?
        ORDER BY si.IssueDate, si.IssueID, pr.ProductName
    """, (employee_id, month_start, month_end))
    detail = []
    for r in rows:
        variance = round((r["QtyIssued"] or 0) - r["QtySold"] - r["QtyReturned"] - r["QtyFree"], 2)
        if abs(variance) < 0.001:
            continue
        detail.append({
            "issue_id": r["IssueID"], "issue_number": r["IssueNumber"] or f"#{r['IssueID']}",
            "date": r["IssueDate"], "product": r["ProductName"], "unit": r["Unit"],
            "qty_issued": r["QtyIssued"], "qty_sold": r["QtySold"], "qty_returned": r["QtyReturned"],
            "qty_free": r["QtyFree"], "variance": variance,
            "value": round(variance * (r["CostPrice"] or 0), 2),
        })
    return detail


@bp.route("/api/stock-returns/<int:employee_id>")
@admin_required
def api_stock_returns_detail(employee_id):
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    employee = db.query("SELECT EmployeeName FROM Employees WHERE EmployeeID=?", (employee_id,), one=True)
    if not employee:
        return jsonify({"error": "Employee not found"}), 404
    lines = get_stock_returns_detail(employee_id, year, month)
    return jsonify({"employee_name": employee["EmployeeName"], "year": year, "month": month, "lines": lines})


# ---------------------------------------------------------------------
# Company Settings (used on GST invoices)
# ---------------------------------------------------------------------


@bp.route("/settings", methods=["GET", "POST"])
def settings_form():
    if request.method == "POST":
        f = request.form
        db.execute("""UPDATE CompanySettings SET CompanyName=?, GSTIN=?, PAN=?, Address=?, City=?,
                    State=?, StateCode=?, Pincode=?, Phone=?, Email=?, BankName=?, BankAccountName=?,
                    BankAccountNumber=?, BankIFSC=?, BankBranch=?, UpiId=?, InvoicePrefix=?, InvoiceTerms=?,
                    GstFilingScheme=?, GstRemindersEnabled=?, GstReminderEmails=?, GstReminderDaysBefore=?,
                    SmtpHost=?, SmtpPort=?, SmtpUsername=?, SmtpPassword=?, SmtpFromEmail=?, SmtpUseTLS=?,
                    CreditControlMode=?, CreditBlockDays=?, LocationTrackingWorkingDays=?, LocationConsentText=?,
                    LocationPingIntervalMinutes=?, VisitGeofenceRadiusMeters=?, ARReminderOverdueDays=?,
                    ARReminderDailyAfterDays=?
                    WHERE SettingsID=1""",
                   (f["company_name"], f["gstin"], f["pan"], f["address"], f["city"],
                    STATE_NAME_BY_CODE.get(f.get("state_code", ""), ""), f.get("state_code", ""), f["pincode"],
                    f["phone"], f["email"], f["bank_name"], f["bank_account_name"], f["bank_account_number"],
                    f["bank_ifsc"], f["bank_branch"], f.get("upi_id", "").strip() or None,
                    f["invoice_prefix"] or "INV", f["invoice_terms"],
                    f.get("gst_filing_scheme") if f.get("gst_filing_scheme") in ("Monthly", "QRMP") else "Monthly",
                    1 if f.get("gst_reminders_enabled") else 0, f.get("gst_reminder_emails", "").strip(),
                    int(f.get("gst_reminder_days_before") or 3),
                    f.get("smtp_host", "").strip(), int(f.get("smtp_port") or 587),
                    f.get("smtp_username", "").strip(), f.get("smtp_password", ""),
                    f.get("smtp_from_email", "").strip(), 1 if f.get("smtp_use_tls") else 0,
                    f.get("credit_control_mode") if f.get("credit_control_mode") in
                        ("Informational", "BlockAfterDays", "BlockImmediate") else "Informational",
                    max(int(f.get("credit_block_days") or 0), 0),
                    ",".join(d for d in LOCATION_WEEKDAY_ABBR if d in set(request.form.getlist("working_day")))
                        or DEFAULT_LOCATION_WORKING_DAYS,
                    f.get("location_consent_text", "").strip() or None,
                    min(max(int(f.get("location_ping_interval_minutes") or 30), 1), 720),
                    min(max(int(f.get("visit_geofence_radius_meters") or 200), 10), 5000),
                    min(max(int(f.get("ar_reminder_overdue_days") or 7), 1), 365),
                    min(max(int(f.get("ar_reminder_daily_after_days") or 14), 1), 365)))
        flash("Company settings saved. These details now appear on every GST invoice.", "success")
        return redirect(url_for("settings_form"))
    company = get_company_settings()
    working_days_set = _location_working_days(company)
    return render_template("settings_form.html", company=company, states=INDIAN_STATES,
                            location_weekdays=LOCATION_WEEKDAY_ABBR, working_days_set=working_days_set,
                            default_location_consent_text=DEFAULT_LOCATION_CONSENT_TEXT)


@bp.route("/settings/gst-test-email", methods=["POST"])
def settings_gst_test_email():
    company = get_company_settings()
    to_address = (request.form.get("test_email") or "").strip()
    if not to_address:
        flash("Enter an email address to send the test to.", "error")
    else:
        ok, message = gst_reminders.send_test_email(company, to_address)
        flash(message, "success" if ok else "error")
    return redirect(url_for("settings_form"))


# ---------------------------------------------------------------------
# Vehicle Maintenance
# ---------------------------------------------------------------------


SERVICE_TYPES = ["Oil Change", "Tyre Replacement", "General Service", "Repair",
                  "Insurance Renewal", "Permit Renewal", "PUC Renewal", "Fitness Renewal", "Other"]


@bp.route("/maintenance")
def maintenance_list():
    records = db.query("""SELECT vm.*, v.RegistrationNumber FROM VehicleMaintenance vm
                        JOIN Vehicles v ON v.VehicleID=vm.VehicleID ORDER BY vm.ServiceDate DESC, vm.MaintenanceID DESC""")
    return render_template("maintenance_list.html", records=records, due=upcoming_maintenance(days=30),
                            columns=get_effective_columns("Maintenance"))


@bp.route("/maintenance/new", methods=["GET", "POST"])
def maintenance_form():
    if request.method == "POST":
        f = request.form
        new_id = db.execute("""INSERT INTO VehicleMaintenance (VehicleID, ServiceType, ServiceDate, Odometer,
                    NextDueDate, NextDueOdometer, Cost, ServiceCenter, Status, Notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (int(f["vehicle_id"]), f["service_type"], f["service_date"],
                    float(f["odometer"] or 0), f.get("next_due_date") or None,
                    float(f["next_due_odometer"]) if f.get("next_due_odometer") else None,
                    float(f["cost"] or 0), f.get("service_center", ""), "Completed", f.get("notes", "")))
        if f.get("odometer"):
            db.execute("UPDATE Vehicles SET CurrentOdometer=? WHERE VehicleID=? AND CurrentOdometer<?",
                        (float(f["odometer"]), int(f["vehicle_id"]), float(f["odometer"])))
        save_custom_fields("Maintenance", new_id, f)
        flash("Maintenance record saved.", "success")
        return redirect(url_for("maintenance_list"))
    vehicles = db.query("SELECT * FROM Vehicles ORDER BY RegistrationNumber")
    custom_fields = get_custom_field_defs("Maintenance")
    return render_template("maintenance_form.html", vehicles=vehicles, service_types=SERVICE_TYPES, today=today_str(),
                            custom_fields=custom_fields, custom_values={},
                            cf_record_id=None, custom_attachments={})


# ---------------------------------------------------------------------
# API (small JSON helper for the sale/purchase line-item forms)
# ---------------------------------------------------------------------


@bp.route("/api/product/<int:pid>")
def api_product(pid):
    p = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
    if not p:
        return jsonify({}), 404
    stock = get_product_stock(pid)
    return jsonify(dict(cost_price=p["CostPrice"], selling_price=p["SellingPrice"], stock=stock, unit=p["Unit"]))
