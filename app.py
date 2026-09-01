import os
import re
import uuid
import secrets
import calendar
import mimetypes
from functools import wraps
from datetime import date, datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, session
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

import db
import gst_logic
import gst_reminders
import purchase_import
import bulk_import

app = Flask(__name__)


def indian_number_format(value, decimals=2):
    """Format a number Indian-style: groups of 2 after the first 3 digits from the
    right (e.g. 862000 -> 8,62,000.00), rather than the Western groups-of-3 style
    (862,000.00). Used everywhere a rupee amount or a plain count is shown."""
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0.0
    negative = value < 0
    value = abs(value)
    if decimals > 0:
        whole_str, dec_str = f"{value:.{decimals}f}".split(".")
    else:
        whole_str, dec_str = f"{value:.0f}", None
    if len(whole_str) > 3:
        last_three = whole_str[-3:]
        rest = whole_str[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        whole_str = ",".join(groups) + "," + last_three
    result = whole_str + (f".{dec_str}" if dec_str is not None else "")
    return ("-" if negative else "") + result


@app.template_filter("inr")
def inr_filter(value, decimals=2):
    """₹ + Indian-style grouped number, e.g. 862000 -> ₹8,62,000.00."""
    return "₹" + indian_number_format(value, decimals)


@app.template_filter("inrn")
def inrn_filter(value, decimals=0):
    """Indian-style grouped number with no currency symbol, e.g. for quantities/
    counts: 862000 -> 8,62,000."""
    return indian_number_format(value, decimals)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_KEY_FILE = os.path.join(BASE_DIR, "secret_key.txt")


def _load_or_create_secret_key():
    """Session cookies are signed with this key - it must stay the same
    across restarts (or everyone gets logged out) and must stay private
    (anyone with it could forge a login). Generated once on first run and
    kept in secret_key.txt next to app.py; don't share that file or check
    it into version control."""
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, "r") as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, "w") as f:
        f.write(key)
    return key


app.secret_key = _load_or_create_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB cap per upload request
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

# 2-digit GST state/UT codes as per the GSTIN structure (Indian states + UTs)
INDIAN_STATES = [
    ("01", "Jammu and Kashmir"), ("02", "Himachal Pradesh"), ("03", "Punjab"),
    ("04", "Chandigarh"), ("05", "Uttarakhand"), ("06", "Haryana"), ("07", "Delhi"),
    ("08", "Rajasthan"), ("09", "Uttar Pradesh"), ("10", "Bihar"), ("11", "Sikkim"),
    ("12", "Arunachal Pradesh"), ("13", "Nagaland"), ("14", "Manipur"), ("15", "Mizoram"),
    ("16", "Tripura"), ("17", "Meghalaya"), ("18", "Assam"), ("19", "West Bengal"),
    ("20", "Jharkhand"), ("21", "Odisha"), ("22", "Chhattisgarh"), ("23", "Madhya Pradesh"),
    ("24", "Gujarat"), ("26", "Dadra and Nagar Haveli and Daman and Diu"),
    ("27", "Maharashtra"), ("28", "Andhra Pradesh (Old)"), ("29", "Karnataka"),
    ("30", "Goa"), ("31", "Lakshadweep"), ("32", "Kerala"), ("33", "Tamil Nadu"),
    ("34", "Puducherry"), ("35", "Andaman and Nicobar Islands"), ("36", "Telangana"),
    ("37", "Andhra Pradesh"), ("38", "Ladakh"),
]
STATE_NAME_BY_CODE = dict(INDIAN_STATES)


# =======================================================================
# LOGIN  (individual staff accounts - see Users table in schema.sql)
# =======================================================================

# Endpoints reachable WITHOUT being signed in. Everything else is gated
# by require_login() below. 'static' is Flask's built-in endpoint for
# files under /static (CSS/JS/icons) and must stay public too.
PUBLIC_ENDPOINTS = {"home", "login", "logout", "static"}


def get_current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    user = db.query("SELECT * FROM Users WHERE UserID=? AND Active=1", (uid,), one=True)
    if not user:
        session.clear()
    return user


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user or user["Role"] != "Admin":
            flash("That page is only available to Admin accounts.", "error")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)
    return wrapper


@app.before_request
def require_login():
    if request.endpoint is None:
        return  # unmatched routes fall through to the normal 404 handling
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if not get_current_user():
        return redirect(url_for("login", next=request.path))


@app.context_processor
def inject_current_user():
    return dict(current_user=get_current_user())


@app.route("/")
def home():
    if get_current_user():
        return redirect(url_for("dashboard"))
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = request.form.get("remember") == "on"
        user = db.query("SELECT * FROM Users WHERE Username=? COLLATE NOCASE", (username,), one=True)
        if user and user["Active"] and check_password_hash(user["PasswordHash"], password):
            session.clear()
            session["user_id"] = user["UserID"]
            session.permanent = remember
            db.execute("UPDATE Users SET LastLoginAt=datetime('now') WHERE UserID=?", (user["UserID"],))
            nxt = request.form.get("next") or request.args.get("next")
            flash(f"Welcome back, {user['FullName'] or user['Username']}.", "success")
            return redirect(nxt if nxt and nxt.startswith("/") else url_for("dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You've been signed out.", "success")
    return redirect(url_for("home"))


@app.route("/account/change-password", methods=["GET", "POST"])
def change_password():
    user = get_current_user()
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not check_password_hash(user["PasswordHash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif new != confirm:
            flash("New password and confirmation don't match.", "error")
        else:
            db.execute("UPDATE Users SET PasswordHash=? WHERE UserID=?",
                       (generate_password_hash(new), user["UserID"]))
            flash("Password changed.", "success")
            return redirect(url_for("dashboard"))
    return render_template("change_password.html")


@app.route("/users")
@admin_required
def users_list():
    users = db.query("SELECT * FROM Users ORDER BY Active DESC, Username")
    return render_template("users_list.html", users=users)


@app.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_add():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        role = request.form.get("role") if request.form.get("role") in ("Admin", "Staff") else "Staff"
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not username:
            flash("Username is required.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Password and confirmation don't match.", "error")
        else:
            try:
                db.execute(
                    "INSERT INTO Users (Username, PasswordHash, FullName, Role, Active) VALUES (?,?,?,?,1)",
                    (username, generate_password_hash(password), full_name or username, role),
                )
                flash(f"Login created for {username}.", "success")
                return redirect(url_for("users_list"))
            except Exception:
                flash(f"Could not create login — the username '{username}' may already be taken.", "error")
    return render_template("user_form.html")


@app.route("/users/<int:uid>/toggle", methods=["POST"])
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


@app.route("/users/<int:uid>/reset-password", methods=["GET", "POST"])
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


def get_company_settings():
    return db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)


def get_financial_year_label(d):
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def next_invoice_number():
    """Allocates the next sequential GST invoice number for the current
    financial year, formatted <Prefix>/<FY>/<0001>. Resets to 0001 when a
    new financial year (1 Apr) begins."""
    fy = get_financial_year_label(date.today())
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT NextInvoiceSeq, InvoiceSeqFY, InvoicePrefix FROM CompanySettings WHERE SettingsID=1").fetchone()
        seq = row["NextInvoiceSeq"] if row["InvoiceSeqFY"] == fy else 1
        prefix = row["InvoicePrefix"] or "INV"
        invoice_no = f"{prefix}/{fy}/{seq:04d}"
        conn.execute("UPDATE CompanySettings SET NextInvoiceSeq=?, InvoiceSeqFY=? WHERE SettingsID=1", (seq + 1, fy))
        conn.commit()
    finally:
        conn.close()
    return invoice_no


def state_code_from_gstin(gstin):
    """The first 2 digits of a GSTIN are the supplier's GST state code -
    used to work out CGST+SGST vs IGST on a purchase without needing a
    separate State field on the Suppliers table."""
    gstin = (gstin or "").strip()
    return gstin[:2] if len(gstin) >= 2 and gstin[:2].isdigit() else ""


def compute_line_gst(taxable_value, gst_rate, is_interstate):
    """Splits GST into CGST+SGST (intra-state) or IGST (inter-state)."""
    if is_interstate:
        igst_rate = gst_rate
        igst_amt = round(taxable_value * igst_rate / 100, 2)
        return dict(cgst_rate=0, cgst_amt=0, sgst_rate=0, sgst_amt=0, igst_rate=igst_rate, igst_amt=igst_amt)
    else:
        half = gst_rate / 2
        cgst_amt = round(taxable_value * half / 100, 2)
        sgst_amt = round(taxable_value * half / 100, 2)
        return dict(cgst_rate=half, cgst_amt=cgst_amt, sgst_rate=half, sgst_amt=sgst_amt, igst_rate=0, igst_amt=0)


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digit_words(n):
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")


def _three_digit_words(n):
    if n >= 100:
        return _ONES[n // 100] + " Hundred" + (" " + _two_digit_words(n % 100) if n % 100 else "")
    return _two_digit_words(n)


def amount_in_words(amount):
    """Converts a rupee amount to words using the Indian numbering system
    (Crore/Lakh/Thousand), for the statutory 'Amount in Words' line."""
    num = int(round(amount))
    if num == 0:
        return "Zero Rupees Only"
    parts = []
    crore, num = divmod(num, 10000000)
    lakh, num = divmod(num, 100000)
    thousand, num = divmod(num, 1000)
    hundred = num
    if crore:
        parts.append(_three_digit_words(crore) + " Crore")
    if lakh:
        parts.append(_three_digit_words(lakh) + " Lakh")
    if thousand:
        parts.append(_three_digit_words(thousand) + " Thousand")
    if hundred:
        parts.append(_three_digit_words(hundred))
    return " ".join(parts) + " Rupees Only"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def today_str():
    return date.today().isoformat()


def get_products_with_stock(active_only=True):
    sql = """
        SELECT p.*, COALESCE(t.CurrentStock, 0) AS CurrentStock,
               s.SupplierName AS DefaultSupplierName
        FROM Products p
        LEFT JOIN (
            SELECT ProductID, SUM(QtyChange) AS CurrentStock
            FROM InventoryTransactions GROUP BY ProductID
        ) t ON t.ProductID = p.ProductID
        LEFT JOIN Suppliers s ON s.SupplierID = p.DefaultSupplierID
    """
    if active_only:
        sql += " WHERE p.Active = 1"
    sql += " ORDER BY p.ProductName"
    return db.query(sql)


def get_product_stock(product_id):
    row = db.query(
        "SELECT COALESCE(SUM(QtyChange),0) AS CurrentStock FROM InventoryTransactions WHERE ProductID=?",
        (product_id,), one=True)
    return row["CurrentStock"] if row else 0


def low_stock_products():
    return [p for p in get_products_with_stock() if p["CurrentStock"] <= p["MinStock"]]


def over_stock_products():
    return [p for p in get_products_with_stock()
            if p["MaxStock"] > 0 and p["CurrentStock"] >= p["MaxStock"]]


def upcoming_maintenance(days=7):
    sql = """
        SELECT vm.*, v.RegistrationNumber, v.VehicleType
        FROM VehicleMaintenance vm
        JOIN Vehicles v ON v.VehicleID = vm.VehicleID
        WHERE vm.MaintenanceID IN (
            SELECT vm2.MaintenanceID FROM VehicleMaintenance vm2
            WHERE vm2.VehicleID = vm.VehicleID AND vm2.ServiceType = vm.ServiceType
            ORDER BY vm2.ServiceDate DESC LIMIT 1
        )
        AND vm.NextDueDate IS NOT NULL
        AND date(vm.NextDueDate) <= date('now', ?)
        ORDER BY vm.NextDueDate
    """
    return db.query(sql, (f"+{days} days",))


def expiring_documents(days=30):
    sql = """
        SELECT VehicleID, RegistrationNumber, 'Insurance' AS DocType, InsuranceExpiry AS ExpiryDate FROM Vehicles
        WHERE InsuranceExpiry IS NOT NULL AND date(InsuranceExpiry) <= date('now', ?)
        UNION ALL
        SELECT VehicleID, RegistrationNumber, 'Permit', PermitExpiry FROM Vehicles
        WHERE PermitExpiry IS NOT NULL AND date(PermitExpiry) <= date('now', ?)
        UNION ALL
        SELECT VehicleID, RegistrationNumber, 'PUC', PUCExpiry FROM Vehicles
        WHERE PUCExpiry IS NOT NULL AND date(PUCExpiry) <= date('now', ?)
        UNION ALL
        SELECT VehicleID, RegistrationNumber, 'Fitness', FitnessExpiry FROM Vehicles
        WHERE FitnessExpiry IS NOT NULL AND date(FitnessExpiry) <= date('now', ?)
        ORDER BY ExpiryDate
    """
    d = f"+{days} days"
    return db.query(sql, (d, d, d, d))


def pending_salary_this_month():
    today = date.today()
    return db.query("""
        SELECT sp.*, e.EmployeeName FROM SalaryPayments sp
        JOIN Employees e ON e.EmployeeID = sp.EmployeeID
        WHERE sp.SalaryYear=? AND sp.SalaryMonth=? AND sp.Status='Pending'
        ORDER BY e.EmployeeName
    """, (today.year, today.month))


def employees_missing_salary_record():
    today = date.today()
    return db.query("""
        SELECT e.* FROM Employees e
        WHERE e.Status='Active' AND e.EmployeeID NOT IN (
            SELECT EmployeeID FROM SalaryPayments WHERE SalaryYear=? AND SalaryMonth=?
        )
    """, (today.year, today.month))


def active_advances():
    return db.query("""
        SELECT a.*, e.EmployeeName FROM AdvancePayments a
        JOIN Employees e ON e.EmployeeID = a.EmployeeID
        WHERE a.Status='Active' ORDER BY a.AdvanceDate DESC
    """)


@app.context_processor
def inject_current_date():
    return dict(current_date=date.today().strftime("%d %b %Y"))


@app.context_processor
def inject_alert_counts():
    try:
        low = len(low_stock_products())
        over = len(over_stock_products())
        maint = len(upcoming_maintenance())
        docs = len(expiring_documents())
        sal = len(pending_salary_this_month()) + len(employees_missing_salary_record())
        gst = len(gst_logic.upcoming_due_dates(get_company_settings(), days_ahead=7))
        return dict(alert_counts=dict(low=low, over=over, maint=maint, docs=docs, sal=sal, gst=gst,
                                        total=low + over + maint + docs + sal + gst))
    except Exception:
        return dict(alert_counts=dict(low=0, over=0, maint=0, docs=0, sal=0, gst=0, total=0))


# =======================================================================
# ATTACHMENTS  (generic — used by Employees, Advances, Vehicles,
# Purchases, Expenses)
# =======================================================================

ATTACHMENT_MODULES = ["Employee", "Advance", "Vehicle", "Purchase", "Expense"]


def custom_field_attachment_module(field_id):
    """The synthetic Attachments.ModuleName used for an attachment-type custom field."""
    return f"CustomField:{field_id}"


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


def get_attachments(module, record_id):
    return db.query("""SELECT * FROM Attachments WHERE ModuleName=? AND RecordID=?
                     ORDER BY UploadedAt DESC, AttachmentID DESC""", (module, record_id))


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


@app.route("/attachments/<module>/<int:record_id>/upload", methods=["POST"])
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


@app.route("/attachments/<int:attachment_id>/download")
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


@app.route("/attachments/<int:attachment_id>/delete", methods=["POST"])
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


def get_custom_field_defs(module):
    return db.query("""SELECT * FROM CustomFieldDefinitions WHERE ModuleName=? AND Active=1
                     ORDER BY DisplayOrder, FieldID""", (module,))


def get_custom_values(module, record_id):
    rows = db.query("""SELECT d.FieldKey, v.ValueText FROM CustomFieldValues v
                     JOIN CustomFieldDefinitions d ON d.FieldID = v.FieldID
                     WHERE d.ModuleName=? AND v.RecordID=?""", (module, record_id))
    return {r["FieldKey"]: r["ValueText"] for r in rows}


def get_custom_values_bulk(module, record_ids):
    """dict record_id -> {field_key: value}, for showing custom fields in list views."""
    record_ids = list(record_ids)
    if not record_ids:
        return {}
    placeholders = ",".join("?" * len(record_ids))
    rows = db.query(f"""SELECT v.RecordID, d.FieldKey, v.ValueText FROM CustomFieldValues v
                      JOIN CustomFieldDefinitions d ON d.FieldID = v.FieldID
                      WHERE d.ModuleName=? AND d.Active=1 AND v.RecordID IN ({placeholders})""",
                     (module, *record_ids))
    out = {}
    for r in rows:
        out.setdefault(r["RecordID"], {})[r["FieldKey"]] = r["ValueText"]
    return out


def save_custom_fields(module, record_id, form):
    for d in get_custom_field_defs(module):
        if d["FieldType"] == "attachment":
            continue  # files for these are uploaded/stored separately via the Attachments table
        field_name = f"custom_{d['FieldKey']}"
        if d["FieldType"] == "checkbox":
            value = "1" if form.get(field_name) else "0"
        else:
            value = form.get(field_name, "")
        existing = db.query("SELECT ValueID FROM CustomFieldValues WHERE FieldID=? AND RecordID=?",
                            (d["FieldID"], record_id), one=True)
        if existing:
            db.execute("UPDATE CustomFieldValues SET ValueText=? WHERE ValueID=?", (value, existing["ValueID"]))
        else:
            db.execute("INSERT INTO CustomFieldValues (FieldID, RecordID, ValueText) VALUES (?,?,?)",
                       (d["FieldID"], record_id, value))


def get_custom_attachments(module, record_id):
    """dict FieldID -> list of Attachments rows, for this module's active
    attachment-type custom fields on one record. Empty dict for a record
    that doesn't exist yet (nothing to attach to until it's saved)."""
    if not record_id:
        return {}
    out = {}
    for d in get_custom_field_defs(module):
        if d["FieldType"] == "attachment":
            out[d["FieldID"]] = get_attachments(custom_field_attachment_module(d["FieldID"]), record_id)
    return out


@app.route("/settings/custom-fields")
@admin_required
def custom_fields_admin():
    module = request.args.get("module", CUSTOM_FIELD_MODULES[0][0])
    fields = db.query("SELECT * FROM CustomFieldDefinitions WHERE ModuleName=? ORDER BY DisplayOrder, FieldID", (module,))
    return render_template("custom_fields_admin.html", modules=CUSTOM_FIELD_MODULES, module=module,
                            module_label=CUSTOM_FIELD_MODULE_LABELS.get(module, module),
                            fields=fields, field_types=CUSTOM_FIELD_TYPES)


@app.route("/settings/custom-fields/add", methods=["POST"])
@admin_required
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


@app.route("/settings/custom-fields/<int:field_id>/toggle", methods=["POST"])
@admin_required
def custom_field_toggle(field_id):
    d = db.query("SELECT * FROM CustomFieldDefinitions WHERE FieldID=?", (field_id,), one=True)
    module = request.form.get("module", d["ModuleName"] if d else "")
    if d:
        db.execute("UPDATE CustomFieldDefinitions SET Active=? WHERE FieldID=?", (0 if d["Active"] else 1, field_id))
    return redirect(url_for("custom_fields_admin", module=module))


@app.route("/settings/custom-fields/<int:field_id>/delete", methods=["POST"])
@admin_required
def custom_field_delete(field_id):
    d = db.query("SELECT * FROM CustomFieldDefinitions WHERE FieldID=?", (field_id,), one=True)
    module = request.form.get("module", d["ModuleName"] if d else "")
    if d and d["FieldType"] == "attachment":
        delete_all_attachments_for_module(custom_field_attachment_module(field_id))
    db.execute("DELETE FROM CustomFieldDefinitions WHERE FieldID=?", (field_id,))
    flash("Custom field deleted (and any values/files stored in it).", "success")
    return redirect(url_for("custom_fields_admin", module=module))


# =======================================================================
# DASHBOARD WIDGET CUSTOMIZATION
# =======================================================================

DASHBOARD_WIDGET_LABELS = {
    "stats": "Summary stat cards (sales, purchases, expenses, receivable/payable)",
    "targets": "Sales Target progress (MTD)",
    "low_stock": "Low stock alert",
    "over_stock": "Overstock alert",
    "maintenance_due": "Vehicle maintenance due",
    "docs_expiring": "Vehicle documents expiring",
    "salary_status": "Salary status this month",
    "active_advances": "Active employee advances",
    "gst_due": "GST filing due dates",
}
DASHBOARD_WIDGET_ORDER_DEFAULT = list(DASHBOARD_WIDGET_LABELS.keys())


def get_dashboard_widgets():
    rows = db.query("SELECT * FROM DashboardWidgets ORDER BY DisplayOrder, WidgetKey")
    seen = {r["WidgetKey"] for r in rows}
    widgets = [dict(r) for r in rows]
    for i, k in enumerate(DASHBOARD_WIDGET_ORDER_DEFAULT):
        if k not in seen:
            widgets.append(dict(WidgetKey=k, Visible=1, DisplayOrder=1000 + i))
    widgets.sort(key=lambda w: w["DisplayOrder"])
    return widgets


@app.route("/dashboard/customize", methods=["GET", "POST"])
@admin_required
def dashboard_customize():
    all_keys = list(DASHBOARD_WIDGET_LABELS.keys())
    if request.method == "POST":
        for key in all_keys:
            visible = 1 if request.form.get(f"visible_{key}") else 0
            try:
                order = int(request.form.get(f"order_{key}", 0))
            except ValueError:
                order = 0
            db.execute("""INSERT INTO DashboardWidgets (WidgetKey, Visible, DisplayOrder) VALUES (?,?,?)
                        ON CONFLICT(WidgetKey) DO UPDATE SET Visible=excluded.Visible, DisplayOrder=excluded.DisplayOrder""",
                       (key, visible, order))
        flash("Dashboard layout saved.", "success")
        return redirect(url_for("dashboard"))
    widgets = get_dashboard_widgets()
    return render_template("dashboard_customize.html", widgets=widgets, labels=DASHBOARD_WIDGET_LABELS)


# =======================================================================
# LIST VIEW CUSTOMIZABLE COLUMNS
# =======================================================================

# Base (built-in) columns available per module — key must match the
# data-col attribute used in that module's list template.
MODULE_COLUMNS = {
    "Product": [("product_name", "Product"), ("sku", "SKU"), ("category", "Category"),
                ("stock", "Stock"), ("min_stock", "Min"), ("max_stock", "Max"),
                ("cost_price", "Cost"), ("selling_price", "Selling"), ("scheme", "Scheme"),
                ("incentive", "Incentive"), ("supplier", "Supplier"), ("status", "Status")],
    "Supplier": [("name", "Name"), ("contact", "Contact"), ("phone", "Phone"),
                 ("email", "Email"), ("gstin", "GSTIN"), ("status", "Status")],
    "Customer": [("name", "Name"), ("contact", "Contact"), ("phone", "Phone"),
                 ("credit_limit", "Credit Limit"), ("credit_days", "Credit Days"), ("status", "Status")],
    "Purchase": [("po_number", "PO #"), ("supplier", "Supplier"), ("date", "Date"),
                 ("invoice_number", "Invoice #"), ("status", "Status"), ("payment", "Payment"),
                 ("total", "Total")],
    "Sale": [("invoice_number", "Invoice #"), ("customer", "Customer"), ("date", "Date"),
             ("status", "Status"), ("payment", "Payment"), ("due_date", "Due Date"), ("total", "Total")],
    "Expense": [("date", "Date"), ("category", "Category"), ("vehicle", "Vehicle"),
                ("paid_to", "Paid To"), ("mode", "Mode"), ("amount", "Amount")],
    "Vehicle": [("reg_number", "Reg. No"), ("type", "Type"), ("make_model", "Make/Model"),
                ("odometer", "Odometer"), ("insurance", "Insurance"), ("permit", "Permit"),
                ("puc", "PUC"), ("fitness", "Fitness"), ("status", "Status")],
    "Maintenance": [("vehicle", "Vehicle"), ("service_type", "Service Type"), ("date", "Date"),
                     ("odometer", "Odometer"), ("next_due_date", "Next Due Date"), ("cost", "Cost"),
                     ("service_center", "Service Center")],
    "Employee": [("name", "Name"), ("designation", "Designation"), ("phone", "Phone"),
                 ("join_date", "Join Date"), ("monthly_salary", "Monthly Salary"), ("status", "Status")],
    "SalaryPayment": [("employee", "Employee"), ("designation", "Designation"), ("basic", "Basic"),
                       ("advance_deducted", "Advance Deducted"), ("net_payable", "Net Payable"),
                       ("status", "Status")],
    "Advance": [("employee", "Employee"), ("date", "Date"), ("amount", "Amount"), ("reason", "Reason"),
                ("repay_months", "Repay (months)"), ("monthly_deduction", "Monthly Deduction"),
                ("balance", "Balance"), ("status", "Status")],
}


def get_effective_columns(module):
    """Returns the ordered list of column dicts for a module's list view,
    with saved visibility/order applied. Includes hidden columns too
    (callers filter by 'visible' as needed) so the customize page can
    show everything. (Custom fields have their own place on the record's
    form/detail view — this only covers the built-in list columns.)"""
    all_cols = [dict(key=k, label=lbl, kind="base") for k, lbl in MODULE_COLUMNS.get(module, [])]

    prefs = {r["ColumnKey"]: r for r in db.query(
        "SELECT * FROM ListViewColumns WHERE ModuleName=?", (module,))}

    for i, col in enumerate(all_cols):
        pref = prefs.get(col["key"])
        if pref:
            col["visible"] = bool(pref["Visible"])
            col["order"] = pref["DisplayOrder"]
        else:
            col["visible"] = True
            col["order"] = i

    all_cols.sort(key=lambda c: c["order"])
    return all_cols


@app.route("/columns/<module>/customize", methods=["GET", "POST"])
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
                            module_label=CUSTOM_FIELD_MODULE_LABELS.get(module, module))


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    today = date.today()
    month_start = today.replace(day=1).isoformat()

    sales_today = db.query("SELECT COALESCE(SUM(TotalAmount),0) AS t FROM Sales WHERE SaleDate=? AND Status<>'Cancelled'",
                            (today.isoformat(),), one=True)["t"]
    sales_month = db.query("SELECT COALESCE(SUM(TotalAmount),0) AS t FROM Sales WHERE SaleDate>=? AND Status<>'Cancelled'",
                            (month_start,), one=True)["t"]
    purchases_month = db.query("SELECT COALESCE(SUM(TotalAmount),0) AS t FROM Purchases WHERE PurchaseDate>=? AND Status<>'Cancelled'",
                                (month_start,), one=True)["t"]
    expenses_month = db.query("SELECT COALESCE(SUM(Amount),0) AS t FROM Expenses WHERE ExpenseDate>=?",
                               (month_start,), one=True)["t"]
    receivable = db.query("SELECT COALESCE(SUM(TotalAmount - AmountReceived),0) AS t FROM Sales WHERE PaymentStatus<>'Paid' AND Status<>'Cancelled'",
                           one=True)["t"]
    payable = db.query("SELECT COALESCE(SUM(TotalAmount - AmountPaid),0) AS t FROM Purchases WHERE PaymentStatus<>'Paid' AND Status<>'Cancelled'",
                        one=True)["t"]
    stock_issue_due = db.query("SELECT COALESCE(SUM(AmountDue),0) AS t FROM StockIssues WHERE AmountDue > 0",
                                one=True)["t"]
    scheme_claim_pending = db.query(
        "SELECT COALESCE(SUM(SchemeAmount),0) AS t FROM StockIssues WHERE SchemeAmount > 0 AND ClaimStatus<>'Received'",
        one=True)["t"]
    unassigned_row = db.query("""SELECT COUNT(*) AS n, COALESCE(SUM(s.TotalAmount),0) AS t FROM Sales s
                               JOIN Customers c ON c.CustomerID=s.CustomerID
                               WHERE c.IsUnassignedBucket=1 AND s.Status<>'Cancelled'""", one=True)
    unassigned_sales_count = unassigned_row["n"]
    unassigned_sales_amount = unassigned_row["t"]

    widgets = get_dashboard_widgets()
    widget_visible = {w["WidgetKey"]: bool(w["Visible"]) for w in widgets}
    widget_order = [w["WidgetKey"] for w in widgets]

    gst_due = gst_logic.upcoming_due_dates(get_company_settings(), days_ahead=14)

    # Sales Target MTD progress - whole-employee (all-products) targets for
    # the current month, combined across every salesperson that has one set.
    target_summary = get_company_target_summary(today.year, today.month)

    return render_template("dashboard.html",
                            sales_today=sales_today, sales_month=sales_month,
                            purchases_month=purchases_month, expenses_month=expenses_month,
                            receivable=receivable, payable=payable,
                            stock_issue_due=stock_issue_due, scheme_claim_pending=scheme_claim_pending,
                            unassigned_sales_count=unassigned_sales_count, unassigned_sales_amount=unassigned_sales_amount,
                            low_stock=low_stock_products(), over_stock=over_stock_products(),
                            maintenance=upcoming_maintenance(), docs=expiring_documents(),
                            pending_salary=pending_salary_this_month(),
                            missing_salary=employees_missing_salary_record(),
                            advances=active_advances(), gst_due=gst_due, target_summary=target_summary,
                            month_name=MONTH_NAMES[today.month], year=today.year,
                            widget_visible=widget_visible, widget_order=widget_order)


# ---------------------------------------------------------------------
# Inventory / Products
# ---------------------------------------------------------------------

@app.route("/inventory")
def inventory_list():
    q = request.args.get("q", "").strip()
    filt = request.args.get("filter", "")
    products = get_products_with_stock()
    if q:
        ql = q.lower()
        products = [p for p in products if ql in p["ProductName"].lower() or (p["SKU"] or "").lower().find(ql) >= 0]
    if filt == "low":
        products = [p for p in products if p["CurrentStock"] <= p["MinStock"]]
    elif filt == "over":
        products = [p for p in products if p["MaxStock"] > 0 and p["CurrentStock"] >= p["MaxStock"]]
    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    columns = get_effective_columns("Product")
    return render_template("inventory_list.html", products=products, q=q, filt=filt, suppliers=suppliers, columns=columns)


@app.route("/inventory/new", methods=["GET", "POST"])
@app.route("/inventory/<int:pid>/edit", methods=["GET", "POST"])
def inventory_form(pid=None):
    product = None
    if pid:
        product = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
    if request.method == "POST":
        f = request.form
        args = (f["sku"], f["product_name"], f["category"], f["unit"],
                float(f["cost_price"] or 0), float(f["selling_price"] or 0),
                float(f["min_stock"] or 0), float(f["max_stock"] or 0),
                float(f["reorder_qty"] or 0),
                int(f["default_supplier_id"]) if f.get("default_supplier_id") else None,
                f.get("hsn_code", ""), float(f.get("gst_rate") or 0),
                1 if f.get("active") else 0,
                f.get("scheme_name", ""), float(f.get("scheme_percent") or 0),
                float(f.get("incentive_per_unit") or 0))
        try:
            if pid:
                db.execute("""UPDATE Products SET SKU=?, ProductName=?, Category=?, Unit=?, CostPrice=?,
                            SellingPrice=?, MinStock=?, MaxStock=?, ReorderQty=?, DefaultSupplierID=?,
                            HSNCode=?, GSTRate=?, Active=?, SchemeName=?, SchemePercent=?, IncentivePerUnit=?
                            WHERE ProductID=?""", args + (pid,))
                save_custom_fields("Product", pid, f)
                flash(f"Product '{f['product_name']}' updated.", "success")
            else:
                new_id = db.execute("""INSERT INTO Products (SKU, ProductName, Category, Unit, CostPrice,
                            SellingPrice, MinStock, MaxStock, ReorderQty, DefaultSupplierID, HSNCode, GSTRate, Active,
                            SchemeName, SchemePercent, IncentivePerUnit)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", args)
                opening = float(f.get("opening_stock") or 0)
                if opening:
                    db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                                QtyChange, RefType, Notes) VALUES (?,?,?,?,?,?)""",
                               (new_id, today_str(), "Opening Stock", opening, "Manual", "Opening balance"))
                save_custom_fields("Product", new_id, f)
                flash(f"Product '{f['product_name']}' added.", "success")
        except Exception:
            flash(f"Could not save — SKU '{f['sku']}' is already used by another product. "
                  f"Pick a different SKU and try again.", "error")
            return redirect(url_for("inventory_form", pid=pid) if pid else url_for("inventory_form"))
        return redirect(url_for("inventory_list"))
    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    custom_fields = get_custom_field_defs("Product")
    custom_values = get_custom_values("Product", pid) if pid else {}
    return render_template("inventory_form.html", product=product, suppliers=suppliers,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=pid, custom_attachments=get_custom_attachments("Product", pid))


@app.route("/api/product/<int:pid>/gst")
def api_product_gst(pid):
    p = db.query("SELECT ProductID, ProductName, HSNCode, GSTRate, SellingPrice FROM Products WHERE ProductID=?",
                 (pid,), one=True)
    if not p:
        return jsonify({}), 404
    return jsonify(dict(hsn_code=p["HSNCode"] or "", gst_rate=p["GSTRate"] or 0, selling_price=p["SellingPrice"]))


@app.route("/inventory/<int:pid>/adjust", methods=["GET", "POST"])
def inventory_adjust(pid):
    product = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
    if request.method == "POST":
        f = request.form
        direction = f["direction"]
        qty = abs(float(f["qty"]))
        change = qty if direction == "in" else -qty
        db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                    QtyChange, RefType, Notes) VALUES (?,?,?,?,?,?)""",
                   (pid, f["adjust_date"] or today_str(), f"Adjustment-{'In' if direction=='in' else 'Out'}",
                    change, "Manual", f.get("notes", "")))
        flash("Stock adjustment recorded.", "success")
        return redirect(url_for("inventory_list"))
    history = db.query("""SELECT * FROM InventoryTransactions WHERE ProductID=?
                        ORDER BY TransactionDate DESC, TransactionID DESC LIMIT 30""", (pid,))
    return render_template("stock_adjust.html", product=product, history=history, today=today_str())


# ---------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------

@app.route("/suppliers")
@admin_required
def suppliers_list():
    return render_template("suppliers_list.html", suppliers=db.query("SELECT * FROM Suppliers ORDER BY SupplierName"),
                            columns=get_effective_columns("Supplier"))


@app.route("/suppliers/new", methods=["GET", "POST"])
@app.route("/suppliers/<int:sid>/edit", methods=["GET", "POST"])
@admin_required
def supplier_form(sid=None):
    supplier = db.query("SELECT * FROM Suppliers WHERE SupplierID=?", (sid,), one=True) if sid else None
    if request.method == "POST":
        f = request.form
        args = (f["supplier_name"], f["contact_person"], f["phone"], f["email"], f["address"], f["gstin"],
                1 if f.get("active") else 0)
        if sid:
            db.execute("""UPDATE Suppliers SET SupplierName=?, ContactPerson=?, Phone=?, Email=?, Address=?,
                        GSTIN=?, Active=? WHERE SupplierID=?""", args + (sid,))
            save_custom_fields("Supplier", sid, f)
        else:
            new_id = db.execute("""INSERT INTO Suppliers (SupplierName, ContactPerson, Phone, Email, Address, GSTIN, Active)
                        VALUES (?,?,?,?,?,?,?)""", args)
            save_custom_fields("Supplier", new_id, f)
        flash("Supplier saved.", "success")
        return redirect(url_for("suppliers_list"))
    custom_fields = get_custom_field_defs("Supplier")
    custom_values = get_custom_values("Supplier", sid) if sid else {}
    return render_template("supplier_form.html", supplier=supplier,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=sid, custom_attachments=get_custom_attachments("Supplier", sid))


# ---------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------

@app.route("/customers")
def customers_list():
    return render_template("customers_list.html", customers=db.query("SELECT * FROM Customers ORDER BY CustomerName"),
                            columns=get_effective_columns("Customer"))


@app.route("/customers/new", methods=["GET", "POST"])
@app.route("/customers/<int:cid>/edit", methods=["GET", "POST"])
def customer_form(cid=None):
    customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (cid,), one=True) if cid else None
    if request.method == "POST":
        f = request.form
        state_code = f.get("state_code", "")
        state_name = STATE_NAME_BY_CODE.get(state_code, "")
        args = (f["customer_name"], f["contact_person"], f["phone"], f["email"], f["address"], f["gstin"],
                state_name, state_code,
                float(f["credit_limit"] or 0), int(f["credit_days"] or 0), 1 if f.get("active") else 0)
        if cid:
            db.execute("""UPDATE Customers SET CustomerName=?, ContactPerson=?, Phone=?, Email=?, Address=?,
                        GSTIN=?, State=?, StateCode=?, CreditLimit=?, CreditDays=?, Active=? WHERE CustomerID=?""",
                       args + (cid,))
            save_custom_fields("Customer", cid, f)
        else:
            new_id = db.execute("""INSERT INTO Customers (CustomerName, ContactPerson, Phone, Email, Address, GSTIN,
                        State, StateCode, CreditLimit, CreditDays, Active) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", args)
            save_custom_fields("Customer", new_id, f)
        flash("Customer saved.", "success")
        return redirect(url_for("customers_list"))
    custom_fields = get_custom_field_defs("Customer")
    custom_values = get_custom_values("Customer", cid) if cid else {}
    return render_template("customer_form.html", customer=customer, states=INDIAN_STATES,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=cid, custom_attachments=get_custom_attachments("Customer", cid))


# ---------------------------------------------------------------------
# Customers: bulk Excel upload
#
# Off an app-provided template (download it, fill it in, upload it) -
# not a specific vendor's own format. A row whose Customer Name exactly
# matches (case-insensitive) an existing customer UPDATES that customer's
# details from the file; anything else is added as a new customer. This
# is a one-step upload (no separate matching/review screen) since there's
# nothing here that needs a human pick, unlike the Purchases vendor-file
# import which has to match unfamiliar material codes to Products.
# ---------------------------------------------------------------------

@app.route("/customers/import/template")
def customers_import_template():
    from flask import send_file
    buf = bulk_import.build_customer_template()
    return send_file(buf, as_attachment=True, download_name="Customer_Upload_Template.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/customers/import", methods=["GET", "POST"])
def customers_import_upload():
    if request.method == "POST":
        file_storage = request.files.get("file")
        if not file_storage or not file_storage.filename:
            flash("Choose a file to upload.", "error")
            return redirect(url_for("customers_import_upload"))

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            file_storage.save(tmp.name)
            tmp_path = tmp.name
        try:
            result = bulk_import.parse_customers_excel(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if not result["ok"]:
            flash(result["message"], "error")
            return redirect(url_for("customers_import_upload"))

        created = updated = 0
        for row in result["rows"]:
            state_code = row["state_code"]
            state_name = row["state"]
            if state_code and not state_name:
                state_name = STATE_NAME_BY_CODE.get(state_code, "")
            elif state_name and not state_code:
                for code, name in INDIAN_STATES:
                    if name.lower() == state_name.lower():
                        state_code = code
                        break

            existing = db.query("SELECT * FROM Customers WHERE CustomerName=? COLLATE NOCASE",
                                (row["customer_name"],), one=True)
            args = (row["customer_name"], row["contact_person"], row["phone"], row["email"], row["address"],
                    row["gstin"], state_name, state_code, row["credit_limit"], row["credit_days"])
            if existing:
                db.execute("""UPDATE Customers SET CustomerName=?, ContactPerson=?, Phone=?, Email=?, Address=?,
                            GSTIN=?, State=?, StateCode=?, CreditLimit=?, CreditDays=? WHERE CustomerID=?""",
                           args + (existing["CustomerID"],))
                updated += 1
            else:
                db.execute("""INSERT INTO Customers (CustomerName, ContactPerson, Phone, Email, Address, GSTIN,
                            State, StateCode, CreditLimit, CreditDays, Active) VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                           args)
                created += 1

        flash(f"{result['message']} {created} new customer(s) added, {updated} existing customer(s) updated.",
              "success")
        return redirect(url_for("customers_list"))

    return render_template("customers_import_upload.html")


@app.route("/api/customer/<int:cid>")
def api_customer(cid):
    c = db.query("SELECT CustomerID, CustomerName, GSTIN, State, StateCode, Address FROM Customers WHERE CustomerID=?",
                 (cid,), one=True)
    if not c:
        return jsonify({}), 404
    return jsonify(dict(c))


# ---------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------

@app.route("/purchases")
@admin_required
def purchases_list():
    purchases = db.query("""SELECT p.*, s.SupplierName FROM Purchases p
                          JOIN Suppliers s ON s.SupplierID = p.SupplierID ORDER BY p.PurchaseDate DESC, p.PurchaseID DESC""")
    return render_template("purchases_list.html", purchases=purchases, columns=get_effective_columns("Purchase"))


def create_purchase(supplier_id, po_number, purchase_date, invoice_number, status, payment_status,
                     amount_paid, notes, lines, interstate_override=False, reverse_charge=False,
                     itc_eligible=True, purchase_id=None):
    """Creates a Purchase + PurchaseLines with the GST breakup computed the
    same way for every entry point (the manual purchase form, the
    vendor-file import, and the Admin-only edit screen all call this).
    `lines` is a list of (product_id, qty, unit_cost) tuples. Returns the
    PurchaseID (new, or the same one passed in). Inter-state is derived
    from the supplier's GSTIN when there is one (trusted over
    `interstate_override`); `interstate_override` is only used as a
    fallback for a supplier with no GSTIN on file.

    Pass `purchase_id` to EDIT an existing purchase in place instead of
    creating a new one: its old PurchaseLines and the InventoryTransaction
    rows that purchase originally created are removed first, then
    recreated from the new `lines`/`status` — so a corrected quantity or
    a Received->Draft status change is reflected correctly in stock,
    exactly as if the purchase had been entered this way to begin with."""
    company = get_company_settings()
    supplier = db.query("SELECT * FROM Suppliers WHERE SupplierID=?", (supplier_id,), one=True)
    supplier_gstin = (supplier["GSTIN"] or "") if supplier else ""
    supplier_state_code = state_code_from_gstin(supplier_gstin)
    if supplier_state_code:
        is_interstate = 1 if (company["StateCode"] and supplier_state_code != company["StateCode"]) else 0
    else:
        is_interstate = 1 if interstate_override else 0

    taxable_total = cgst_total = sgst_total = igst_total = 0.0
    line_data = []
    for prod_id, qty, cost in lines:
        prod = db.query("SELECT HSNCode, GSTRate FROM Products WHERE ProductID=?", (prod_id,), one=True)
        hsn = (prod["HSNCode"] or "") if prod else ""
        gst_rate = (prod["GSTRate"] or 0) if prod else 0
        taxable_value = round(qty * cost, 2)
        gst = compute_line_gst(taxable_value, gst_rate, is_interstate)
        taxable_total += taxable_value
        cgst_total += gst["cgst_amt"]
        sgst_total += gst["sgst_amt"]
        igst_total += gst["igst_amt"]
        line_data.append((prod_id, qty, cost, taxable_value, hsn, gst_rate, gst))

    raw_total = taxable_total + cgst_total + sgst_total + igst_total
    grand_total = round(raw_total)
    round_off = round(grand_total - raw_total, 2)

    if purchase_id is None:
        purchase_id = db.execute("""INSERT INTO Purchases (PONumber, SupplierID, PurchaseDate, InvoiceNumber,
                    Status, PaymentStatus, TotalAmount, AmountPaid, Notes, SupplierGSTIN, SupplierStateCode,
                    IsInterState, TaxableAmount, CGSTAmount, SGSTAmount, IGSTAmount, RoundOff, ReverseCharge,
                    ITCEligible)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (po_number, supplier_id, purchase_date, invoice_number,
                     status, payment_status, grand_total, amount_paid, notes,
                     supplier_gstin, supplier_state_code, is_interstate,
                     round(taxable_total, 2), round(cgst_total, 2), round(sgst_total, 2), round(igst_total, 2),
                     round_off, 1 if reverse_charge else 0, 1 if itc_eligible else 0))
    else:
        db.execute("""UPDATE Purchases SET PONumber=?, SupplierID=?, PurchaseDate=?, InvoiceNumber=?, Status=?,
                    PaymentStatus=?, TotalAmount=?, AmountPaid=?, Notes=?, SupplierGSTIN=?, SupplierStateCode=?,
                    IsInterState=?, TaxableAmount=?, CGSTAmount=?, SGSTAmount=?, IGSTAmount=?, RoundOff=?,
                    ReverseCharge=?, ITCEligible=? WHERE PurchaseID=?""",
                   (po_number, supplier_id, purchase_date, invoice_number,
                    status, payment_status, grand_total, amount_paid, notes,
                    supplier_gstin, supplier_state_code, is_interstate,
                    round(taxable_total, 2), round(cgst_total, 2), round(sgst_total, 2), round(igst_total, 2),
                    round_off, 1 if reverse_charge else 0, 1 if itc_eligible else 0, purchase_id))
        db.execute("DELETE FROM PurchaseLines WHERE PurchaseID=?", (purchase_id,))
        db.execute("DELETE FROM InventoryTransactions WHERE RefType='Purchase' AND RefID=?", (purchase_id,))

    for prod_id, qty, cost, taxable_value, hsn, gst_rate, gst in line_data:
        db.execute("""INSERT INTO PurchaseLines (PurchaseID, ProductID, Qty, UnitCost, LineTotal, HSNCode,
                    GSTRate, TaxableValue, CGSTRate, CGSTAmount, SGSTRate, SGSTAmount, IGSTRate, IGSTAmount)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (purchase_id, prod_id, qty, cost, taxable_value, hsn, gst_rate, taxable_value,
                    gst["cgst_rate"], gst["cgst_amt"], gst["sgst_rate"], gst["sgst_amt"],
                    gst["igst_rate"], gst["igst_amt"]))
        if status == "Received":
            db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                        QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                       (prod_id, purchase_date, "Purchase", qty, "Purchase", purchase_id, po_number))
    return purchase_id


@app.route("/purchases/new", methods=["GET", "POST"])
@admin_required
def purchase_form():
    company = get_company_settings()
    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        costs = request.form.getlist("unit_cost[]")
        lines = [(int(p), float(q), float(c)) for p, q, c in zip(product_ids, qtys, costs) if p and q]

        po_number = f["po_number"] or f"PO-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        purchase_id = create_purchase(
            supplier_id=int(f["supplier_id"]), po_number=po_number, purchase_date=f["purchase_date"],
            invoice_number=f["invoice_number"], status=f["status"], payment_status=f["payment_status"],
            amount_paid=float(f["amount_paid"] or 0), notes=f.get("notes", ""), lines=lines,
            interstate_override=bool(f.get("interstate_purchase")),
            reverse_charge=bool(f.get("reverse_charge")), itc_eligible=bool(f.get("itc_eligible")))
        save_custom_fields("Purchase", purchase_id, f)
        flash(f"Purchase {po_number} recorded.", "success")
        return redirect(url_for("purchase_view", pid=purchase_id))
    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    products = db.query("SELECT * FROM Products WHERE Active=1 ORDER BY ProductName")
    custom_fields = get_custom_field_defs("Purchase")
    return render_template("purchase_form.html", suppliers=suppliers, products=products, today=today_str(),
                            company=company, custom_fields=custom_fields, custom_values={},
                            cf_record_id=None, custom_attachments={}, purchase=None, existing_lines=None)


@app.route("/purchases/<int:pid>")
@admin_required
def purchase_view(pid):
    purchase = db.query("""SELECT p.*, s.SupplierName, s.Phone, s.Address FROM Purchases p
                         JOIN Suppliers s ON s.SupplierID=p.SupplierID WHERE p.PurchaseID=?""", (pid,), one=True)
    lines = db.query("""SELECT pl.*, pr.ProductName, pr.Unit FROM PurchaseLines pl
                      JOIN Products pr ON pr.ProductID=pl.ProductID WHERE pl.PurchaseID=?""", (pid,))
    custom_fields = get_custom_field_defs("Purchase")
    custom_values = get_custom_values("Purchase", pid)
    attachments = get_attachments("Purchase", pid)
    return render_template("purchase_view.html", purchase=purchase, lines=lines,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=pid, custom_attachments=get_custom_attachments("Purchase", pid),
                            att_module="Purchase", att_record_id=pid, attachments=attachments)


@app.route("/purchases/<int:pid>/edit", methods=["GET", "POST"])
@admin_required
def purchase_edit(pid):
    """Admin-only: correct a mistake on an already-saved purchase (wrong
    quantity/price/product/date/etc.) without deleting and re-entering it.
    Recomputes GST and replaces the InventoryTransaction rows this purchase
    originally created, the same way create_purchase() does for a new one."""
    company = get_company_settings()
    existing = db.query("SELECT * FROM Purchases WHERE PurchaseID=?", (pid,), one=True)
    if not existing:
        flash("Purchase not found.", "error")
        return redirect(url_for("purchases_list"))

    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        costs = request.form.getlist("unit_cost[]")
        lines = [(int(p), float(q), float(c)) for p, q, c in zip(product_ids, qtys, costs) if p and q]

        po_number = f["po_number"] or existing["PONumber"]
        create_purchase(
            supplier_id=int(f["supplier_id"]), po_number=po_number, purchase_date=f["purchase_date"],
            invoice_number=f["invoice_number"], status=f["status"], payment_status=f["payment_status"],
            amount_paid=float(f["amount_paid"] or 0), notes=f.get("notes", ""), lines=lines,
            interstate_override=bool(f.get("interstate_purchase")),
            reverse_charge=bool(f.get("reverse_charge")), itc_eligible=bool(f.get("itc_eligible")),
            purchase_id=pid)
        save_custom_fields("Purchase", pid, f)
        flash(f"Purchase {po_number} updated.", "success")
        return redirect(url_for("purchase_view", pid=pid))

    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    products = db.query("SELECT * FROM Products WHERE Active=1 ORDER BY ProductName")
    existing_lines = db.query("SELECT * FROM PurchaseLines WHERE PurchaseID=?", (pid,))
    custom_fields = get_custom_field_defs("Purchase")
    custom_values = get_custom_values("Purchase", pid)
    return render_template("purchase_form.html", suppliers=suppliers, products=products,
                            today=existing["PurchaseDate"], company=company, custom_fields=custom_fields,
                            custom_values=custom_values, cf_record_id=pid, custom_attachments={},
                            purchase=existing, existing_lines=existing_lines)


# ---------------------------------------------------------------------
# Purchases: Import from Vendor File
#
# Some suppliers can export an "SO Details" spreadsheet of what they've
# shipped instead of (or alongside) sending an invoice to type up by
# hand. This is a two-step flow: upload + parse the file (this doesn't
# save anything yet), review/match each line to one of our Products (or
# create a new one), then confirm to actually create the Purchase(s).
# Matches are remembered per supplier (SupplierProductMap) so the next
# file from the same supplier mostly auto-matches.
# ---------------------------------------------------------------------

def find_supplier_product_match(supplier_id, material_code):
    """Returns a Products row already known to correspond to this
    supplier's material code, or None if there's no known match yet.
    Checks a saved SupplierProductMap first, then falls back to a
    Products.SKU that happens to equal the vendor's code."""
    mapped = db.query("""SELECT pr.* FROM SupplierProductMap m JOIN Products pr ON pr.ProductID = m.ProductID
                       WHERE m.SupplierID=? AND m.VendorCode=?""", (supplier_id, material_code), one=True)
    if mapped:
        return mapped
    return db.query("SELECT * FROM Products WHERE SKU=?", (material_code,), one=True)


@app.route("/purchases/import", methods=["GET", "POST"])
@admin_required
def purchase_import_upload():
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id")
        file_storage = request.files.get("file")
        if not supplier_id:
            flash("Select which supplier this file is from.", "error")
            return redirect(url_for("purchase_import_upload"))
        if not file_storage or not file_storage.filename:
            flash("Choose a file to upload.", "error")
            return redirect(url_for("purchase_import_upload"))

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            file_storage.save(tmp.name)
            tmp_path = tmp.name
        try:
            result = purchase_import.parse_so_details(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if not result["ok"]:
            flash(result["message"], "error")
            return redirect(url_for("purchase_import_upload"))

        supplier_id = int(supplier_id)
        rows = result["rows"]
        # Group rows by SO Number (order of first appearance), so the
        # review page can show one header (PO number/date/etc.) per
        # group and create one Purchase per group on confirm.
        group_index_by_so = {}
        groups = []
        for row in rows:
            key = row["so_number"] or "(no SO number)"
            if key not in group_index_by_so:
                group_index_by_so[key] = len(groups)
                groups.append(dict(so_number=key, dates=[], rows=[]))
            g = groups[group_index_by_so[key]]
            g["rows"].append(row)
            if row["so_date"]:
                g["dates"].append(row["so_date"])

        for g in groups:
            g["purchase_date"] = max(g["dates"]) if g["dates"] else today_str()
            g["suggested_po_number"] = f"SO-{g['so_number']}" if g["so_number"] != "(no SO number)" \
                else f"IMPORT-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Attach a match (or None) to every row, a group index, and a stable
        # global row index (0..len(rows)-1) that the review template uses to
        # name each row's form fields, since rows are also re-grouped by SO
        # Number for display.
        for i, row in enumerate(rows):
            row["group_index"] = group_index_by_so[row["so_number"] or "(no SO number)"]
            row["match"] = find_supplier_product_match(supplier_id, row["material_code"])
            row["global_index"] = i

        supplier = db.query("SELECT * FROM Suppliers WHERE SupplierID=?", (supplier_id,), one=True)
        products = db.query("SELECT * FROM Products WHERE Active=1 ORDER BY ProductName")
        matched_count = sum(1 for row in rows if row["match"])
        flash(f"{result['message']} {matched_count} of {len(rows)} already matched to a known product "
              f"— review the rest below before confirming.", "success")
        return render_template("purchase_import_review.html", supplier=supplier, groups=groups, rows=rows,
                                products=products, today=today_str())

    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    return render_template("purchase_import_upload.html", suppliers=suppliers)


@app.route("/purchases/import/confirm", methods=["POST"])
@admin_required
def purchase_import_confirm():
    f = request.form
    supplier_id = int(f["supplier_id"])
    row_count = int(f.get("row_count") or 0)
    group_count = int(f.get("group_count") or 0)

    # Build each group's header info and an empty line list first.
    group_lines = [[] for _ in range(group_count)]
    group_info = []
    for g in range(group_count):
        group_info.append(dict(
            po_number=(f.get(f"po_number_{g}") or "").strip() or f"IMPORT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{g}",
            purchase_date=f.get(f"purchase_date_{g}") or today_str(),
            payment_status=f.get(f"payment_status_{g}") or "Unpaid",
            amount_paid=float(f.get(f"amount_paid_{g}") or 0),
            notes=f.get(f"notes_{g}") or "",
            reverse_charge=bool(f.get(f"reverse_charge_{g}")),
            itc_eligible=bool(f.get(f"itc_eligible_{g}")),
            interstate_override=bool(f.get(f"interstate_override_{g}")),
        ))

    created_purchase_ids = []
    skipped = 0
    new_products_created = 0
    for i in range(row_count):
        group_idx = int(f.get(f"row_group_{i}", 0))
        material_code = f.get(f"row_material_code_{i}", "")
        material_name = f.get(f"row_material_name_{i}", "")
        qty = float(f.get(f"row_qty_{i}") or 0)
        unit_cost = float(f.get(f"row_unit_cost_{i}") or 0)
        if qty <= 0:
            skipped += 1
            continue
        product_id_raw = f.get(f"row_product_id_{i}", "")
        remember = bool(f.get(f"row_remember_{i}"))

        if product_id_raw == "__new__" or not product_id_raw:
            new_name = (f.get(f"row_new_product_name_{i}") or material_name or material_code).strip()
            sku_candidate = material_code
            # Selling price isn't in a vendor purchase file, so default it to the purchase cost
            # rather than 0 - a new product should have a usable price right away (e.g. when
            # issuing stock to a salesperson), not a ₹0 price that's easy to miss and correct later
            # on the Products page.
            default_selling_price = unit_cost
            product_id = None
            # Try the material code as-is, then a supplier-prefixed variant, then keep appending a
            # counter until an unused SKU is found - covers both a collision against an existing
            # product AND two rows in the same file sharing a material code that both need a new
            # product (each previous attempt in that case fails on the exact same fallback string).
            candidates = [sku_candidate, f"S{supplier_id}-{material_code}"]
            n = 2
            while product_id is None:
                if not candidates:
                    candidates = [f"S{supplier_id}-{material_code}-{n}"]
                    n += 1
                sku_candidate = candidates.pop(0)
                try:
                    product_id = db.execute("""INSERT INTO Products (SKU, ProductName, Unit, CostPrice, SellingPrice,
                                DefaultSupplierID, Active) VALUES (?,?,?,?,?,?,1)""",
                                (sku_candidate, new_name, "PCS", unit_cost, default_selling_price, supplier_id))
                except Exception:
                    continue  # SKU already taken - try the next candidate
            remember = True  # always remember a brand-new product's mapping, so it isn't recreated next time
            new_products_created += 1
        else:
            product_id = int(product_id_raw)

        if remember and material_code:
            db.execute("""INSERT INTO SupplierProductMap (SupplierID, VendorCode, VendorName, ProductID)
                        VALUES (?,?,?,?)
                        ON CONFLICT(SupplierID, VendorCode) DO UPDATE SET ProductID=excluded.ProductID,
                        VendorName=excluded.VendorName""",
                       (supplier_id, material_code, material_name, product_id))

        group_lines[group_idx].append((product_id, qty, unit_cost))

    for g, lines in enumerate(group_lines):
        if not lines:
            continue
        info = group_info[g]
        purchase_id = create_purchase(
            supplier_id=supplier_id, po_number=info["po_number"], purchase_date=info["purchase_date"],
            invoice_number="", status="Received", payment_status=info["payment_status"],
            amount_paid=info["amount_paid"], notes=info["notes"] or "Imported from vendor file", lines=lines,
            interstate_override=info["interstate_override"], reverse_charge=info["reverse_charge"],
            itc_eligible=info["itc_eligible"])
        created_purchase_ids.append(purchase_id)

    if not created_purchase_ids:
        flash("Nothing to import — every line had zero quantity.", "error")
        return redirect(url_for("purchase_import_upload"))

    msg = f"Imported {len(created_purchase_ids)} purchase(s) from the file."
    if skipped:
        msg += f" ({skipped} zero-quantity line(s) skipped.)"
    flash(msg, "success")
    if new_products_created:
        flash(f"{new_products_created} new product(s) were created with their selling price defaulted to their "
              f"purchase cost (no markup) — review and update pricing on the Inventory page before selling them.",
              "warning")
    if len(created_purchase_ids) == 1:
        return redirect(url_for("purchase_view", pid=created_purchase_ids[0]))
    return redirect(url_for("purchases_list"))


# ---------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------

@app.route("/sales")
def sales_list():
    sales = db.query("""SELECT s.*, c.CustomerName, c.IsUnassignedBucket FROM Sales s
                      JOIN Customers c ON c.CustomerID=s.CustomerID ORDER BY s.SaleDate DESC, s.SaleID DESC""")
    filt = request.args.get("filter", "")
    if filt == "unassigned":
        sales = [s for s in sales if s["IsUnassignedBucket"]]
    return render_template("sales_list.html", sales=sales, columns=get_effective_columns("Sale"), filt=filt)


def create_sale(customer_id, sale_date, status, payment_status, payment_due_date, amount_received, notes,
                 place_of_supply_code, lines, reverse_charge=False, invoice_no=None, sale_id=None,
                 post_inventory=True):
    """Creates a Sale + SalesLines with the GST breakup, or (when `sale_id`
    is passed) EDITS an existing one in place: its old SalesLines and the
    InventoryTransaction rows it originally created are removed first, then
    recreated from the new `lines`/`status` — mirrors create_purchase()'s
    edit path. `lines` is a list of (product_id, qty, unit_price) tuples.
    The invoice number is never changed on an edit (pass the existing one
    in via `invoice_no`) so numbering/any already-printed invoice stays
    consistent.

    `post_inventory=False` skips posting this Sale's own stock-deduction
    InventoryTransactions — used when the stock effect is already fully
    accounted for elsewhere (e.g. a Sale auto-created from a reconciled
    Stock Issue, whose Issue/Return-In/Free-Scheme transactions already
    cover the sold units; a second deduction here would double-count it)."""
    company = get_company_settings()
    place_of_supply_name = STATE_NAME_BY_CODE.get(place_of_supply_code, "")
    is_interstate = 1 if (company["StateCode"] and place_of_supply_code != company["StateCode"]) else 0

    if invoice_no is None:
        invoice_no = next_invoice_number()

    taxable_total = cgst_total = sgst_total = igst_total = 0.0
    line_data = []
    for prod_id, qty, price in lines:
        prod = db.query("SELECT HSNCode, GSTRate FROM Products WHERE ProductID=?", (prod_id,), one=True)
        hsn = (prod["HSNCode"] or "") if prod else ""
        gst_rate = (prod["GSTRate"] or 0) if prod else 0
        taxable_value = round(qty * price, 2)
        gst = compute_line_gst(taxable_value, gst_rate, is_interstate)
        taxable_total += taxable_value
        cgst_total += gst["cgst_amt"]
        sgst_total += gst["sgst_amt"]
        igst_total += gst["igst_amt"]
        line_data.append((prod_id, qty, price, taxable_value, hsn, gst_rate, gst))

    raw_total = taxable_total + cgst_total + sgst_total + igst_total
    grand_total = round(raw_total)
    round_off = round(grand_total - raw_total, 2)

    if sale_id is None:
        sale_id = db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status, PaymentStatus,
                    PaymentDueDate, TotalAmount, AmountReceived, Notes, PlaceOfSupplyState, PlaceOfSupplyStateCode,
                    IsInterState, TaxableAmount, CGSTAmount, SGSTAmount, IGSTAmount, RoundOff, ReverseCharge)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (invoice_no, customer_id, sale_date, status, payment_status,
                     payment_due_date or None, grand_total, amount_received, notes,
                     place_of_supply_name, place_of_supply_code, is_interstate,
                     round(taxable_total, 2), round(cgst_total, 2), round(sgst_total, 2), round(igst_total, 2),
                     round_off, 1 if reverse_charge else 0))
    else:
        db.execute("""UPDATE Sales SET CustomerID=?, SaleDate=?, Status=?, PaymentStatus=?, PaymentDueDate=?,
                    TotalAmount=?, AmountReceived=?, Notes=?, PlaceOfSupplyState=?, PlaceOfSupplyStateCode=?,
                    IsInterState=?, TaxableAmount=?, CGSTAmount=?, SGSTAmount=?, IGSTAmount=?, RoundOff=?,
                    ReverseCharge=? WHERE SaleID=?""",
                   (customer_id, sale_date, status, payment_status, payment_due_date or None,
                    grand_total, amount_received, notes, place_of_supply_name, place_of_supply_code, is_interstate,
                    round(taxable_total, 2), round(cgst_total, 2), round(sgst_total, 2), round(igst_total, 2),
                    round_off, 1 if reverse_charge else 0, sale_id))
        db.execute("DELETE FROM SalesLines WHERE SaleID=?", (sale_id,))
        db.execute("DELETE FROM InventoryTransactions WHERE RefType='Sale' AND RefID=?", (sale_id,))

    for prod_id, qty, price, taxable_value, hsn, gst_rate, gst in line_data:
        db.execute("""INSERT INTO SalesLines (SaleID, ProductID, Qty, UnitPrice, LineTotal, HSNCode, GSTRate,
                    TaxableValue, CGSTRate, CGSTAmount, SGSTRate, SGSTAmount, IGSTRate, IGSTAmount)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (sale_id, prod_id, qty, price, taxable_value, hsn, gst_rate, taxable_value,
                    gst["cgst_rate"], gst["cgst_amt"], gst["sgst_rate"], gst["sgst_amt"],
                    gst["igst_rate"], gst["igst_amt"]))
        if status == "Completed" and post_inventory:
            db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                        QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                       (prod_id, sale_date, "Sale", -qty, "Sale", sale_id, invoice_no))
    return sale_id


def get_unassigned_customer_id():
    """The single system 'Unassigned' customer that reconciled Stock Issue
    sales are billed to until reassigned - created on first use."""
    row = db.query("SELECT CustomerID FROM Customers WHERE IsUnassignedBucket=1", one=True)
    if row:
        return row["CustomerID"]
    return db.execute("""INSERT INTO Customers (CustomerName, Active, IsUnassignedBucket)
                       VALUES ('Unassigned (Van/Route Sales)', 1, 1)""")


@app.route("/sales/new", methods=["GET", "POST"])
def sale_form():
    company = get_company_settings()
    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        prices = request.form.getlist("unit_price[]")
        lines = [(int(p), float(q), float(pr)) for p, q, pr in zip(product_ids, qtys, prices) if p and q]
        place_of_supply_code = f.get("place_of_supply_code", "") or company["StateCode"]

        sale_id = create_sale(
            customer_id=int(f["customer_id"]), sale_date=f["sale_date"], status=f["status"],
            payment_status=f["payment_status"], payment_due_date=f.get("payment_due_date"),
            amount_received=float(f["amount_received"] or 0), notes=f.get("notes", ""),
            place_of_supply_code=place_of_supply_code, lines=lines,
            reverse_charge=bool(f.get("reverse_charge")), invoice_no=f.get("invoice_number") or None)
        save_custom_fields("Sale", sale_id, f)
        flash(f"Sale recorded.", "success")
        return redirect(url_for("sale_view", sid=sale_id))
    customers = db.query("SELECT * FROM Customers WHERE Active=1 ORDER BY CustomerName")
    products = get_products_with_stock()
    custom_fields = get_custom_field_defs("Sale")
    return render_template("sale_form.html", customers=customers, products=products, today=today_str(),
                            states=INDIAN_STATES, company=company, suggested_invoice=None,
                            custom_fields=custom_fields, custom_values={},
                            cf_record_id=None, custom_attachments={}, sale=None, existing_lines=None)


@app.route("/sales/<int:sid>/edit", methods=["GET", "POST"])
@admin_required
def sale_edit(sid):
    """Admin-only: correct a mistake on an already-saved sale without
    deleting and re-entering it. The Invoice Number is preserved."""
    company = get_company_settings()
    existing = db.query("SELECT * FROM Sales WHERE SaleID=?", (sid,), one=True)
    if not existing:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))

    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        prices = request.form.getlist("unit_price[]")
        lines = [(int(p), float(q), float(pr)) for p, q, pr in zip(product_ids, qtys, prices) if p and q]
        place_of_supply_code = f.get("place_of_supply_code", "") or company["StateCode"]

        create_sale(
            customer_id=int(f["customer_id"]), sale_date=f["sale_date"], status=f["status"],
            payment_status=f["payment_status"], payment_due_date=f.get("payment_due_date"),
            amount_received=float(f["amount_received"] or 0), notes=f.get("notes", ""),
            place_of_supply_code=place_of_supply_code, lines=lines,
            reverse_charge=bool(f.get("reverse_charge")), invoice_no=existing["InvoiceNumber"], sale_id=sid)
        save_custom_fields("Sale", sid, f)
        flash(f"Sale {existing['InvoiceNumber']} updated.", "success")
        return redirect(url_for("sale_view", sid=sid))

    customers = db.query("SELECT * FROM Customers WHERE Active=1 ORDER BY CustomerName")
    products = get_products_with_stock()
    existing_lines = db.query("SELECT * FROM SalesLines WHERE SaleID=?", (sid,))
    custom_fields = get_custom_field_defs("Sale")
    custom_values = get_custom_values("Sale", sid)
    return render_template("sale_form.html", customers=customers, products=products, today=existing["SaleDate"],
                            states=INDIAN_STATES, company=company, suggested_invoice=None,
                            custom_fields=custom_fields, custom_values=custom_values, cf_record_id=sid,
                            custom_attachments={}, sale=existing, existing_lines=existing_lines)


# ---------------------------------------------------------------------
# Sales: bulk Excel upload
#
# Off an app-provided template (download it, fill it in, upload it).
# Two-step flow, same shape as the Purchases vendor-file import: upload +
# parse + auto-match Customer/Product by exact name, review/fix any
# unmatched rows and edit per-invoice header fields, then confirm to
# actually create the Sale(s) via the same create_sale() every other
# entry point uses - so GST and stock are computed identically.
# ---------------------------------------------------------------------

@app.route("/sales/import/template")
def sales_import_template():
    from flask import send_file
    buf = bulk_import.build_sales_template()
    return send_file(buf, as_attachment=True, download_name="Sales_Upload_Template.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/sales/import", methods=["GET", "POST"])
def sales_import_upload():
    if request.method == "POST":
        file_storage = request.files.get("file")
        if not file_storage or not file_storage.filename:
            flash("Choose a file to upload.", "error")
            return redirect(url_for("sales_import_upload"))

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            file_storage.save(tmp.name)
            tmp_path = tmp.name
        try:
            result = bulk_import.parse_sales_excel(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if not result["ok"]:
            flash(result["message"], "error")
            return redirect(url_for("sales_import_upload"))

        rows = result["rows"]
        # Group by Invoice Number - a blank invoice number gets its own
        # group per row (each becomes its own single-line sale), same as
        # purchase_import_upload() treats a blank SO Number.
        group_index_by_key = {}
        groups = []
        for i, row in enumerate(rows):
            key = row["invoice_number"] or f"__blank_{i}__"
            if key not in group_index_by_key:
                group_index_by_key[key] = len(groups)
                groups.append(dict(invoice_number=row["invoice_number"], dates=[], rows=[]))
            g = groups[group_index_by_key[key]]
            g["rows"].append(row)
            if row["date"]:
                g["dates"].append(row["date"])

        for g in groups:
            g["sale_date"] = max(g["dates"]) if g["dates"] else today_str()
            g["payment_status"] = g["rows"][0]["payment_status"]
            g["amount_received"] = sum(r["amount_received"] for r in g["rows"]) or g["rows"][0]["amount_received"]

        customers = db.query("SELECT * FROM Customers WHERE Active=1 ORDER BY CustomerName")
        products = get_products_with_stock()
        customer_by_name = {c["CustomerName"].strip().lower(): c for c in customers}
        product_by_name = {p["ProductName"].strip().lower(): p for p in products}

        for i, row in enumerate(rows):
            row["global_index"] = i
            row["group_index"] = group_index_by_key[row["invoice_number"] or f"__blank_{i}__"]
            row["customer_match"] = customer_by_name.get(row["customer_name"].strip().lower())
            row["product_match"] = product_by_name.get(row["product"].strip().lower())

        matched_cust = sum(1 for r in rows if r["customer_match"])
        matched_prod = sum(1 for r in rows if r["product_match"])
        flash(f"{result['message']} {matched_cust}/{len(rows)} customers matched, "
              f"{matched_prod}/{len(rows)} products matched — review below before confirming.", "success")
        return render_template("sales_import_review.html", groups=groups, rows=rows, customers=customers,
                                products=products, today=today_str())

    return render_template("sales_import_upload.html")


@app.route("/sales/import/confirm", methods=["POST"])
def sales_import_confirm():
    f = request.form
    company = get_company_settings()
    row_count = int(f.get("row_count") or 0)
    group_count = int(f.get("group_count") or 0)

    group_lines = [[] for _ in range(group_count)]
    group_info = []
    for g in range(group_count):
        group_info.append(dict(
            invoice_number=(f.get(f"invoice_number_{g}") or "").strip() or None,
            sale_date=f.get(f"sale_date_{g}") or today_str(),
            payment_status=f.get(f"payment_status_{g}") or "Unpaid",
            amount_received=float(f.get(f"amount_received_{g}") or 0),
        ))

    created_sale_ids = []
    skipped = 0
    for i in range(row_count):
        group_idx = int(f.get(f"row_group_{i}", 0))
        qty = float(f.get(f"row_qty_{i}") or 0)
        rate = float(f.get(f"row_rate_{i}") or 0)
        if qty <= 0:
            skipped += 1
            continue

        product_id_raw = f.get(f"row_product_id_{i}", "")
        if not product_id_raw or product_id_raw == "__skip__":
            skipped += 1
            continue
        product_id = int(product_id_raw)

        group_lines[group_idx].append((product_id, qty, rate))

    # Resolve (and, for "__new__", actually create) each group's customer
    # only now that we know the group has at least one usable line - so a
    # group whose only line(s) got skipped for having no product match
    # doesn't leave behind an unused new Customer row.
    for g, lines in enumerate(group_lines):
        if not lines:
            continue
        customer_id_raw = f.get(f"customer_id_{g}", "")
        customer_id = None
        if customer_id_raw == "__new__":
            new_name = (f.get(f"new_customer_name_{g}") or "").strip()
            if new_name:
                customer_id = db.execute("INSERT INTO Customers (CustomerName, Active) VALUES (?,1)", (new_name,))
        elif customer_id_raw and customer_id_raw != "__skip__":
            customer_id = int(customer_id_raw)
        if not customer_id:
            continue

        info = group_info[g]
        customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (customer_id,), one=True)
        place_of_supply_code = (customer["StateCode"] if customer else "") or company["StateCode"]
        sale_id = create_sale(
            customer_id=customer_id, sale_date=info["sale_date"], status="Completed",
            payment_status=info["payment_status"], payment_due_date=None,
            amount_received=info["amount_received"], notes="Imported from Excel upload",
            place_of_supply_code=place_of_supply_code, lines=lines, invoice_no=info["invoice_number"])
        created_sale_ids.append(sale_id)

    if not created_sale_ids:
        flash("Nothing to import — no group had both a matched customer and at least one usable line.", "error")
        return redirect(url_for("sales_import_upload"))

    msg = f"Imported {len(created_sale_ids)} sale(s) from the file."
    if skipped:
        msg += f" ({skipped} line(s) skipped — no product/customer match or zero quantity.)"
    flash(msg, "success")
    if len(created_sale_ids) == 1:
        return redirect(url_for("sale_view", sid=created_sale_ids[0]))
    return redirect(url_for("sales_list"))


@app.route("/sales/<int:sid>")
def sale_view(sid):
    sale = db.query("""SELECT s.*, c.CustomerName, c.Phone, c.Address, c.IsUnassignedBucket FROM Sales s
                     JOIN Customers c ON c.CustomerID=s.CustomerID WHERE s.SaleID=?""", (sid,), one=True)
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))
    custom_fields = get_custom_field_defs("Sale")
    custom_values = get_custom_values("Sale", sid)
    return render_template("sale_view.html", sale=sale, lines=lines,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=sid, custom_attachments=get_custom_attachments("Sale", sid))


@app.route("/sales/<int:sid>/reassign", methods=["GET", "POST"])
def sale_reassign(sid):
    """Split an 'Unassigned' Sale's lines out to the real customer(s). Each
    line's quantity can itself be broken down across multiple customers (e.g.
    20 units of a product sold that day split 8/7/5 across three customers) -
    each split names a quantity plus an existing Customer or a brand-new one
    typed directly on this page (no separate Add Customer trip needed).
    Splits going to the same new-customer details in one submission are
    grouped into a single new Customer + Sale. Any quantity not covered by a
    split stays behind on the original Sale."""
    sale = db.query("SELECT s.*, c.IsUnassignedBucket FROM Sales s JOIN Customers c ON c.CustomerID=s.CustomerID "
                     "WHERE s.SaleID=?", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))
    if not sale["IsUnassignedBucket"]:
        flash("This sale is already assigned to a customer.", "error")
        return redirect(url_for("sale_view", sid=sid))
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))

    if request.method == "POST":
        f = request.form
        company = get_company_settings()
        groups = {}       # target_key -> {"lines": [...], "choice": str, "new_info": {...}|None}
        group_order = []
        keep_lines = []   # remaining (unsplit) quantity, left on the original Unassigned sale
        for line in lines:
            lid = line["LineID"]
            choices = f.getlist(f"split_choice_{lid}[]")
            qtys = f.getlist(f"split_qty_{lid}[]")
            new_names = f.getlist(f"split_new_name_{lid}[]")
            new_phones = f.getlist(f"split_new_phone_{lid}[]")
            new_addresses = f.getlist(f"split_new_address_{lid}[]")
            new_gstins = f.getlist(f"split_new_gstin_{lid}[]")
            new_states = f.getlist(f"split_new_state_{lid}[]")
            allocated = 0.0
            for i, choice in enumerate(choices):
                if not choice or choice == "keep":
                    continue
                try:
                    qty = float(qtys[i]) if i < len(qtys) and qtys[i] else 0.0
                except ValueError:
                    qty = 0.0
                if qty <= 0:
                    continue
                new_info = None
                if choice.startswith("existing:"):
                    key = choice
                else:
                    new_name = (new_names[i] if i < len(new_names) else "").strip()
                    if not new_name:
                        continue
                    new_phone = (new_phones[i] if i < len(new_phones) else "").strip()
                    key = f"new:{new_name.lower()}:{new_phone}"
                    new_info = dict(
                        name=new_name, phone=new_phone,
                        address=(new_addresses[i] if i < len(new_addresses) else "").strip(),
                        gstin=(new_gstins[i] if i < len(new_gstins) else "").strip(),
                        state_code=(new_states[i] if i < len(new_states) else "").strip())
                # Cap this split at whatever's left unallocated on the line, so a
                # typo/over-entry can't allocate more than was actually sold.
                qty = min(qty, max(line["Qty"] - allocated, 0))
                if qty <= 0:
                    continue
                allocated = round(allocated + qty, 4)
                if key not in groups:
                    group_order.append(key)
                    groups[key] = dict(lines=[], choice=choice, new_info=new_info)
                groups[key]["lines"].append((line["ProductID"], qty, line["UnitPrice"]))
            remaining = round(line["Qty"] - allocated, 4)
            if remaining > 0:
                keep_lines.append((line["ProductID"], remaining, line["UnitPrice"]))

        new_sale_ids = []
        for key in group_order:
            g = groups[key]
            if g["choice"].startswith("existing:"):
                customer_id = int(g["choice"].split(":", 1)[1])
            else:
                ni = g["new_info"]
                customer_id = db.execute(
                    """INSERT INTO Customers (CustomerName, Phone, Address, GSTIN, State, StateCode, Active)
                       VALUES (?,?,?,?,?,?,1)""",
                    (ni["name"], ni["phone"], ni["address"], ni["gstin"],
                     STATE_NAME_BY_CODE.get(ni["state_code"], ""), ni["state_code"]))
            customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (customer_id,), one=True)
            place_of_supply_code = (customer["StateCode"] if customer else "") or company["StateCode"]
            new_sale_id = create_sale(
                customer_id=customer_id, sale_date=sale["SaleDate"], status="Completed",
                payment_status=sale["PaymentStatus"], payment_due_date=None, amount_received=0,
                notes=f"Reassigned from Unassigned Sale #{sid}", place_of_supply_code=place_of_supply_code,
                lines=g["lines"], post_inventory=False)
            new_sale_ids.append(new_sale_id)

        if not new_sale_ids:
            flash("Nothing reassigned — enter a quantity and pick a customer (existing or new) for at least one split.", "error")
            return redirect(url_for("sale_reassign", sid=sid))

        if keep_lines:
            create_sale(customer_id=sale["CustomerID"], sale_date=sale["SaleDate"], status=sale["Status"],
                        payment_status=sale["PaymentStatus"], payment_due_date=sale["PaymentDueDate"],
                        amount_received=sale["AmountReceived"], notes=sale["Notes"],
                        place_of_supply_code=sale["PlaceOfSupplyStateCode"], lines=keep_lines,
                        invoice_no=sale["InvoiceNumber"], sale_id=sid, post_inventory=False)
            flash(f"Reassigned {len(new_sale_ids)} customer group(s); "
                  f"{len(keep_lines)} line(s) left unassigned on this sale.", "success")
        else:
            # Every line was reassigned - the original Unassigned sale has nothing left to bill.
            db.execute("UPDATE StockIssues SET SaleID=NULL WHERE SaleID=?", (sid,))
            db.execute("DELETE FROM SalesLines WHERE SaleID=?", (sid,))
            db.execute("DELETE FROM Sales WHERE SaleID=?", (sid,))
            flash(f"Reassigned all lines to {len(new_sale_ids)} customer group(s). "
                  f"The Unassigned sale has been removed.", "success")
        if len(new_sale_ids) == 1 and not keep_lines:
            return redirect(url_for("sale_view", sid=new_sale_ids[0]))
        return redirect(url_for("sales_list"))

    customers = db.query("SELECT * FROM Customers WHERE Active=1 AND IsUnassignedBucket=0 ORDER BY CustomerName")
    return render_template("sale_reassign.html", sale=sale, lines=lines, customers=customers,
                            indian_states=INDIAN_STATES)


@app.route("/sales/<int:sid>/invoice")
def sale_invoice(sid):
    sale = db.query("""SELECT s.*, c.CustomerName, c.Phone, c.Address, c.GSTIN AS CustomerGSTIN,
                     c.State AS CustomerState, c.StateCode AS CustomerStateCode FROM Sales s
                     JOIN Customers c ON c.CustomerID=s.CustomerID WHERE s.SaleID=?""", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))
    company = get_company_settings()
    words = amount_in_words(sale["TotalAmount"])
    return render_template("invoice.html", sale=sale, lines=lines, company=company, amount_words=words)


@app.route("/sales/<int:sid>/invoice.pdf")
def sale_invoice_pdf(sid):
    from invoice_pdf import build_invoice_pdf
    sale = db.query("""SELECT s.*, c.CustomerName, c.Phone, c.Address, c.GSTIN AS CustomerGSTIN,
                     c.State AS CustomerState, c.StateCode AS CustomerStateCode FROM Sales s
                     JOIN Customers c ON c.CustomerID=s.CustomerID WHERE s.SaleID=?""", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))
    company = get_company_settings()
    words = amount_in_words(sale["TotalAmount"])
    pdf_bytes = build_invoice_pdf(sale, lines, company, words)
    from flask import Response
    safe_name = sale["InvoiceNumber"].replace("/", "-")
    return Response(pdf_bytes, mimetype="application/pdf",
                     headers={"Content-Disposition": f'inline; filename="Invoice-{safe_name}.pdf"'})


# ---------------------------------------------------------------------
# Stock Issues (van/route sales: issue stock to a salesperson, reconcile
# sold/returned/cash at day's end)
# ---------------------------------------------------------------------

@app.route("/stock-issues")
def stock_issues_list():
    issues = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                       JOIN Employees e ON e.EmployeeID = si.EmployeeID
                       ORDER BY si.IssueDate DESC, si.IssueID DESC""")
    line_agg_rows = db.query("""SELECT IssueID,
                              COALESCE(SUM(QtySold), 0) AS qty_sold,
                              COALESCE(SUM(DiscountAmount), 0) AS discount,
                              COALESCE(SUM(SchemeClaimAmount), 0) AS scheme,
                              COALESCE(SUM(QtyIssued - QtySold - QtyReturned - QtyFree), 0) AS unaccounted
                              FROM StockIssueLines GROUP BY IssueID""")
    line_agg_by_issue = {r["IssueID"]: r for r in line_agg_rows}
    issues = [dict(i) for i in issues]
    target_cache = {}
    for i in issues:
        agg = line_agg_by_issue.get(i["IssueID"])
        i["QtySoldTotal"] = round(agg["qty_sold"], 2) if agg else 0
        i["DiscountTotal"] = round(agg["discount"], 2) if agg else 0
        i["SchemeTotal"] = round(agg["scheme"], 2) if agg else 0
        i["UnaccountedTotal"] = round(agg["unaccounted"], 2) if agg else 0
        # This employee's whole-month Qty Sold target progress, if one is set for the
        # issue's month - a quick "on pace or not" signal without leaving the list.
        y, m = int(i["IssueDate"][:4]), int(i["IssueDate"][5:7])
        cache_key = (i["EmployeeID"], y, m)
        if cache_key not in target_cache:
            target_cache[cache_key] = get_employee_month_target_progress(i["EmployeeID"], y, m)
        i["TargetProgress"] = target_cache[cache_key]
    totals = {
        "expected": sum(i["ExpectedAmount"] or 0 for i in issues),
        "collected": sum(i["CashCollected"] or 0 for i in issues),
        "discrepancy": sum(i["Discrepancy"] or 0 for i in issues),
        "qty_sold": sum(i["QtySoldTotal"] for i in issues),
        "discount": sum(i["DiscountTotal"] for i in issues),
        "scheme": sum(i["SchemeTotal"] for i in issues),
        "unaccounted": sum(i["UnaccountedTotal"] for i in issues),
    }
    return render_template("stock_issues_list.html", issues=issues, totals=totals)


def stock_issue_post_line(issue_id, product_id, qty, price, txn_date, txn_notes):
    """Issue (deduct stock for) qty of product_id against a Stock Issue, consolidated to one
    StockIssueLines row per product per issue rather than a separate row every time. If this
    issue already has a line for this product (from the original Issue Stock action or an
    earlier 'Add More Products' top-up on the same day), its Qty Issued is topped up in place
    and its Unit Price becomes the qty-weighted average of the old and new price, so a single
    consolidated line still produces the correct expected revenue at reconciliation. Otherwise a
    new line is created. Either way, a fresh 'Issue' InventoryTransactions row is posted for the
    qty being added, so stock deduction and the audit trail are unaffected by the consolidation."""
    existing = db.query("SELECT LineID, QtyIssued, UnitPrice FROM StockIssueLines WHERE IssueID=? AND ProductID=?",
                        (issue_id, product_id), one=True)
    if existing:
        new_qty = (existing["QtyIssued"] or 0) + qty
        new_price = round((((existing["QtyIssued"] or 0) * (existing["UnitPrice"] or 0)) + (qty * price)) / new_qty, 4) \
            if new_qty else price
        db.execute("UPDATE StockIssueLines SET QtyIssued=?, UnitPrice=? WHERE LineID=?",
                   (new_qty, new_price, existing["LineID"]))
    else:
        db.execute("""INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice)
                    VALUES (?,?,?,?)""", (issue_id, product_id, qty, price))
    db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
               (product_id, txn_date, "Issue", -qty, "StockIssue", issue_id, txn_notes))


@app.route("/stock-issues/new", methods=["GET", "POST"])
def stock_issue_form():
    if request.method == "POST":
        f = request.form
        employee_id = int(f["employee_id"])
        issue_date = f["issue_date"]
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        prices = request.form.getlist("unit_price[]")
        lines = [(int(p), float(q), float(pr)) for p, q, pr in zip(product_ids, qtys, prices) if p and q]
        if not lines:
            flash("Add at least one product line.", "error")
            return redirect(url_for("stock_issue_form"))

        issue_id = db.execute("""INSERT INTO StockIssues (EmployeeID, IssueDate, Status, Notes)
                    VALUES (?,?,?,?)""", (employee_id, issue_date, "Issued", f.get("notes", "")))
        for prod_id, qty, price in lines:
            # Consolidates same-product lines within this one submission too (e.g. the same
            # product picked on two rows), not just across a later "Add More Products" top-up.
            stock_issue_post_line(issue_id, prod_id, qty, price, issue_date, "Issued to salesperson")
        flash("Stock issued.", "success")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))

    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    products = get_products_with_stock()
    return render_template("stock_issue_form.html", employees=employees, products=products, today=today_str())


@app.route("/stock-issues/<int:issue_id>/add-lines", methods=["GET", "POST"])
def stock_issue_add_lines(issue_id):
    """Add more product lines to a Stock Issue that hasn't been reconciled yet -
    covers a salesperson getting re-stocked more than once in the same day
    (morning batch, then a top-up in the afternoon) without needing a second,
    separate Stock Issue for the same day/person."""
    issue = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                      JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    if issue["Status"] == "Reconciled":
        flash("This issue has already been reconciled — more products can't be added to it. "
              "Issue a new Stock Issue instead, or use 'Edit Reconciliation' if you need to correct it.", "error")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))

    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        prices = request.form.getlist("unit_price[]")
        lines = [(int(p), float(q), float(pr)) for p, q, pr in zip(product_ids, qtys, prices) if p and q]
        if not lines:
            flash("Add at least one product line.", "error")
            return redirect(url_for("stock_issue_add_lines", issue_id=issue_id))

        add_date = f.get("issue_date") or issue["IssueDate"]
        for prod_id, qty, price in lines:
            # Consolidated: a product already on this issue (from the original Issue Stock
            # action or an earlier top-up today) gets its existing line topped up in place
            # (qty-weighted average price) rather than appearing as a separate line — so
            # Reconcile shows one row per product for the day, not one row per top-up.
            stock_issue_post_line(issue_id, prod_id, qty, price, add_date,
                                   "Additional stock issued to salesperson (same-day top-up)")
        flash(f"Added {len(lines)} more product line(s) to this stock issue "
              f"(consolidated into existing product lines where applicable).", "success")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))

    products = get_products_with_stock()
    return render_template("stock_issue_add_lines.html", issue=issue, products=products, today=today_str())


@app.route("/stock-issues/<int:issue_id>")
def stock_issue_view(issue_id):
    issue = db.query("""SELECT si.*, e.EmployeeName, e.Phone FROM StockIssues si
                      JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    lines = db.query("""SELECT sil.*, pr.ProductName, pr.Unit FROM StockIssueLines sil
                      JOIN Products pr ON pr.ProductID = sil.ProductID WHERE sil.IssueID=?""", (issue_id,))
    # Column totals for the line table below - every numeric column except Unit Price
    # (summing a per-unit price across different products isn't a meaningful figure).
    line_totals = {
        "qty_issued": sum(l["QtyIssued"] or 0 for l in lines),
        "qty_sold": sum(l["QtySold"] or 0 for l in lines),
        "qty_returned": sum(l["QtyReturned"] or 0 for l in lines),
        "qty_free": sum(l["QtyFree"] or 0 for l in lines),
        "discount": sum(l["DiscountAmount"] or 0 for l in lines),
        "scheme_claim": sum(l["SchemeClaimAmount"] or 0 for l in lines),
        "unaccounted": sum((l["QtyIssued"] or 0) - (l["QtySold"] or 0) - (l["QtyReturned"] or 0) - (l["QtyFree"] or 0)
                            for l in lines),
    }
    y, m = int(issue["IssueDate"][:4]), int(issue["IssueDate"][5:7])
    target_progress = get_employee_month_target_progress(issue["EmployeeID"], y, m)
    return render_template("stock_issue_view.html", issue=issue, lines=lines, today=today_str(),
                            money_locked=stock_issue_money_locked(issue), line_totals=line_totals,
                            target_progress=target_progress)


def stock_issue_money_locked(issue):
    """True if a Stock Issue's reconciliation can no longer be safely edited or
    deleted - a due payment or scheme claim has already been recorded against
    it, so changing the underlying figures would orphan that money trail."""
    due_payments = db.query("SELECT COUNT(*) c FROM StockIssueDuePayments WHERE IssueID=?",
                            (issue["IssueID"],), one=True)["c"]
    return due_payments > 0 or issue["ClaimStatus"] != "Not Claimed"


@app.route("/stock-issues/<int:issue_id>/reconcile", methods=["GET", "POST"])
def stock_issue_reconcile(issue_id):
    # Joins Employees for EmployeeName - the template's page title and (new) Target-progress
    # box both display it; a plain "SELECT * FROM StockIssues" here previously left it blank.
    issue = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                      JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    is_reedit = issue["Status"] == "Reconciled"
    money_locked = False
    if is_reedit:
        current = get_current_user()
        if not current or current["Role"] != "Admin":
            flash("Only Admin accounts can edit an already-reconciled stock issue.", "error")
            return redirect(url_for("stock_issue_view", issue_id=issue_id))
        # A due payment or scheme claim already recorded no longer blocks re-editing outright -
        # the Admin instead chooses, right on this form, whether to keep that history (just
        # recompute the figures on top) or clear it and start fresh (same reversal-then-redo
        # approach Delete already uses), since the old due-payment/claim amounts were based on
        # figures that are about to change.
        money_locked = stock_issue_money_locked(issue)

    lines = db.query("""SELECT sil.*, pr.ProductName, pr.Unit, pr.CostPrice, pr.SchemeName, pr.SchemePercent
                      FROM StockIssueLines sil
                      JOIN Products pr ON pr.ProductID = sil.ProductID WHERE sil.IssueID=?""", (issue_id,))

    if request.method == "POST":
        f = request.form
        cash_amount = float(f.get("cash_amount") or 0)
        bank_amount = float(f.get("bank_amount") or 0)
        cash_collected = round(cash_amount + bank_amount, 2)
        expected_amount = 0.0
        scheme_amount = 0.0
        if is_reedit:
            # Remove the Return-In/Free Scheme entries the previous reconciliation posted
            # (but NOT the original Issue transaction) before re-posting fresh ones below.
            db.execute("""DELETE FROM InventoryTransactions WHERE RefType='StockIssue' AND RefID=?
                        AND TransactionType IN ('Return-In','Free Scheme')""", (issue_id,))
            if money_locked and f.get("money_data_action") == "clear":
                # Admin chose to clear existing due-payment/claim history rather than keep it,
                # since it was based on figures this edit is about to change - reset it the same
                # way Delete's reversal does, so nothing stale is left referencing old amounts.
                db.execute("DELETE FROM StockIssueDuePayments WHERE IssueID=?", (issue_id,))
                db.execute("""UPDATE StockIssues SET ClaimStatus='Not Claimed', ClaimedAt=NULL,
                            ClaimedAmount=NULL, ReceivedAt=NULL, ReceivedAmount=NULL, ClaimNotes=NULL
                            WHERE IssueID=?""", (issue_id,))
        sale_lines = []  # (product_id, qty_sold, effective_rate) - feeds the auto-created "Unassigned" Sale below
        for line in lines:
            qty_sold = float(f.get(f"qty_sold_{line['LineID']}") or 0)
            qty_returned = float(f.get(f"qty_returned_{line['LineID']}") or 0)
            qty_free = float(f.get(f"qty_free_{line['LineID']}") or 0)
            discount_amount = float(f.get(f"discount_amount_{line['LineID']}") or 0)
            scheme_claim_amount = float(f.get(f"scheme_claim_{line['LineID']}") or 0)
            line_comments = (f.get(f"comments_{line['LineID']}") or "").strip()
            db.execute("""UPDATE StockIssueLines SET QtySold=?, QtyReturned=?, QtyFree=?,
                        DiscountAmount=?, SchemeClaimAmount=?, LineComments=? WHERE LineID=?""",
                       (qty_sold, qty_returned, qty_free, discount_amount, scheme_claim_amount,
                        line_comments, line["LineID"]))
            expected_amount += (qty_sold * line["UnitPrice"]) - discount_amount
            # Scheme Amount (claimable back from the company) is now driven purely by the
            # directly-entered Scheme Claim Rs field, NOT by Discount Rs - a discount is a
            # real margin reduction the distributor absorbs, never something the company
            # reimburses, so it must never be counted as claimable.
            scheme_amount += scheme_claim_amount
            if qty_sold > 0:
                effective_rate = round(max((qty_sold * line["UnitPrice"]) - discount_amount, 0) / qty_sold, 4)
                sale_lines.append((line["ProductID"], qty_sold, effective_rate))
            if qty_returned > 0:
                db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                            QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                           (line["ProductID"], today_str(), "Return-In", qty_returned, "StockIssue", issue_id,
                            "Returned unsold from stock issue"))
            if qty_free > 0:
                db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                            QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                           (line["ProductID"], today_str(), "Free Scheme", -qty_free, "StockIssue", issue_id,
                            "Given free to customer under scheme"))
        expected_amount = round(max(expected_amount, 0), 2)
        scheme_amount = round(scheme_amount, 2)
        discrepancy = round(cash_collected - expected_amount, 2)
        amount_due = round(max(expected_amount - cash_collected, 0), 2)
        if amount_due <= 0:
            payment_status = "Paid"
        elif cash_collected <= 0:
            payment_status = "Unpaid"
        else:
            payment_status = "Partial"
        db.execute("""UPDATE StockIssues SET Status='Reconciled', CashCollected=?, CashAmount=?, BankAmount=?,
                    ExpectedAmount=?, Discrepancy=?, AmountDue=?, PaymentStatus=?, SchemeAmount=?, ReconciledAt=?, Notes=?
                    WHERE IssueID=?""",
                   (cash_collected, cash_amount, bank_amount, expected_amount, discrepancy, amount_due,
                    payment_status, scheme_amount,
                    datetime.now().isoformat(timespec="seconds"), f.get("notes", issue["Notes"]), issue_id))

        # Auto-create (or, on re-edit, update in place) a real GST Sale for the sold units,
        # billed to the system "Unassigned" customer until someone reassigns it via
        # /sales/<id>/reassign. Stock is NOT deducted again here (post_inventory=False) -
        # the Issue/Return-In/Free-Scheme transactions above already account for it.
        company = get_company_settings()
        existing_sale_id = issue["SaleID"]
        if sale_lines:
            existing_invoice = None
            if existing_sale_id:
                existing_row = db.query("SELECT InvoiceNumber FROM Sales WHERE SaleID=?", (existing_sale_id,), one=True)
                existing_invoice = existing_row["InvoiceNumber"] if existing_row else None
            new_sale_id = create_sale(
                customer_id=get_unassigned_customer_id(), sale_date=issue["IssueDate"], status="Completed",
                payment_status=payment_status, payment_due_date=None, amount_received=cash_collected,
                notes=f"Auto-created from Stock Issue #{issue_id} reconciliation",
                place_of_supply_code=company["StateCode"], lines=sale_lines,
                invoice_no=existing_invoice, sale_id=existing_sale_id, post_inventory=False)
            if new_sale_id != existing_sale_id:
                db.execute("UPDATE StockIssues SET SaleID=? WHERE IssueID=?", (new_sale_id, issue_id))
        elif existing_sale_id:
            # Re-edited down to nothing sold - nothing left to bill, drop the link (the Sale
            # itself is left as-is rather than deleted, since it may already have been
            # reassigned/split to real customers by this point).
            db.execute("UPDATE StockIssues SET SaleID=NULL WHERE IssueID=?", (issue_id,))

        msg = f"Reconciled — expected ₹{expected_amount:.2f}, collected ₹{cash_collected:.2f}"
        if scheme_amount:
            msg += f", ₹{scheme_amount:.2f} given as discount/free scheme (claimable from company)"
        if amount_due > 0:
            msg += f". ₹{amount_due:.2f} still due — record it later with 'Record Due Payment'."
            flash(msg, "warning")
        else:
            msg += "."
            flash(msg, "success")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))

    y, m = int(issue["IssueDate"][:4]), int(issue["IssueDate"][5:7])
    target_progress = get_employee_month_target_progress(issue["EmployeeID"], y, m)
    return render_template("stock_issue_reconcile.html", issue=issue, lines=lines, is_reedit=is_reedit,
                            money_locked=money_locked, target_progress=target_progress)


@app.route("/stock-issues/<int:issue_id>/delete", methods=["POST"])
@admin_required
def stock_issue_delete(issue_id):
    issue = db.query("SELECT * FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    # A due payment or scheme claim already recorded against this issue used to block
    # deletion outright. It no longer does (Admin-only, as this whole route already is) -
    # instead the delete performs a full reversal: due-payment history and claim status
    # are erased along with the issue itself, exactly like every other figure on it.
    was_money_locked = stock_issue_money_locked(issue)
    due_payment_count = db.query("SELECT COUNT(*) c FROM StockIssueDuePayments WHERE IssueID=?",
                                 (issue_id,), one=True)["c"]
    # Deleting every InventoryTransaction this issue posted (Issue-out, any Return-In,
    # any Free Scheme) fully reverses its stock impact, since nothing else references them.
    db.execute("DELETE FROM InventoryTransactions WHERE RefType='StockIssue' AND RefID=?", (issue_id,))
    db.execute("DELETE FROM StockIssueDuePayments WHERE IssueID=?", (issue_id,))
    db.execute("DELETE FROM StockIssueLines WHERE IssueID=?", (issue_id,))
    sale_note = ""
    if issue["SaleID"]:
        # The linked "Unassigned" Sale (if it hasn't already been reassigned away) is deleted
        # too - its own InventoryTransactions are already none (post_inventory=False when it
        # was created), so this only removes the Sale/SalesLines rows, not stock. Null out the
        # StockIssues.SaleID reference first - it has no ON DELETE CASCADE, so deleting the
        # Sale while this issue still points at it would fail the foreign key check.
        db.execute("UPDATE StockIssues SET SaleID=NULL WHERE IssueID=?", (issue_id,))
        db.execute("DELETE FROM SalesLines WHERE SaleID=?", (issue["SaleID"],))
        db.execute("DELETE FROM Sales WHERE SaleID=?", (issue["SaleID"],))
        sale_note = " and its linked Sale"
    db.execute("DELETE FROM StockIssues WHERE IssueID=?", (issue_id,))
    msg = f"Stock issue deleted{sale_note}, and its stock impact reversed."
    if was_money_locked:
        reversed_bits = []
        if due_payment_count:
            reversed_bits.append(f"{due_payment_count} due payment record(s)")
        if issue["ClaimStatus"] != "Not Claimed":
            reversed_bits.append(f"its scheme claim (was {issue['ClaimStatus']})")
        msg += f" Also reversed: {', '.join(reversed_bits)}."
    flash(msg, "success")
    return redirect(url_for("stock_issues_list"))


@app.route("/stock-issues/<int:issue_id>/collect-due", methods=["POST"])
def stock_issue_collect_due(issue_id):
    issue = db.query("SELECT * FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    f = request.form
    amount = round(float(f.get("amount") or 0), 2)
    if amount <= 0:
        flash("Enter an amount greater than zero.", "error")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))
    payment_date = f.get("payment_date") or today_str()
    notes = (f.get("notes") or "").strip()
    db.execute("""INSERT INTO StockIssueDuePayments (IssueID, PaymentDate, Amount, Notes)
                VALUES (?,?,?,?)""", (issue_id, payment_date, amount, notes))
    new_cash_collected = round((issue["CashCollected"] or 0) + amount, 2)
    new_due = round(max((issue["AmountDue"] or 0) - amount, 0), 2)
    payment_status = "Paid" if new_due <= 0 else "Partial"
    new_discrepancy = round(new_cash_collected - (issue["ExpectedAmount"] or 0), 2)
    db.execute("UPDATE StockIssues SET CashCollected=?, AmountDue=?, PaymentStatus=?, Discrepancy=? WHERE IssueID=?",
               (new_cash_collected, new_due, payment_status, new_discrepancy, issue_id))
    if issue["SaleID"]:
        # Keep the linked "Unassigned" Sale's own payment figures in step with the issue's.
        db.execute("UPDATE Sales SET AmountReceived=?, PaymentStatus=? WHERE SaleID=?",
                   (new_cash_collected, payment_status, issue["SaleID"]))
    flash(f"Recorded ₹{amount:.2f} due payment. {'Fully settled.' if new_due <= 0 else f'₹{new_due:.2f} still due.'}",
          "success")
    return redirect(url_for("stock_issue_view", issue_id=issue_id))


@app.route("/stock-issues/<int:issue_id>/claim", methods=["POST"])
def stock_issue_claim(issue_id):
    issue = db.query("SELECT * FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    f = request.form
    action = f.get("action")
    notes = (f.get("notes") or "").strip()
    if action == "claimed":
        amount = round(float(f.get("claimed_amount") or issue["SchemeAmount"] or 0), 2)
        db.execute("""UPDATE StockIssues SET ClaimStatus='Claimed', ClaimedAt=?, ClaimedAmount=?,
                    ClaimNotes=? WHERE IssueID=?""",
                   (datetime.now().isoformat(timespec="seconds"), amount, notes, issue_id))
        flash(f"Marked ₹{amount:.2f} scheme claim as submitted to the company.", "success")
    elif action == "received":
        amount = round(float(f.get("received_amount") or issue["ClaimedAmount"] or issue["SchemeAmount"] or 0), 2)
        db.execute("""UPDATE StockIssues SET ClaimStatus='Received', ReceivedAt=?, ReceivedAmount=?,
                    ClaimNotes=? WHERE IssueID=?""",
                   (datetime.now().isoformat(timespec="seconds"), amount, notes, issue_id))
        flash(f"Marked ₹{amount:.2f} scheme claim as received from the company.", "success")
    else:
        flash("Unknown claim action.", "error")
    return redirect(url_for("stock_issue_view", issue_id=issue_id))


@app.route("/reports/stock-issues")
def stock_issues_report():
    date_from = request.args.get("from") or date.today().replace(day=1).isoformat()
    date_to = request.args.get("to") or today_str()

    issues = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                       JOIN Employees e ON e.EmployeeID = si.EmployeeID
                       WHERE si.IssueDate BETWEEN ? AND ? ORDER BY si.IssueDate DESC, si.IssueID DESC""",
                       (date_from, date_to))

    totals = {
        "expected": sum(i["ExpectedAmount"] or 0 for i in issues),
        "collected": sum(i["CashCollected"] or 0 for i in issues),
        "scheme": sum(i["SchemeAmount"] or 0 for i in issues),
        "due": sum(i["AmountDue"] or 0 for i in issues),
    }
    due_issues = [i for i in issues if (i["AmountDue"] or 0) > 0]
    claim_pending = [i for i in issues if (i["SchemeAmount"] or 0) > 0 and i["ClaimStatus"] != "Received"]

    free_lines = db.query("""SELECT sil.*, pr.ProductName, si.IssueDate, e.EmployeeName FROM StockIssueLines sil
                           JOIN StockIssues si ON si.IssueID = sil.IssueID
                           JOIN Employees e ON e.EmployeeID = si.EmployeeID
                           JOIN Products pr ON pr.ProductID = sil.ProductID
                           WHERE si.IssueDate BETWEEN ? AND ?
                             AND (sil.QtyFree > 0 OR sil.DiscountAmount > 0 OR sil.SchemeClaimAmount > 0)
                           ORDER BY si.IssueDate DESC""", (date_from, date_to))

    # Day-wise Cost vs Sales - a plain reconciliation check, deliberately not
    # touching Incentive (that only belongs in the P&L margin view). Cost =
    # each issued unit's Product.CostPrice; Sales = the Stock Issue's own
    # Expected/Collected amounts for that day.
    daywise_cost_rows = db.query("""SELECT si.IssueDate AS d, COALESCE(SUM(sil.QtyIssued * pr.CostPrice), 0) AS cost
                                  FROM StockIssueLines sil
                                  JOIN StockIssues si ON si.IssueID = sil.IssueID
                                  JOIN Products pr ON pr.ProductID = sil.ProductID
                                  WHERE si.IssueDate BETWEEN ? AND ?
                                  GROUP BY si.IssueDate""", (date_from, date_to))
    daywise_sales_rows = db.query("""SELECT IssueDate AS d, COALESCE(SUM(ExpectedAmount), 0) AS expected,
                                   COALESCE(SUM(CashCollected), 0) AS collected
                                   FROM StockIssues WHERE IssueDate BETWEEN ? AND ? GROUP BY IssueDate""",
                                  (date_from, date_to))
    daywise_line_rows = db.query("""SELECT si.IssueDate AS d,
                                  COALESCE(SUM(sil.QtySold), 0) AS qty_sold,
                                  COALESCE(SUM(sil.DiscountAmount), 0) AS discount,
                                  COALESCE(SUM(sil.SchemeClaimAmount), 0) AS scheme,
                                  COALESCE(SUM(sil.QtyIssued - sil.QtySold - sil.QtyReturned - sil.QtyFree), 0) AS unaccounted
                                  FROM StockIssueLines sil
                                  JOIN StockIssues si ON si.IssueID = sil.IssueID
                                  WHERE si.IssueDate BETWEEN ? AND ?
                                  GROUP BY si.IssueDate""", (date_from, date_to))
    cost_by_day = {r["d"]: r["cost"] for r in daywise_cost_rows}
    sales_by_day = {r["d"]: {"expected": r["expected"], "collected": r["collected"]} for r in daywise_sales_rows}
    lines_by_day = {r["d"]: {"qty_sold": r["qty_sold"], "discount": r["discount"], "scheme": r["scheme"],
                              "unaccounted": r["unaccounted"]} for r in daywise_line_rows}
    all_days = sorted(set(cost_by_day) | set(sales_by_day) | set(lines_by_day), reverse=True)
    daywise = []
    for d in all_days:
        cost = round(cost_by_day.get(d, 0), 2)
        expected = round(sales_by_day.get(d, {}).get("expected", 0), 2)
        collected = round(sales_by_day.get(d, {}).get("collected", 0), 2)
        line_agg = lines_by_day.get(d, {})
        daywise.append({"date": d, "cost": cost, "expected": expected, "collected": collected,
                         "margin": round(expected - cost, 2),
                         "qty_sold": round(line_agg.get("qty_sold", 0), 2),
                         "discount": round(line_agg.get("discount", 0), 2),
                         "scheme": round(line_agg.get("scheme", 0), 2),
                         "unaccounted": round(line_agg.get("unaccounted", 0), 2)})

    return render_template("stock_issues_report.html", date_from=date_from, date_to=date_to,
                            issues=issues, totals=totals, due_issues=due_issues,
                            claim_pending=claim_pending, free_lines=free_lines, daywise=daywise)


# ---------------------------------------------------------------------
# Scheme Claims - distributor-level claims not tied to any one product or
# Stock Issue (e.g. an annual volume rebate, a company-wide promo claim
# covering multiple products/months). Separate from, and doesn't touch,
# the per-Stock-Issue discount/free-scheme claim tracking above.
# ---------------------------------------------------------------------

@app.route("/scheme-claims")
@admin_required
def scheme_claims_list():
    status_filter = request.args.get("status", "")
    sql = "SELECT * FROM SchemeClaims"
    args = ()
    if status_filter:
        sql += " WHERE Status=?"
        args = (status_filter,)
    sql += " ORDER BY ClaimDate DESC, ClaimID DESC"
    claims = db.query(sql, args)
    totals = {
        "pending": sum(c["ClaimAmount"] or 0 for c in claims if c["Status"] in ("Pending", "Claimed")),
        "received": sum((c["ReceivedAmount"] if c["ReceivedAmount"] is not None else c["ClaimAmount"]) or 0
                         for c in claims if c["Status"] in ("Received", "Completed")),
    }
    return render_template("scheme_claims_list.html", claims=claims, status_filter=status_filter, totals=totals)


@app.route("/scheme-claims/new", methods=["GET", "POST"])
@app.route("/scheme-claims/<int:claim_id>/edit", methods=["GET", "POST"])
@admin_required
def scheme_claim_form(claim_id=None):
    claim = db.query("SELECT * FROM SchemeClaims WHERE ClaimID=?", (claim_id,), one=True) if claim_id else None
    if request.method == "POST":
        f = request.form
        args = (f["scheme_name"], f["claim_date"], f.get("applicable_products", ""),
                f.get("description", ""), float(f.get("claim_amount") or 0), f.get("notes", ""))
        if claim_id:
            db.execute("""UPDATE SchemeClaims SET SchemeName=?, ClaimDate=?, ApplicableProducts=?,
                        Description=?, ClaimAmount=?, Notes=? WHERE ClaimID=?""", args + (claim_id,))
            flash("Scheme claim updated.", "success")
        else:
            new_id = db.execute("""INSERT INTO SchemeClaims (SchemeName, ClaimDate, ApplicableProducts,
                        Description, ClaimAmount, Notes, Status) VALUES (?,?,?,?,?,?,'Pending')""", args)
            flash("Scheme claim added.", "success")
        return redirect(url_for("scheme_claims_list"))
    return render_template("scheme_claim_form.html", claim=claim, today=today_str())


@app.route("/scheme-claims/<int:claim_id>/mark", methods=["POST"])
@admin_required
def scheme_claim_mark(claim_id):
    claim = db.query("SELECT * FROM SchemeClaims WHERE ClaimID=?", (claim_id,), one=True)
    if not claim:
        flash("Scheme claim not found.", "error")
        return redirect(url_for("scheme_claims_list"))
    new_status = request.form.get("status")
    if new_status not in ("Claimed", "Received", "Completed"):
        flash("Unknown status.", "error")
        return redirect(url_for("scheme_claims_list"))
    now = datetime.now().isoformat(timespec="seconds")
    if new_status == "Claimed":
        db.execute("UPDATE SchemeClaims SET Status='Claimed', ClaimedAt=? WHERE ClaimID=?", (now, claim_id))
    elif new_status == "Received":
        received_amount = float(request.form.get("received_amount") or claim["ClaimAmount"])
        db.execute("""UPDATE SchemeClaims SET Status='Received', ReceivedAt=?, ReceivedAmount=?
                    WHERE ClaimID=?""", (now, received_amount, claim_id))
    else:  # Completed
        db.execute("UPDATE SchemeClaims SET Status='Completed' WHERE ClaimID=?", (claim_id,))
    flash(f"Scheme claim marked {new_status}.", "success")
    return redirect(url_for("scheme_claims_list"))


@app.route("/scheme-claims/<int:claim_id>/delete", methods=["POST"])
@admin_required
def scheme_claim_delete(claim_id):
    db.execute("DELETE FROM SchemeClaims WHERE ClaimID=?", (claim_id,))
    flash("Scheme claim deleted.", "success")
    return redirect(url_for("scheme_claims_list"))


# ---------------------------------------------------------------------
# Excel export - "Export to Excel" on every major list page. Each spec is
# a plain SELECT with friendly column aliases (via `AS`) so the header row
# of the download matches what the column actually means; no filters are
# applied here - it's always the full list, matching what a fresh visit to
# that list page would show unfiltered.
# ---------------------------------------------------------------------

EXPORT_SPECS = {
    "products": dict(title="Products", admin_only=False, query="""
        SELECT ProductName AS "Product Name", SKU, Category, Unit, CostPrice AS "Cost Price",
               SellingPrice AS "Selling Price", SchemeName AS "Scheme Name", SchemePercent AS "Scheme %",
               IncentivePerUnit AS "Incentive/Unit",
               MinStock AS "Min Stock", MaxStock AS "Max Stock", HSNCode AS "HSN Code",
               GSTRate AS "GST Rate", CASE Active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS Status
        FROM Products ORDER BY ProductName"""),
    "suppliers": dict(title="Suppliers", admin_only=True, query="""
        SELECT SupplierName AS "Supplier Name", ContactPerson AS "Contact Person", Phone, Email, Address, GSTIN,
               CASE Active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS Status
        FROM Suppliers ORDER BY SupplierName"""),
    "customers": dict(title="Customers", admin_only=False, query="""
        SELECT CustomerName AS "Customer Name", ContactPerson AS "Contact Person", Phone, Email, Address, GSTIN,
               State, StateCode AS "State Code", CreditLimit AS "Credit Limit", CreditDays AS "Credit Days",
               CASE Active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS Status
        FROM Customers WHERE IsUnassignedBucket=0 ORDER BY CustomerName"""),
    "purchases": dict(title="Purchases", admin_only=True, query="""
        SELECT p.PurchaseDate AS "Purchase Date", s.SupplierName AS Supplier, p.PONumber AS "PO Number",
               p.InvoiceNumber AS "Invoice Number", p.Status, p.PaymentStatus AS "Payment Status",
               p.TaxableAmount AS "Taxable Amount", p.TotalAmount AS "Total Amount"
        FROM Purchases p JOIN Suppliers s ON s.SupplierID=p.SupplierID
        ORDER BY p.PurchaseDate DESC, p.PurchaseID DESC"""),
    "sales": dict(title="Sales", admin_only=False, query="""
        SELECT s.SaleDate AS "Sale Date", c.CustomerName AS Customer, s.InvoiceNumber AS "Invoice Number",
               s.Status, s.PaymentStatus AS "Payment Status", s.TaxableAmount AS "Taxable Amount",
               s.TotalAmount AS "Total Amount", s.AmountReceived AS "Amount Received"
        FROM Sales s JOIN Customers c ON c.CustomerID=s.CustomerID
        ORDER BY s.SaleDate DESC, s.SaleID DESC"""),
    "stock_issues": dict(title="Stock Issues", admin_only=False, query="""
        SELECT si.IssueDate AS "Issue Date", e.EmployeeName AS Salesperson, si.Status,
               si.ExpectedAmount AS "Expected Amount", si.CashCollected AS "Cash Collected",
               si.SchemeAmount AS "Scheme Amount", si.AmountDue AS "Amount Due",
               si.PaymentStatus AS "Payment Status", si.ClaimStatus AS "Claim Status"
        FROM StockIssues si JOIN Employees e ON e.EmployeeID=si.EmployeeID
        ORDER BY si.IssueDate DESC, si.IssueID DESC"""),
    "expenses": dict(title="Expenses", admin_only=False, query="""
        SELECT e.ExpenseDate AS "Expense Date", e.Category, v.RegistrationNumber AS Vehicle, e.PaidTo AS "Paid To",
               e.PaymentMode AS "Payment Mode", e.Amount, e.Description
        FROM Expenses e LEFT JOIN Vehicles v ON v.VehicleID=e.VehicleID
        ORDER BY e.ExpenseDate DESC, e.ExpenseID DESC"""),
    "vehicles": dict(title="Vehicles", admin_only=False, query="""
        SELECT RegistrationNumber AS "Registration Number", VehicleType AS "Vehicle Type", Make, Model,
               CurrentOdometer AS "Current Odometer", InsuranceExpiry AS "Insurance Expiry",
               PermitExpiry AS "Permit Expiry", PUCExpiry AS "PUC Expiry", FitnessExpiry AS "Fitness Expiry", Status
        FROM Vehicles ORDER BY RegistrationNumber"""),
    "maintenance": dict(title="Maintenance", admin_only=False, query="""
        SELECT v.RegistrationNumber AS Vehicle, vm.ServiceType AS "Service Type", vm.ServiceDate AS "Service Date",
               vm.Odometer, vm.NextDueDate AS "Next Due Date", vm.Cost, vm.ServiceCenter AS "Service Center", vm.Status
        FROM VehicleMaintenance vm JOIN Vehicles v ON v.VehicleID=vm.VehicleID
        ORDER BY vm.ServiceDate DESC, vm.MaintenanceID DESC"""),
    "salary": dict(title="Salary Schedule", admin_only=True, query="""
        SELECT e.EmployeeName AS Employee, sp.SalaryYear AS Year, sp.SalaryMonth AS Month,
               sp.GrossSalary AS "Gross Salary", sp.LOPDays AS "LOP Days", sp.BasicAmount AS Basic,
               sp.AdvanceDeducted AS "Advance Deducted", sp.NetPayable AS "Net Payable", sp.Status,
               sp.PaymentDate AS "Payment Date"
        FROM SalaryPayments sp JOIN Employees e ON e.EmployeeID=sp.EmployeeID
        ORDER BY sp.SalaryYear DESC, sp.SalaryMonth DESC, e.EmployeeName"""),
    "employees": dict(title="Employees", admin_only=True, query="""
        SELECT EmployeeName AS "Employee Name", Designation, Phone, JoinDate AS "Join Date",
               MonthlySalary AS "Monthly Salary", Status
        FROM Employees ORDER BY EmployeeName"""),
    "advances": dict(title="Advance Payments", admin_only=True, query="""
        SELECT a.AdvanceDate AS "Advance Date", e.EmployeeName AS Employee, a.Amount, a.Reason,
               a.RepaymentMonths AS "Repayment Months", a.MonthlyDeduction AS "Monthly Deduction",
               a.BalanceRemaining AS "Balance Remaining", a.Status
        FROM AdvancePayments a JOIN Employees e ON e.EmployeeID=a.EmployeeID
        ORDER BY a.AdvanceDate DESC"""),
    "scheme_claims": dict(title="Scheme Claims", admin_only=True, query="""
        SELECT SchemeName AS "Scheme Name", ClaimDate AS "Claim Date", ApplicableProducts AS "Applicable Products",
               Description, ClaimAmount AS "Claim Amount", Status, ClaimedAt AS "Claimed At",
               ReceivedAt AS "Received At", ReceivedAmount AS "Received Amount", Notes
        FROM SchemeClaims ORDER BY ClaimDate DESC, ClaimID DESC"""),
}


def rows_to_xlsx(rows, sheet_title="Report"):
    import openpyxl
    import io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (sheet_title or "Report")[:31]
    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for r in rows:
            ws.append([r[h] for h in headers])
        for i in range(1, len(headers) + 1):
            letter = openpyxl.utils.get_column_letter(i)
            ws.column_dimensions[letter].width = 18
    else:
        ws.append(["No data for this report"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def rows_to_pdf(rows, title="Report"):
    """Same full-table export as rows_to_xlsx(), formatted as a printable
    PDF (reportlab, already used by invoice_pdf.py - no new dependency).
    Landscape A4 since most of these tables are wide."""
    import io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("t", parent=styles["Normal"], fontSize=14, alignment=TA_CENTER, fontName="Helvetica-Bold")
    cell_style = ParagraphStyle("c", parent=styles["Normal"], fontSize=7.5, leading=9.5)
    header_style = ParagraphStyle("h", parent=styles["Normal"], fontSize=7.5, leading=9.5,
                                   textColor=colors.white, fontName="Helvetica-Bold")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=12 * mm, bottomMargin=12 * mm,
                             leftMargin=10 * mm, rightMargin=10 * mm)
    story = [Paragraph(title, title_style), Spacer(1, 6),
              Paragraph(f"Generated {today_str()}", styles["Normal"]), Spacer(1, 10)]

    if rows:
        headers = list(rows[0].keys())
        data = [[Paragraph(str(h), header_style) for h in headers]]
        for r in rows:
            data.append([Paragraph("" if r[h] is None else str(r[h]), cell_style) for h in headers])
        page_width = landscape(A4)[0] - 20 * mm
        col_width = page_width / len(headers)
        table = Table(data, colWidths=[col_width] * len(headers), repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("No data for this report.", styles["Normal"]))

    doc.build(story)
    buf.seek(0)
    return buf


def _check_export_access(spec):
    """Returns a redirect Response if this export is blocked for the
    current user, else None."""
    if spec["admin_only"]:
        current = get_current_user()
        if not current or current["Role"] != "Admin":
            flash("Only Admin accounts can export this report.", "error")
            return redirect(url_for("dashboard"))
    return None


@app.route("/export/<module>")
def export_module(module):
    spec = EXPORT_SPECS.get(module)
    if not spec:
        flash("Unknown report.", "error")
        return redirect(url_for("dashboard"))
    blocked = _check_export_access(spec)
    if blocked:
        return blocked
    rows = db.query(spec["query"])
    buf = rows_to_xlsx(rows, spec["title"])
    from flask import send_file
    filename = f"{spec['title'].replace(' ', '_')}_{today_str()}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/export/<module>/pdf")
def export_module_pdf(module):
    spec = EXPORT_SPECS.get(module)
    if not spec:
        flash("Unknown report.", "error")
        return redirect(url_for("dashboard"))
    blocked = _check_export_access(spec)
    if blocked:
        return blocked
    rows = db.query(spec["query"])
    buf = rows_to_pdf(rows, spec["title"])
    from flask import send_file
    filename = f"{spec['title'].replace(' ', '_')}_{today_str()}.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


# ---------------------------------------------------------------------
# Targets - sales targets to drive the distributor's business, set per
# Employee (required) x Product (optional - NULL applies across all
# products for that employee) x calendar Month. Actuals are always
# computed live from Stock Issues / StockIssueLines, never stored, so a
# target never goes stale relative to reconciliation edits. Week/day/
# MTD/WTD views are all derived from the month-level target by pro-
# rating across the days in that month.
# ---------------------------------------------------------------------

def target_month_bounds(year, month):
    days_in_month = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, days_in_month)
    return start, end, days_in_month


def target_days_elapsed(year, month):
    """How many days of the given month count as 'to date' for MTD/day-wise
    trend purposes: the full month if it's entirely in the past, 0 if it's
    entirely in the future, otherwise today's day-of-month."""
    start, end, days_in_month = target_month_bounds(year, month)
    today = date.today()
    if today < start:
        return 0
    if today > end:
        return days_in_month
    return today.day


def compute_target_actuals(employee_id, product_id, date_from, date_to):
    """Actuals for one Employee (+ optional Product) over a date range, all
    computed live from Stock Issues so a target is never stale. For a
    whole-employee target (product_id is None), Expected/Collected come
    straight from the Stock Issue header. For a product-level target,
    Expected is summed at line level (always exact); Collected is
    allocated proportionally per issue (each issue's actual cash split
    across its lines by each line's share of that issue's line-level
    expected amount) since CashCollected is only recorded per issue, not
    per product line."""
    if product_id:
        line_rows = db.query("""SELECT sil.QtySold, sil.UnitPrice, sil.DiscountAmount, sil.IssueID,
                              si.CashCollected, si.ExpectedAmount
                              FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID
                              WHERE si.EmployeeID=? AND sil.ProductID=? AND si.IssueDate BETWEEN ? AND ?""",
                              (employee_id, product_id, date_from, date_to))
        qty_sold = sum(r["QtySold"] or 0 for r in line_rows)
        discount = sum(r["DiscountAmount"] or 0 for r in line_rows)
        qty_no_discount = sum((r["QtySold"] or 0) for r in line_rows if not (r["DiscountAmount"] or 0))
        line_expected_by_issue = {}
        cash_by_issue = {}
        for r in line_rows:
            le = (r["QtySold"] or 0) * (r["UnitPrice"] or 0) - (r["DiscountAmount"] or 0)
            line_expected_by_issue[r["IssueID"]] = line_expected_by_issue.get(r["IssueID"], 0) + le
            cash_by_issue[r["IssueID"]] = (r["CashCollected"] or 0, r["ExpectedAmount"] or 0)
        expected = sum(line_expected_by_issue.values())
        collected = 0.0
        for iid, line_exp in line_expected_by_issue.items():
            cash, issue_exp = cash_by_issue[iid]
            collected += cash * (line_exp / issue_exp) if issue_exp else 0
    else:
        issue_rows = db.query("""SELECT ExpectedAmount, CashCollected FROM StockIssues
                               WHERE EmployeeID=? AND IssueDate BETWEEN ? AND ?""", (employee_id, date_from, date_to))
        expected = sum(r["ExpectedAmount"] or 0 for r in issue_rows)
        collected = sum(r["CashCollected"] or 0 for r in issue_rows)
        line_rows = db.query("""SELECT sil.QtySold, sil.DiscountAmount FROM StockIssueLines sil
                              JOIN StockIssues si ON si.IssueID = sil.IssueID
                              WHERE si.EmployeeID=? AND si.IssueDate BETWEEN ? AND ?""", (employee_id, date_from, date_to))
        qty_sold = sum(r["QtySold"] or 0 for r in line_rows)
        discount = sum(r["DiscountAmount"] or 0 for r in line_rows)
        qty_no_discount = sum((r["QtySold"] or 0) for r in line_rows if not (r["DiscountAmount"] or 0))
    return {"qty_sold": round(qty_sold, 2), "expected": round(expected, 2), "collected": round(collected, 2),
            "discount": round(discount, 2), "qty_no_discount": round(qty_no_discount, 2)}


def pct(actual, target):
    if not target:
        return None
    return round(actual / target * 100, 1)


def get_target_buckets(target_id):
    """A Target's over-achievement incentive buckets, sorted highest threshold first -
    the order compute_incentive() needs to find the first (highest) threshold met."""
    return db.query("""SELECT * FROM TargetIncentiveBuckets WHERE TargetID=?
                     ORDER BY AchievementPct DESC""", (target_id,))


def compute_incentive(base_amount, buckets, achievement_pct):
    """Base Incentive Rs x the multiplier of the highest achievement-%% bucket threshold
    that's been met. Reaching no bucket at all (achievement below every threshold, or no
    buckets configured) means no incentive: multiplier 0, amount 0. Returns
    (multiplier_or_None, incentive_amount)."""
    if not buckets or achievement_pct is None:
        return None, 0.0
    for b in buckets:
        if achievement_pct >= b["AchievementPct"]:
            return b["Multiplier"], round(base_amount * b["Multiplier"], 2)
    return None, 0.0


def build_target_view(t, days_elapsed, days_in_month):
    """Attach full-month and MTD actuals + progress percentages to a Target row (as dict)."""
    start, end, _ = target_month_bounds(t["TargetYear"], t["TargetMonth"])
    mtd_end = start + timedelta(days=max(days_elapsed, 1) - 1) if days_elapsed else start
    full = compute_target_actuals(t["EmployeeID"], t["ProductID"], start.isoformat(), end.isoformat())
    mtd = compute_target_actuals(t["EmployeeID"], t["ProductID"], start.isoformat(), mtd_end.isoformat()) if days_elapsed else \
        {"qty_sold": 0, "expected": 0, "collected": 0, "discount": 0, "qty_no_discount": 0}
    prorated_qty = round(t["QtySoldTarget"] * days_elapsed / days_in_month, 2) if days_in_month else 0
    prorated_value = round(t["SalesValueTarget"] * days_elapsed / days_in_month, 2) if days_in_month else 0
    rate_cap_effective_full = round(t["DiscountCapRatePerUnit"] * full["qty_sold"], 2)
    rate_cap_effective_mtd = round(t["DiscountCapRatePerUnit"] * mtd["qty_sold"], 2)
    view = dict(t)
    view["full"] = full
    view["mtd"] = mtd
    view["prorated_qty"] = prorated_qty
    view["prorated_value"] = prorated_value
    view["qty_pct_full"] = pct(full["qty_sold"], t["QtySoldTarget"])
    view["qty_pct_mtd"] = pct(mtd["qty_sold"], prorated_qty)
    view["value_pct_full_expected"] = pct(full["expected"], t["SalesValueTarget"])
    view["value_pct_full_collected"] = pct(full["collected"], t["SalesValueTarget"])
    view["value_pct_mtd_expected"] = pct(mtd["expected"], prorated_value)
    view["discount_fixed_pct_full"] = pct(full["discount"], t["DiscountCapAmount"])
    view["discount_fixed_pct_mtd"] = pct(mtd["discount"], t["DiscountCapAmount"])
    view["discount_rate_cap_full"] = rate_cap_effective_full
    view["discount_rate_cap_mtd"] = rate_cap_effective_mtd
    view["discount_rate_pct_full"] = pct(full["discount"], rate_cap_effective_full)
    view["discount_rate_pct_mtd"] = pct(mtd["discount"], rate_cap_effective_mtd)
    view["no_discount_pct_full"] = round(full["qty_no_discount"] / full["qty_sold"] * 100, 1) if full["qty_sold"] else None
    buckets = get_target_buckets(t["TargetID"])
    view["buckets"] = buckets
    view["incentive_multiplier_full"], view["incentive_amount_full"] = compute_incentive(
        t["BaseIncentiveAmount"], buckets, view["qty_pct_full"])
    view["incentive_multiplier_mtd"], view["incentive_amount_mtd"] = compute_incentive(
        t["BaseIncentiveAmount"], buckets, view["qty_pct_mtd"])
    return view


@app.route("/targets")
def targets_view():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    selected_employee_id = request.args.get("employee_id", type=int)
    start, end, days_in_month = target_month_bounds(year, month)
    days_elapsed = target_days_elapsed(year, month)
    is_current_month = (year, month) == (today.year, today.month)

    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    products = db.query("SELECT * FROM Products WHERE Active=1 ORDER BY ProductName")
    is_admin = bool(get_current_user() and get_current_user()["Role"] == "Admin")

    q = """SELECT t.*, e.EmployeeName, p.ProductName FROM Targets t
           JOIN Employees e ON e.EmployeeID = t.EmployeeID
           LEFT JOIN Products p ON p.ProductID = t.ProductID
           WHERE t.TargetYear=? AND t.TargetMonth=?"""
    params = [year, month]
    if selected_employee_id:
        q += " AND t.EmployeeID=?"
        params.append(selected_employee_id)
    q += " ORDER BY e.EmployeeName, p.ProductName IS NULL DESC, p.ProductName"
    targets_raw = db.query(q, tuple(params))
    targets = [build_target_view(t, days_elapsed, days_in_month) for t in targets_raw]

    # Whole-company (or whole-employee, if one is selected) summary: only the
    # whole-employee (ProductID IS NULL) targets are added up, so a
    # product-level target under the same employee doesn't get double-counted.
    company_targets = [t for t in targets if t["ProductID"] is None]
    summary = {
        "qty_target": sum(t["QtySoldTarget"] for t in company_targets),
        "qty_actual_mtd": sum(t["mtd"]["qty_sold"] for t in company_targets),
        "qty_prorated": sum(t["prorated_qty"] for t in company_targets),
        "value_target": sum(t["SalesValueTarget"] for t in company_targets),
        "expected_mtd": sum(t["mtd"]["expected"] for t in company_targets),
        "collected_mtd": sum(t["mtd"]["collected"] for t in company_targets),
        "value_prorated": sum(t["prorated_value"] for t in company_targets),
        "discount_cap": sum(t["DiscountCapAmount"] for t in company_targets),
        "discount_mtd": sum(t["mtd"]["discount"] for t in company_targets),
        "incentive_mtd": sum(t["incentive_amount_mtd"] for t in company_targets),
        "incentive_full": sum(t["incentive_amount_full"] for t in company_targets),
        "employee_count": len(company_targets),
    }
    summary["qty_pct_mtd"] = pct(summary["qty_actual_mtd"], summary["qty_prorated"])
    summary["value_pct_mtd"] = pct(summary["expected_mtd"], summary["value_prorated"])
    summary["discount_pct_mtd"] = pct(summary["discount_mtd"], summary["discount_cap"])

    # Day-wise trend (for the selected employee if one is picked, else all
    # employees with a whole-employee target combined) across all days up
    # to days_elapsed, cumulative actual qty sold vs cumulative pro-rated
    # target qty - the core "are we on pace" chart, rendered as a simple
    # proportional bar per day (no charting library in this app).
    daywise = []
    if days_elapsed and company_targets:
        emp_ids = [selected_employee_id] if selected_employee_id else [t["EmployeeID"] for t in company_targets]
        daily_qty_target = sum(t["QtySoldTarget"] for t in company_targets) / days_in_month if days_in_month else 0
        day_rows = db.query(f"""SELECT si.IssueDate AS d, COALESCE(SUM(sil.QtySold), 0) AS qty
                             FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID
                             WHERE si.EmployeeID IN ({','.join('?' * len(emp_ids))})
                               AND si.IssueDate BETWEEN ? AND ?
                             GROUP BY si.IssueDate""",
                             tuple(emp_ids) + (start.isoformat(), (start + timedelta(days=days_elapsed - 1)).isoformat()))
        qty_by_day = {r["d"]: r["qty"] for r in day_rows}
        cum_actual = 0.0
        for i in range(days_elapsed):
            d = start + timedelta(days=i)
            cum_actual += qty_by_day.get(d.isoformat(), 0)
            cum_target = round(daily_qty_target * (i + 1), 2)
            daywise.append({"date": d.isoformat(), "qty": round(qty_by_day.get(d.isoformat(), 0), 2),
                             "cum_actual": round(cum_actual, 2), "cum_target": cum_target,
                             "pct": pct(cum_actual, cum_target)})

    # Week-to-date: the current ISO week (Mon-today), clipped to the month
    # and only meaningful when the selected month is the current month.
    wtd = None
    if is_current_month and company_targets:
        emp_ids = [selected_employee_id] if selected_employee_id else [t["EmployeeID"] for t in company_targets]
        week_start = max(today - timedelta(days=today.weekday()), start)
        days_in_week_so_far = (today - week_start).days + 1
        daily_qty_target = sum(t["QtySoldTarget"] for t in company_targets) / days_in_month if days_in_month else 0
        wtd_target_qty = round(daily_qty_target * days_in_week_so_far, 2)
        wtd_rows = db.query(f"""SELECT COALESCE(SUM(sil.QtySold), 0) qty, COALESCE(SUM(sil.DiscountAmount), 0) discount
                             FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID
                             WHERE si.EmployeeID IN ({','.join('?' * len(emp_ids))})
                               AND si.IssueDate BETWEEN ? AND ?""",
                             tuple(emp_ids) + (week_start.isoformat(), today.isoformat()), one=True)
        wtd = {"week_start": week_start.isoformat(), "qty_actual": round(wtd_rows["qty"], 2),
               "qty_target": wtd_target_qty, "discount": round(wtd_rows["discount"], 2),
               "pct": pct(wtd_rows["qty"], wtd_target_qty)}

    edit_target = None
    edit_target_buckets = []
    edit_id = request.args.get("edit", type=int)
    if edit_id:
        edit_target = db.query("SELECT * FROM Targets WHERE TargetID=?", (edit_id,), one=True)
        if edit_target:
            edit_target_buckets = get_target_buckets(edit_id)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("targets.html", year=year, month=month, month_name=MONTH_NAMES[month],
                            prev_month=prev_month, prev_year=prev_year, next_month=next_month, next_year=next_year,
                            employees=employees, products=products, selected_employee_id=selected_employee_id,
                            targets=targets, summary=summary, daywise=daywise, wtd=wtd,
                            days_in_month=days_in_month, days_elapsed=days_elapsed, is_current_month=is_current_month,
                            is_admin=is_admin, edit_target=edit_target, edit_target_buckets=edit_target_buckets,
                            today=today_str())


@app.route("/targets/save", methods=["POST"])
def target_save():
    current = get_current_user()
    if not current or current["Role"] != "Admin":
        flash("Only an Admin can set targets.", "error")
        return redirect(url_for("targets_view"))
    f = request.form
    target_id = f.get("target_id", type=int)
    employee_id = f.get("employee_id", type=int)
    product_id = f.get("product_id", type=int) or None
    year = f.get("year", type=int)
    month = f.get("month", type=int)
    if not employee_id or not year or not month:
        flash("Employee, year and month are required.", "error")
        return redirect(url_for("targets_view", year=year or today_str()[:4], month=month or 1))

    qty_target = float(f.get("qty_target") or 0)
    value_target = float(f.get("value_target") or 0)
    discount_cap = float(f.get("discount_cap") or 0)
    discount_rate = float(f.get("discount_rate") or 0)
    base_incentive = float(f.get("base_incentive") or 0)
    notes = (f.get("notes") or "").strip()
    now = datetime.now().isoformat(timespec="seconds")

    # Achievement %% / Multiplier bucket rows - parallel arrays from the dynamic bucket-row
    # form fields; blank trailing rows (from "Add Bucket" then not filling it in) are skipped.
    bucket_pcts = request.form.getlist("bucket_pct[]")
    bucket_mults = request.form.getlist("bucket_mult[]")
    buckets = [(float(p), float(m)) for p, m in zip(bucket_pcts, bucket_mults) if p != "" and m != ""]

    existing = db.query("""SELECT TargetID FROM Targets WHERE EmployeeID=? AND ProductID IS ?
                         AND TargetYear=? AND TargetMonth=?""", (employee_id, product_id, year, month), one=True)
    if existing and (not target_id or existing["TargetID"] == target_id):
        target_id = existing["TargetID"]
        db.execute("""UPDATE Targets SET QtySoldTarget=?, SalesValueTarget=?, DiscountCapAmount=?,
                    DiscountCapRatePerUnit=?, BaseIncentiveAmount=?, Notes=?, UpdatedAt=? WHERE TargetID=?""",
                   (qty_target, value_target, discount_cap, discount_rate, base_incentive, notes, now, target_id))
        flash("Target updated.", "success")
    elif target_id:
        db.execute("""UPDATE Targets SET EmployeeID=?, ProductID=?, TargetYear=?, TargetMonth=?, QtySoldTarget=?,
                    SalesValueTarget=?, DiscountCapAmount=?, DiscountCapRatePerUnit=?, BaseIncentiveAmount=?,
                    Notes=?, UpdatedAt=? WHERE TargetID=?""",
                   (employee_id, product_id, year, month, qty_target, value_target, discount_cap, discount_rate,
                    base_incentive, notes, now, target_id))
        flash("Target updated.", "success")
    else:
        target_id = db.execute("""INSERT INTO Targets (EmployeeID, ProductID, TargetYear, TargetMonth, QtySoldTarget,
                    SalesValueTarget, DiscountCapAmount, DiscountCapRatePerUnit, BaseIncentiveAmount, Notes, UpdatedAt)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (employee_id, product_id, year, month, qty_target, value_target, discount_cap, discount_rate,
                    base_incentive, notes, now))
        flash("Target created.", "success")

    # Buckets are fully replaced on every save - simplest way to keep the editable table in
    # the form (add/remove rows freely) in sync with what's stored, without diffing rows.
    db.execute("DELETE FROM TargetIncentiveBuckets WHERE TargetID=?", (target_id,))
    for achievement_pct, multiplier in buckets:
        db.execute("""INSERT INTO TargetIncentiveBuckets (TargetID, AchievementPct, Multiplier)
                    VALUES (?,?,?)""", (target_id, achievement_pct, multiplier))

    return redirect(url_for("targets_view", year=year, month=month))


@app.route("/targets/<int:target_id>/delete", methods=["POST"])
def target_delete(target_id):
    current = get_current_user()
    if not current or current["Role"] != "Admin":
        flash("Only an Admin can delete targets.", "error")
        return redirect(url_for("targets_view"))
    t = db.query("SELECT * FROM Targets WHERE TargetID=?", (target_id,), one=True)
    if not t:
        flash("Target not found.", "error")
        return redirect(url_for("targets_view"))
    db.execute("DELETE FROM Targets WHERE TargetID=?", (target_id,))
    flash("Target deleted.", "success")
    return redirect(url_for("targets_view", year=t["TargetYear"], month=t["TargetMonth"]))


def get_company_target_summary(year=None, month=None):
    """Whole-employee (all-products) targets combined across every
    salesperson that has one set for the given month (defaults to the
    current month) - used by both the Dashboard and Reports hub cards.
    Returns None if no such target exists yet for that month."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    days_elapsed = target_days_elapsed(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    rows = db.query("""SELECT * FROM Targets WHERE ProductID IS NULL
                     AND TargetYear=? AND TargetMonth=?""", (year, month))
    if not rows:
        return None
    views = [build_target_view(t, days_elapsed, days_in_month) for t in rows]
    qty_target = sum(t["QtySoldTarget"] for t in views)
    qty_prorated = sum(t["prorated_qty"] for t in views)
    qty_actual = sum(t["mtd"]["qty_sold"] for t in views)
    value_target = sum(t["SalesValueTarget"] for t in views)
    value_prorated = sum(t["prorated_value"] for t in views)
    expected_actual = sum(t["mtd"]["expected"] for t in views)
    discount_cap = sum(t["DiscountCapAmount"] for t in views)
    discount_actual = sum(t["mtd"]["discount"] for t in views)
    incentive_mtd = sum(t["incentive_amount_mtd"] for t in views)
    return {
        "employee_count": len(views), "qty_target": qty_target, "qty_prorated": qty_prorated,
        "qty_actual": qty_actual, "qty_pct": pct(qty_actual, qty_prorated),
        "value_target": value_target, "value_prorated": value_prorated, "expected_actual": expected_actual,
        "value_pct": pct(expected_actual, value_prorated),
        "discount_cap": discount_cap, "discount_actual": discount_actual,
        "discount_pct": pct(discount_actual, discount_cap),
        "incentive_mtd": incentive_mtd,
    }


def get_employee_month_target_progress(employee_id, year=None, month=None):
    """Convenience for other tabs (Dashboard, Stock Issues list/view, Reconcile)
    to show a lightweight Target-vs-Actual badge for one employee's whole-
    employee (all-products) target for a given month, without duplicating
    the fuller Targets-tab logic. Returns None if no such target is set."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    t = db.query("""SELECT * FROM Targets WHERE EmployeeID=? AND ProductID IS NULL
                  AND TargetYear=? AND TargetMonth=?""", (employee_id, year, month), one=True)
    if not t:
        return None
    days_elapsed = target_days_elapsed(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    return build_target_view(t, days_elapsed, days_in_month)


# ---------------------------------------------------------------------
# Reports hub - a Dashboard-linked landing page giving a quick numeric
# breakdown/analysis per tab (Sales, Purchases, Stock, Expenses, Stock
# Issues, Employees/Salary, Scheme Claims, Customers, Fleet), each with
# Export to Excel/PDF for that tab's full data right there.
# ---------------------------------------------------------------------

@app.route("/reports")
def reports_hub():
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    is_admin = bool(get_current_user() and get_current_user()["Role"] == "Admin")

    sales_month = db.query("SELECT COALESCE(SUM(TotalAmount),0) t, COUNT(*) n FROM Sales WHERE SaleDate>=? AND Status<>'Cancelled'",
                            (month_start,), one=True)
    top_customers = db.query("""SELECT c.CustomerName, SUM(s.TotalAmount) total FROM Sales s
                              JOIN Customers c ON c.CustomerID=s.CustomerID
                              WHERE s.Status<>'Cancelled' AND c.IsUnassignedBucket=0
                              GROUP BY s.CustomerID ORDER BY total DESC LIMIT 5""")
    sales_trend = db.query("""SELECT strftime('%Y-%m', SaleDate) ym, SUM(TotalAmount) total FROM Sales
                            WHERE Status<>'Cancelled' GROUP BY ym ORDER BY ym DESC LIMIT 6""")

    purchases_month = db.query("SELECT COALESCE(SUM(TotalAmount),0) t, COUNT(*) n FROM Purchases WHERE PurchaseDate>=? AND Status<>'Cancelled'",
                                (month_start,), one=True)
    top_suppliers = db.query("""SELECT s.SupplierName, SUM(p.TotalAmount) total FROM Purchases p
                              JOIN Suppliers s ON s.SupplierID=p.SupplierID
                              WHERE p.Status<>'Cancelled' GROUP BY p.SupplierID
                              ORDER BY total DESC LIMIT 5""") if is_admin else []

    stock_summary = db.query("""SELECT COUNT(*) n, COALESCE(SUM(COALESCE(t.CurrentStock,0) * p.CostPrice),0) value
                              FROM Products p LEFT JOIN (
                                SELECT ProductID, SUM(QtyChange) CurrentStock FROM InventoryTransactions GROUP BY ProductID
                              ) t ON t.ProductID=p.ProductID WHERE p.Active=1""", one=True)
    low_stock_count = len(low_stock_products())
    over_stock_count = len(over_stock_products())
    scheme_product_count = db.query("SELECT COUNT(*) n FROM Products WHERE SchemePercent > 0 AND Active=1", one=True)["n"]
    # Category-wise total Qty Stock, alongside the overall total, for the Products & Stock card.
    category_stock = db.query("""SELECT COALESCE(p.Category, 'Uncategorized') AS category,
                               COALESCE(SUM(COALESCE(t.CurrentStock, 0)), 0) AS qty
                               FROM Products p LEFT JOIN (
                                 SELECT ProductID, SUM(QtyChange) CurrentStock FROM InventoryTransactions GROUP BY ProductID
                               ) t ON t.ProductID = p.ProductID
                               WHERE p.Active=1 GROUP BY COALESCE(p.Category, 'Uncategorized') ORDER BY qty DESC""")
    total_qty_stock = sum(r["qty"] for r in category_stock)

    expenses_month_total = db.query("SELECT COALESCE(SUM(Amount),0) t FROM Expenses WHERE ExpenseDate>=?", (month_start,), one=True)["t"]
    expenses_by_category = db.query("""SELECT Category, SUM(Amount) total FROM Expenses WHERE ExpenseDate>=?
                                     GROUP BY Category ORDER BY total DESC""", (month_start,))

    stock_issues_month = db.query("""SELECT COALESCE(SUM(ExpectedAmount),0) expected, COALESCE(SUM(CashCollected),0) collected,
                                   COALESCE(SUM(SchemeAmount),0) scheme, COALESCE(SUM(AmountDue),0) due
                                   FROM StockIssues WHERE IssueDate>=?""", (month_start,), one=True)

    salary_summary = None
    scheme_claims_summary = None
    if is_admin:
        salary_summary = db.query("""SELECT COALESCE(SUM(NetPayable),0) total,
                                   SUM(CASE WHEN Status='Pending' THEN 1 ELSE 0 END) pending,
                                   SUM(CASE WHEN Status='Paid' THEN 1 ELSE 0 END) paid
                                   FROM SalaryPayments WHERE SalaryYear=? AND SalaryMonth=?""",
                                   (today.year, today.month), one=True)
        scheme_claims_summary = db.query("""SELECT
                                          COALESCE(SUM(CASE WHEN Status IN ('Pending','Claimed') THEN ClaimAmount ELSE 0 END),0) pending,
                                          COALESCE(SUM(CASE WHEN Status IN ('Received','Completed')
                                                             THEN COALESCE(ReceivedAmount, ClaimAmount) ELSE 0 END),0) received
                                          FROM SchemeClaims""", one=True)

    fleet_due = len(upcoming_maintenance()) + len(expiring_documents())
    active_customers = db.query("SELECT COUNT(*) n FROM Customers WHERE Active=1 AND IsUnassignedBucket=0", one=True)["n"]
    target_summary = get_company_target_summary(today.year, today.month)

    return render_template("reports_hub.html", is_admin=is_admin, target_summary=target_summary,
                            sales_month=sales_month, top_customers=top_customers, sales_trend=sales_trend,
                            purchases_month=purchases_month, top_suppliers=top_suppliers,
                            stock_summary=stock_summary, low_stock_count=low_stock_count,
                            over_stock_count=over_stock_count, scheme_product_count=scheme_product_count,
                            category_stock=category_stock, total_qty_stock=total_qty_stock,
                            expenses_month_total=expenses_month_total, expenses_by_category=expenses_by_category,
                            stock_issues_month=stock_issues_month, salary_summary=salary_summary,
                            scheme_claims_summary=scheme_claims_summary, fleet_due=fleet_due,
                            active_customers=active_customers, month_name=MONTH_NAMES[today.month], year=today.year)


# ---------------------------------------------------------------------
# Company Settings (used on GST invoices)
# ---------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
@admin_required
def settings_form():
    if request.method == "POST":
        f = request.form
        db.execute("""UPDATE CompanySettings SET CompanyName=?, GSTIN=?, PAN=?, Address=?, City=?,
                    State=?, StateCode=?, Pincode=?, Phone=?, Email=?, BankName=?, BankAccountName=?,
                    BankAccountNumber=?, BankIFSC=?, BankBranch=?, InvoicePrefix=?, InvoiceTerms=?,
                    GstFilingScheme=?, GstRemindersEnabled=?, GstReminderEmails=?, GstReminderDaysBefore=?,
                    SmtpHost=?, SmtpPort=?, SmtpUsername=?, SmtpPassword=?, SmtpFromEmail=?, SmtpUseTLS=?
                    WHERE SettingsID=1""",
                   (f["company_name"], f["gstin"], f["pan"], f["address"], f["city"],
                    STATE_NAME_BY_CODE.get(f.get("state_code", ""), ""), f.get("state_code", ""), f["pincode"],
                    f["phone"], f["email"], f["bank_name"], f["bank_account_name"], f["bank_account_number"],
                    f["bank_ifsc"], f["bank_branch"], f["invoice_prefix"] or "INV", f["invoice_terms"],
                    f.get("gst_filing_scheme") if f.get("gst_filing_scheme") in ("Monthly", "QRMP") else "Monthly",
                    1 if f.get("gst_reminders_enabled") else 0, f.get("gst_reminder_emails", "").strip(),
                    int(f.get("gst_reminder_days_before") or 3),
                    f.get("smtp_host", "").strip(), int(f.get("smtp_port") or 587),
                    f.get("smtp_username", "").strip(), f.get("smtp_password", ""),
                    f.get("smtp_from_email", "").strip(), 1 if f.get("smtp_use_tls") else 0))
        flash("Company settings saved. These details now appear on every GST invoice.", "success")
        return redirect(url_for("settings_form"))
    company = get_company_settings()
    return render_template("settings_form.html", company=company, states=INDIAN_STATES)


@app.route("/settings/gst-test-email", methods=["POST"])
@admin_required
def settings_gst_test_email():
    company = get_company_settings()
    to_address = (request.form.get("test_email") or "").strip()
    if not to_address:
        flash("Enter an email address to send the test to.", "error")
    else:
        ok, message = gst_reminders.send_test_email(company, to_address)
        flash(message, "success" if ok else "error")
    return redirect(url_for("settings_form"))


# =======================================================================
# GST FILING  (GSTR-1 / GSTR-2B / GSTR-3B reference reports + reminders)
# =======================================================================
# See gst_logic.py's module docstring for the important caveats: due
# dates can change by government notification, and these figures are a
# computed reference/working paper, not a filing or a live pull from the
# GST portal.

GSTR2B_UPLOAD_DIR = "Gstr2b"  # folder name under uploads/, same convention as ATTACHMENT_MODULES


def _year_month_from_request():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    return year, month


# ---------------------------------------------------------------------
# Reports: Profit & Loss
#
# Computed on the fly from what's already tracked elsewhere - nothing new
# is entered here, and nothing is stored:
#   Revenue          = Sales.TaxableAmount (GST-exclusive) for Completed
#                       sales in the date range.
#   Cost of Goods Sold = each sold line's quantity x that product's CURRENT
#                       Products.CostPrice. This is a simplification: cost
#                       price isn't tracked historically per sale, so if a
#                       product's cost has changed since a past sale, that
#                       sale's COGS here uses today's cost rather than
#                       whatever it actually cost back then.
#   Operating Expenses = Expenses.Amount in the date range.
#   Salary Payouts    = SalaryPayments.NetPayable for payments marked Paid
#                       with a PaymentDate in the date range.
# Gross Profit = Revenue - COGS. Net Profit = Gross Profit - Expenses -
# Salary. This is a working-paper view for the business, not a statutory
# filing - it doesn't attempt double-entry bookkeeping, depreciation, or
# accrual adjustments.
# ---------------------------------------------------------------------

@app.route("/reports/pnl")
@admin_required
def pnl_report():
    today = date.today()
    default_from = today.replace(day=1).isoformat()
    default_to = today.isoformat()
    date_from = request.args.get("from") or default_from
    date_to = request.args.get("to") or default_to

    revenue_row = db.query("""SELECT COALESCE(SUM(TaxableAmount), 0) AS total FROM Sales
                            WHERE Status='Completed' AND SaleDate BETWEEN ? AND ?""",
                           (date_from, date_to), one=True)
    revenue = revenue_row["total"]

    cogs_row = db.query("""SELECT COALESCE(SUM(sl.Qty * pr.CostPrice), 0) AS total
                         FROM SalesLines sl
                         JOIN Sales s ON s.SaleID = sl.SaleID
                         JOIN Products pr ON pr.ProductID = sl.ProductID
                         WHERE s.Status='Completed' AND s.SaleDate BETWEEN ? AND ?""",
                        (date_from, date_to), one=True)
    cogs = cogs_row["total"]

    expense_rows = db.query("""SELECT Category, COALESCE(SUM(Amount), 0) AS total FROM Expenses
                             WHERE ExpenseDate BETWEEN ? AND ? GROUP BY Category ORDER BY total DESC""",
                            (date_from, date_to))
    total_expenses = sum(r["total"] for r in expense_rows)

    salary_row = db.query("""SELECT COALESCE(SUM(NetPayable), 0) AS total FROM SalaryPayments
                           WHERE Status='Paid' AND PaymentDate BETWEEN ? AND ?""",
                          (date_from, date_to), one=True)
    total_salary = salary_row["total"]

    incentive_row = db.query("""SELECT COALESCE(SUM(sl.Qty * pr.IncentivePerUnit), 0) AS total
                              FROM SalesLines sl
                              JOIN Sales s ON s.SaleID = sl.SaleID
                              JOIN Products pr ON pr.ProductID = sl.ProductID
                              WHERE s.Status='Completed' AND s.SaleDate BETWEEN ? AND ?""",
                             (date_from, date_to), one=True)
    incentive_income = round(incentive_row["total"], 2)

    gross_profit = round(revenue - cogs, 2)
    total_operating_costs = round(total_expenses + total_salary, 2)
    net_profit = round(gross_profit + incentive_income - total_operating_costs, 2)

    return render_template("pnl_report.html", date_from=date_from, date_to=date_to,
                            revenue=round(revenue, 2), cogs=round(cogs, 2), gross_profit=gross_profit,
                            incentive_income=incentive_income,
                            expense_rows=expense_rows, total_expenses=round(total_expenses, 2),
                            total_salary=round(total_salary, 2), total_operating_costs=total_operating_costs,
                            net_profit=net_profit)


@app.route("/gst")
@admin_required
def gst_dashboard():
    company = get_company_settings()
    due_items = gst_logic.gst_due_dates(company)
    today = date.today()
    recent_uploads = db.query("SELECT * FROM Gstr2bUploads ORDER BY UploadID DESC LIMIT 5")
    return render_template("gst_dashboard.html", company=company, due_items=due_items, today=today,
                            recent_uploads=recent_uploads)


@app.route("/gst/gstr1")
@admin_required
def gst_gstr1():
    year, month = _year_month_from_request()
    summary = gst_logic.gstr1_summary(year, month)
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("gst_gstr1.html", summary=summary, year=year, month=month,
                            month_name=MONTH_NAMES[month],
                            prev_month=prev_month, prev_year=prev_year,
                            next_month=next_month, next_year=next_year)


@app.route("/gst/gstr3b")
@admin_required
def gst_gstr3b():
    year, month = _year_month_from_request()
    company = get_company_settings()
    summary = gst_logic.gstr3b_summary(year, month)
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("gst_gstr3b.html", summary=summary, year=year, month=month,
                            month_name=MONTH_NAMES[month], company=company,
                            prev_month=prev_month, prev_year=prev_year,
                            next_month=next_month, next_year=next_year)


@app.route("/gst/gstr2b")
@admin_required
def gst_gstr2b_list():
    uploads = db.query("SELECT * FROM Gstr2bUploads ORDER BY Period DESC, UploadID DESC")
    return render_template("gst_gstr2b.html", uploads=uploads, today=today_str())


@app.route("/gst/gstr2b/upload", methods=["POST"])
@admin_required
def gst_gstr2b_upload():
    period = (request.form.get("period") or "").strip()
    file_storage = request.files.get("file")
    if not period:
        flash("Select which month (return period) this GSTR-2B file is for.", "error")
        return redirect(url_for("gst_gstr2b_list"))
    if not file_storage or not file_storage.filename:
        flash("Choose a GSTR-2B Excel (.xlsx) file to upload.", "error")
        return redirect(url_for("gst_gstr2b_list"))

    folder = os.path.join(UPLOAD_ROOT, GSTR2B_UPLOAD_DIR, period)
    os.makedirs(folder, exist_ok=True)
    safe_name = secure_filename(file_storage.filename) or f"gstr2b-{uuid.uuid4().hex[:8]}.xlsx"
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    full_path = os.path.join(folder, stored_name)
    file_storage.save(full_path)
    rel_path = "/".join([GSTR2B_UPLOAD_DIR, period, stored_name])

    result = gst_logic.parse_gstr2b_excel(full_path)
    upload_id = db.execute("""INSERT INTO Gstr2bUploads (Period, FileName, StoredPath, UploadedAt, ParseStatus,
                ParseMessage, ITCIntegrated, ITCCentral, ITCState, ITCCess, RecordCount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (period, safe_name, rel_path, datetime.now().isoformat(timespec="seconds"),
                "Parsed" if result["ok"] else "Failed", result["message"],
                result["itc_integrated"], result["itc_central"], result["itc_state"], result["itc_cess"],
                result["record_count"]))
    flash(result["message"], "success" if result["ok"] else "error")
    return redirect(url_for("gst_gstr2b_list"))


@app.route("/gst/gstr2b/<int:upload_id>/download")
@admin_required
def gst_gstr2b_download(upload_id):
    upload = db.query("SELECT * FROM Gstr2bUploads WHERE UploadID=?", (upload_id,), one=True)
    if not upload:
        flash("File not found.", "error")
        return redirect(url_for("gst_gstr2b_list"))
    full_path = os.path.join(UPLOAD_ROOT, upload["StoredPath"])
    directory = os.path.dirname(full_path)
    filename = os.path.basename(full_path)
    return send_from_directory(directory, filename, as_attachment=True, download_name=upload["FileName"])


@app.route("/gst/gstr2b/<int:upload_id>/delete", methods=["POST"])
@admin_required
def gst_gstr2b_delete(upload_id):
    upload = db.query("SELECT * FROM Gstr2bUploads WHERE UploadID=?", (upload_id,), one=True)
    if upload:
        full_path = os.path.join(UPLOAD_ROOT, upload["StoredPath"])
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except OSError:
            pass
        db.execute("DELETE FROM Gstr2bUploads WHERE UploadID=?", (upload_id,))
        flash(f"GSTR-2B upload for {upload['Period']} removed.", "success")
    return redirect(url_for("gst_gstr2b_list"))


# ---------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------

EXPENSE_CATEGORIES = ["Rent", "Utilities", "Office Supplies", "Fuel", "Toll",
                       "Loading/Unloading", "Salaries (misc)", "Repairs", "Insurance", "Other"]


@app.route("/expenses")
def expenses_list():
    expenses = db.query("""SELECT e.*, v.RegistrationNumber FROM Expenses e
                         LEFT JOIN Vehicles v ON v.VehicleID=e.VehicleID ORDER BY e.ExpenseDate DESC, e.ExpenseID DESC""")
    return render_template("expenses_list.html", expenses=expenses, categories=EXPENSE_CATEGORIES,
                            columns=get_effective_columns("Expense"))


@app.route("/expenses/new", methods=["GET", "POST"])
@app.route("/expenses/<int:eid>/edit", methods=["GET", "POST"])
def expense_form(eid=None):
    expense = db.query("SELECT * FROM Expenses WHERE ExpenseID=?", (eid,), one=True) if eid else None
    if request.method == "POST":
        f = request.form
        args = (f["expense_date"], f["category"], int(f["vehicle_id"]) if f.get("vehicle_id") else None,
                float(f["amount"]), f["payment_mode"], f["paid_to"], f.get("description", ""))
        if eid:
            db.execute("""UPDATE Expenses SET ExpenseDate=?, Category=?, VehicleID=?, Amount=?, PaymentMode=?,
                        PaidTo=?, Description=? WHERE ExpenseID=?""", args + (eid,))
            save_custom_fields("Expense", eid, f)
        else:
            new_id = db.execute("""INSERT INTO Expenses (ExpenseDate, Category, VehicleID, Amount, PaymentMode, PaidTo,
                        Description) VALUES (?,?,?,?,?,?,?)""", args)
            save_custom_fields("Expense", new_id, f)
        flash("Expense saved.", "success")
        return redirect(url_for("expenses_list"))
    vehicles = db.query("SELECT * FROM Vehicles ORDER BY RegistrationNumber")
    custom_fields = get_custom_field_defs("Expense")
    custom_values = get_custom_values("Expense", eid) if eid else {}
    attachments = get_attachments("Expense", eid) if eid else []
    return render_template("expense_form.html", expense=expense, vehicles=vehicles,
                            categories=EXPENSE_CATEGORIES, today=today_str(),
                            cf_record_id=eid, custom_attachments=get_custom_attachments("Expense", eid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            att_module="Expense", att_record_id=eid, attachments=attachments)


# ---------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------

@app.route("/vehicles")
def vehicles_list():
    return render_template("vehicles_list.html", vehicles=db.query("SELECT * FROM Vehicles ORDER BY RegistrationNumber"),
                            today=today_str(), columns=get_effective_columns("Vehicle"))


@app.route("/vehicles/new", methods=["GET", "POST"])
@app.route("/vehicles/<int:vid>/edit", methods=["GET", "POST"])
def vehicle_form(vid=None):
    vehicle = db.query("SELECT * FROM Vehicles WHERE VehicleID=?", (vid,), one=True) if vid else None
    if request.method == "POST":
        f = request.form
        args = (f["registration_number"], f["vehicle_type"], f["make"], f["model"],
                f.get("purchase_date") or None, f.get("insurance_expiry") or None,
                f.get("permit_expiry") or None, f.get("puc_expiry") or None,
                f.get("fitness_expiry") or None, float(f["current_odometer"] or 0), f["status"])
        if vid:
            db.execute("""UPDATE Vehicles SET RegistrationNumber=?, VehicleType=?, Make=?, Model=?,
                        PurchaseDate=?, InsuranceExpiry=?, PermitExpiry=?, PUCExpiry=?, FitnessExpiry=?,
                        CurrentOdometer=?, Status=? WHERE VehicleID=?""", args + (vid,))
            save_custom_fields("Vehicle", vid, f)
        else:
            new_id = db.execute("""INSERT INTO Vehicles (RegistrationNumber, VehicleType, Make, Model, PurchaseDate,
                        InsuranceExpiry, PermitExpiry, PUCExpiry, FitnessExpiry, CurrentOdometer, Status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", args)
            save_custom_fields("Vehicle", new_id, f)
        flash("Vehicle saved.", "success")
        return redirect(url_for("vehicles_list"))
    custom_fields = get_custom_field_defs("Vehicle")
    custom_values = get_custom_values("Vehicle", vid) if vid else {}
    attachments = get_attachments("Vehicle", vid) if vid else []
    return render_template("vehicle_form.html", vehicle=vehicle,
                            cf_record_id=vid, custom_attachments=get_custom_attachments("Vehicle", vid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            att_module="Vehicle", att_record_id=vid, attachments=attachments)


# ---------------------------------------------------------------------
# Vehicle Maintenance
# ---------------------------------------------------------------------

SERVICE_TYPES = ["Oil Change", "Tyre Replacement", "General Service", "Repair",
                  "Insurance Renewal", "Permit Renewal", "PUC Renewal", "Fitness Renewal", "Other"]


@app.route("/maintenance")
def maintenance_list():
    records = db.query("""SELECT vm.*, v.RegistrationNumber FROM VehicleMaintenance vm
                        JOIN Vehicles v ON v.VehicleID=vm.VehicleID ORDER BY vm.ServiceDate DESC, vm.MaintenanceID DESC""")
    return render_template("maintenance_list.html", records=records, due=upcoming_maintenance(days=30),
                            columns=get_effective_columns("Maintenance"))


@app.route("/maintenance/new", methods=["GET", "POST"])
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
# Employees
# ---------------------------------------------------------------------

def get_active_leave_types():
    return db.query("SELECT * FROM LeaveTypes WHERE Active=1 ORDER BY DisplayOrder, LeaveTypeID")


@app.route("/employees")
@admin_required
def employees_list():
    return render_template("employees_list.html", employees=db.query("SELECT * FROM Employees ORDER BY EmployeeName"),
                            columns=get_effective_columns("Employee"))


@app.route("/employees/new", methods=["GET", "POST"])
@app.route("/employees/<int:eid>/edit", methods=["GET", "POST"])
@admin_required
def employee_form(eid=None):
    employee = db.query("SELECT * FROM Employees WHERE EmployeeID=?", (eid,), one=True) if eid else None
    if request.method == "POST":
        f = request.form
        args = (f["employee_name"], f["designation"], f["phone"], f.get("join_date") or None,
                float(f["monthly_salary"] or 0), f["bank_account"], f["status"])
        if eid:
            db.execute("""UPDATE Employees SET EmployeeName=?, Designation=?, Phone=?, JoinDate=?,
                        MonthlySalary=?, BankAccount=?, Status=? WHERE EmployeeID=?""", args + (eid,))
            save_custom_fields("Employee", eid, f)
        else:
            new_id = db.execute("""INSERT INTO Employees (EmployeeName, Designation, Phone, JoinDate, MonthlySalary,
                        BankAccount, Status) VALUES (?,?,?,?,?,?,?)""", args)
            save_custom_fields("Employee", new_id, f)
        flash("Employee saved.", "success")
        return redirect(url_for("employees_list"))
    custom_fields = get_custom_field_defs("Employee")
    custom_values = get_custom_values("Employee", eid) if eid else {}
    attachments = get_attachments("Employee", eid) if eid else []
    return render_template("employee_form.html", employee=employee,
                            cf_record_id=eid, custom_attachments=get_custom_attachments("Employee", eid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            att_module="Employee", att_record_id=eid, attachments=attachments)


# ---------------------------------------------------------------------
# Salary (monthly schedule)
# ---------------------------------------------------------------------

@app.route("/salary")
@admin_required
def salary_month():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    records = db.query("""SELECT sp.*, e.EmployeeName, e.Designation FROM SalaryPayments sp
                        JOIN Employees e ON e.EmployeeID=sp.EmployeeID
                        WHERE sp.SalaryYear=? AND sp.SalaryMonth=? ORDER BY e.EmployeeName""", (year, month))
    generated_ids = {r["EmployeeID"] for r in records}
    missing = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    missing = [m for m in missing if m["EmployeeID"] not in generated_ids]
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("salary_month.html", records=records, missing=missing, year=year, month=month,
                            month_name=MONTH_NAMES[month], prev_month=prev_month, prev_year=prev_year,
                            next_month=next_month, next_year=next_year, today=today_str(),
                            columns=get_effective_columns("SalaryPayment"))


def compute_salary_figures(e, year, month):
    """Computes one employee's pay for a given month: gross pro-rated by
    days actually worked (days in month, minus any days before their Join
    Date if they joined mid-month, minus loss-of-pay days from the Leave &
    Attendance tab), minus the currently-active advance deduction. Used by
    both salary_generate() (first-time schedule creation) and
    salary_refresh() (re-pull advances / recompute for a Pending row)."""
    gross = e["MonthlySalary"]
    days_in_month = calendar.monthrange(year, month)[1]

    # Loss-of-pay: only applied if attendance was actually recorded for this
    # employee/month (Leave & Attendance tab). No attendance entry -> 0 LOP
    # days, same behavior as before attendance tracking existed.
    att = db.query("SELECT * FROM AttendanceMonthly WHERE EmployeeID=? AND AttYear=? AND AttMonth=?",
                   (e["EmployeeID"], year, month), one=True)
    lop_days = att["LOPDays"] if att else 0.0
    if att and att["DaysInMonth"]:
        days_in_month = att["DaysInMonth"]

    # Days before Join Date: if the employee joined partway through this
    # month, don't pay for the days before they joined. If they haven't
    # joined by the end of this month at all, payable days is 0.
    days_before_join = 0.0
    join_date_str = e["JoinDate"]
    if join_date_str:
        try:
            jd = datetime.strptime(join_date_str[:10], "%Y-%m-%d").date()
            month_start = date(year, month, 1)
            month_end = date(year, month, calendar.monthrange(year, month)[1])
            if jd > month_end:
                days_before_join = days_in_month  # not yet joined this month - 0 payable days
            elif jd > month_start:
                days_before_join = (jd - month_start).days  # join day itself still counts as worked
        except ValueError:
            pass

    per_day = (gross / days_in_month) if days_in_month else 0
    payable_days = max(days_in_month - days_before_join - lop_days, 0)
    basic = round(per_day * payable_days, 2)

    advances = db.query("SELECT * FROM AdvancePayments WHERE EmployeeID=? AND Status='Active'", (e["EmployeeID"],))
    advance_deduct = round(sum(min(a["MonthlyDeduction"], a["BalanceRemaining"]) for a in advances), 2)
    net = round(basic - advance_deduct, 2)
    return dict(gross=gross, lop_days=lop_days, days_before_join=days_before_join,
                days_in_month=days_in_month, basic=basic, advance_deduct=advance_deduct, net=net)


@app.route("/salary/generate", methods=["POST"])
@admin_required
def salary_generate():
    year = int(request.form["year"])
    month = int(request.form["month"])
    employees = db.query("SELECT * FROM Employees WHERE Status='Active'")
    created = 0
    for e in employees:
        exists = db.query("SELECT 1 FROM SalaryPayments WHERE EmployeeID=? AND SalaryYear=? AND SalaryMonth=?",
                           (e["EmployeeID"], year, month), one=True)
        if exists:
            continue
        fig = compute_salary_figures(e, year, month)
        db.execute("""INSERT INTO SalaryPayments (EmployeeID, SalaryYear, SalaryMonth, GrossSalary, LOPDays,
                    BasicAmount, Bonus, Deductions, AdvanceDeducted, NetPayable, Status)
                    VALUES (?,?,?,?,?,?,0,0,?,?,?)""",
                   (e["EmployeeID"], year, month, fig["gross"], fig["lop_days"], fig["basic"],
                    fig["advance_deduct"], fig["net"], "Pending"))
        created += 1
    flash(f"Salary schedule generated for {MONTH_NAMES[month]} {year}"
          f"{f' ({created} new record(s))' if created else ' (nothing new — all employees already have a record)'}.",
          "success")
    return redirect(url_for("salary_month", year=year, month=month))


@app.route("/salary/refresh", methods=["POST"])
@admin_required
def salary_refresh():
    """Re-pulls active advances and recalculates pro-rated pay (Join Date,
    attendance, leave policy) for every Pending salary row this month, and
    creates any still-missing rows too (same as Generate) — use this after
    giving an advance or updating attendance so the schedule reflects it
    before anyone is marked Paid. Rows already marked Paid are left alone,
    since that money has already gone out the door."""
    year = int(request.form["year"])
    month = int(request.form["month"])
    employees = db.query("SELECT * FROM Employees WHERE Status='Active'")
    updated = created = skipped_paid = 0
    for e in employees:
        existing = db.query("SELECT * FROM SalaryPayments WHERE EmployeeID=? AND SalaryYear=? AND SalaryMonth=?",
                            (e["EmployeeID"], year, month), one=True)
        if existing and existing["Status"] == "Paid":
            skipped_paid += 1
            continue
        fig = compute_salary_figures(e, year, month)
        if existing:
            db.execute("""UPDATE SalaryPayments SET GrossSalary=?, LOPDays=?, BasicAmount=?, AdvanceDeducted=?,
                        NetPayable=? WHERE PaymentID=?""",
                       (fig["gross"], fig["lop_days"], fig["basic"], fig["advance_deduct"], fig["net"],
                        existing["PaymentID"]))
            updated += 1
        else:
            db.execute("""INSERT INTO SalaryPayments (EmployeeID, SalaryYear, SalaryMonth, GrossSalary, LOPDays,
                        BasicAmount, Bonus, Deductions, AdvanceDeducted, NetPayable, Status)
                        VALUES (?,?,?,?,?,?,0,0,?,?,?)""",
                       (e["EmployeeID"], year, month, fig["gross"], fig["lop_days"], fig["basic"],
                        fig["advance_deduct"], fig["net"], "Pending"))
            created += 1
    msg = f"Refreshed {MONTH_NAMES[month]} {year}: {updated} updated, {created} newly added"
    if skipped_paid:
        msg += f", {skipped_paid} already Paid (left as-is)"
    flash(msg + ".", "success")
    return redirect(url_for("salary_month", year=year, month=month))


@app.route("/salary/<int:pid>/pay", methods=["POST"])
@admin_required
def salary_pay(pid):
    payment = db.query("SELECT * FROM SalaryPayments WHERE PaymentID=?", (pid,), one=True)
    db.execute("""UPDATE SalaryPayments SET Status='Paid', PaymentDate=?, PaymentMode=? WHERE PaymentID=?""",
               (request.form.get("payment_date") or today_str(), request.form.get("payment_mode", "Bank"), pid))
    if payment["AdvanceDeducted"] > 0:
        remaining_to_apply = payment["AdvanceDeducted"]
        advances = db.query("""SELECT * FROM AdvancePayments WHERE EmployeeID=? AND Status='Active'
                             ORDER BY AdvanceDate""", (payment["EmployeeID"],))
        for a in advances:
            if remaining_to_apply <= 0:
                break
            deduct = min(a["MonthlyDeduction"], a["BalanceRemaining"], remaining_to_apply)
            new_balance = a["BalanceRemaining"] - deduct
            status = "Closed" if new_balance <= 0.0001 else "Active"
            db.execute("UPDATE AdvancePayments SET BalanceRemaining=?, Status=? WHERE AdvanceID=?",
                       (max(new_balance, 0), status, a["AdvanceID"]))
            remaining_to_apply -= deduct
    flash("Salary marked as paid.", "success")
    return redirect(url_for("salary_month", year=payment["SalaryYear"], month=payment["SalaryMonth"]))


# ---------------------------------------------------------------------
# Advance payments
# ---------------------------------------------------------------------

@app.route("/advances")
@admin_required
def advances_list():
    advances = db.query("""SELECT a.*, e.EmployeeName FROM AdvancePayments a
                         JOIN Employees e ON e.EmployeeID=a.EmployeeID ORDER BY a.AdvanceDate DESC""")
    return render_template("advances_list.html", advances=advances, columns=get_effective_columns("Advance"))


@app.route("/advances/new", methods=["GET", "POST"])
@admin_required
def advance_form():
    if request.method == "POST":
        f = request.form
        amount = float(f["amount"])
        months = max(int(f["repayment_months"] or 1), 1)
        monthly = round(amount / months, 2)
        new_id = db.execute("""INSERT INTO AdvancePayments (EmployeeID, AdvanceDate, Amount, Reason, RepaymentMonths,
                    MonthlyDeduction, BalanceRemaining, Status) VALUES (?,?,?,?,?,?,?,?)""",
                   (int(f["employee_id"]), f["advance_date"], amount, f.get("reason", ""), months,
                    monthly, amount, "Active"))
        save_custom_fields("Advance", new_id, f)
        flash("Advance payment recorded.", "success")
        return redirect(url_for("advance_view", aid=new_id))
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    custom_fields = get_custom_field_defs("Advance")
    return render_template("advance_form.html", employees=employees, today=today_str(),
                            custom_fields=custom_fields, custom_values={},
                            cf_record_id=None, custom_attachments={})


@app.route("/advances/<int:aid>")
@admin_required
def advance_view(aid):
    advance = db.query("""SELECT a.*, e.EmployeeName, e.Designation, e.Phone FROM AdvancePayments a
                        JOIN Employees e ON e.EmployeeID=a.EmployeeID WHERE a.AdvanceID=?""", (aid,), one=True)
    if not advance:
        flash("Advance not found.", "error")
        return redirect(url_for("advances_list"))
    custom_fields = get_custom_field_defs("Advance")
    custom_values = get_custom_values("Advance", aid)
    attachments = get_attachments("Advance", aid)
    return render_template("advance_view.html", advance=advance,
                            cf_record_id=aid, custom_attachments=get_custom_attachments("Advance", aid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            att_module="Advance", att_record_id=aid, attachments=attachments)


@app.route("/advances/<int:aid>/close", methods=["POST"])
@admin_required
def advance_close(aid):
    db.execute("UPDATE AdvancePayments SET Status='Closed', BalanceRemaining=0 WHERE AdvanceID=?", (aid,))
    flash("Advance closed.", "success")
    return redirect(request.form.get("return_to") or url_for("advances_list"))


# =======================================================================
# EMPLOYEE LEAVE & ATTENDANCE
# =======================================================================
# Attendance is entered as one MONTHLY SUMMARY per employee (present days,
# weekly-off/holiday days, and days taken per configured leave type) —
# not a daily calendar log. Each leave type's days are automatically split
# into Paid (covered by the SHARED remaining annual quota for that leave
# type — the same quota every employee draws from, tracked per-employee
# only in terms of how much each one has used) and Unpaid the moment the
# month is saved; Loss-of-Pay days = days in month - present - weekly off
# - paid leave, folded straight into that month's salary generation as a
# per-day deduction.

def get_shared_leave_quotas():
    """dict LeaveTypeID -> AnnualQuota. Quotas are shared: every employee
    draws from the same annual quota for a given leave type, configured
    once on the Leave Types page rather than per employee."""
    rows = db.query("SELECT LeaveTypeID, AnnualQuota FROM LeaveTypes")
    return {r["LeaveTypeID"]: r["AnnualQuota"] for r in rows}


def get_leave_paid_used_before(employee_id, leave_type_id, year, month):
    """Total PAID leave days already used this calendar year, strictly
    before the given month — i.e. how much of the annual quota is left
    to draw on when this month is saved."""
    r = db.query("""SELECT COALESCE(SUM(ald.PaidDays),0) AS u FROM AttendanceLeaveDetail ald
                  JOIN AttendanceMonthly am ON am.AttendanceID = ald.AttendanceID
                  WHERE am.EmployeeID=? AND ald.LeaveTypeID=? AND am.AttYear=? AND am.AttMonth<?""",
                 (employee_id, leave_type_id, year, month), one=True)
    return r["u"] or 0.0


def get_leave_used_in_year(employee_id, leave_type_id, year):
    r = db.query("""SELECT COALESCE(SUM(ald.PaidDays),0) AS u FROM AttendanceLeaveDetail ald
                  JOIN AttendanceMonthly am ON am.AttendanceID = ald.AttendanceID
                  WHERE am.EmployeeID=? AND ald.LeaveTypeID=? AND am.AttYear=?""",
                 (employee_id, leave_type_id, year), one=True)
    return r["u"] or 0.0


def get_leave_balance_asof(employee_id, year, month):
    """dict LeaveTypeID -> {quota, used_before, remaining_before} as of
    the start of the given month (used to show a reference balance while
    entering that month's attendance)."""
    quotas = get_shared_leave_quotas()
    out = {}
    for lt in get_active_leave_types():
        ltid = lt["LeaveTypeID"]
        used = get_leave_paid_used_before(employee_id, ltid, year, month)
        quota = quotas.get(ltid, 0)
        out[ltid] = dict(quota=quota, used_before=used, remaining_before=max(quota - used, 0))
    return out


def get_attendance_month(employee_id, year, month):
    return db.query("SELECT * FROM AttendanceMonthly WHERE EmployeeID=? AND AttYear=? AND AttMonth=?",
                    (employee_id, year, month), one=True)


def get_attendance_leave_detail(attendance_id):
    return db.query("""SELECT ald.*, lt.LeaveTypeName, lt.LeaveCode FROM AttendanceLeaveDetail ald
                     JOIN LeaveTypes lt ON lt.LeaveTypeID = ald.LeaveTypeID WHERE ald.AttendanceID=?""",
                    (attendance_id,))


def save_attendance(employee_id, year, month, present_days, weekly_off_days, leave_inputs, notes):
    """leave_inputs: dict {LeaveTypeID: days_taken}. Computes each leave
    type's paid/unpaid split against the employee's remaining annual quota
    (quota minus what they've already used earlier in the same calendar
    year), locks that split in, and derives the month's LOPDays. Returns
    the computed LOPDays."""
    days_in_month = calendar.monthrange(year, month)[1]
    quotas = get_shared_leave_quotas()

    total_paid_leave = 0.0
    detail_rows = []
    for leave_type_id, days_taken in leave_inputs.items():
        if days_taken <= 0:
            continue
        prior_used = get_leave_paid_used_before(employee_id, leave_type_id, year, month)
        quota = quotas.get(leave_type_id, 0)
        remaining = max(quota - prior_used, 0)
        paid = min(days_taken, remaining)
        unpaid = days_taken - paid
        total_paid_leave += paid
        detail_rows.append((leave_type_id, days_taken, paid, unpaid))

    lop_days = max(days_in_month - present_days - weekly_off_days - total_paid_leave, 0)

    existing = db.query("SELECT AttendanceID FROM AttendanceMonthly WHERE EmployeeID=? AND AttYear=? AND AttMonth=?",
                        (employee_id, year, month), one=True)
    now = datetime.now().isoformat(timespec="seconds")
    if existing:
        attendance_id = existing["AttendanceID"]
        db.execute("""UPDATE AttendanceMonthly SET DaysInMonth=?, PresentDays=?, WeeklyOffDays=?, LOPDays=?,
                    Notes=?, UpdatedAt=? WHERE AttendanceID=?""",
                   (days_in_month, present_days, weekly_off_days, lop_days, notes, now, attendance_id))
        db.execute("DELETE FROM AttendanceLeaveDetail WHERE AttendanceID=?", (attendance_id,))
    else:
        attendance_id = db.execute("""INSERT INTO AttendanceMonthly (EmployeeID, AttYear, AttMonth, DaysInMonth,
                    PresentDays, WeeklyOffDays, LOPDays, Notes, UpdatedAt) VALUES (?,?,?,?,?,?,?,?,?)""",
                   (employee_id, year, month, days_in_month, present_days, weekly_off_days, lop_days, notes, now))

    for leave_type_id, days_taken, paid, unpaid in detail_rows:
        db.execute("""INSERT INTO AttendanceLeaveDetail (AttendanceID, LeaveTypeID, DaysTaken, PaidDays, UnpaidDays)
                    VALUES (?,?,?,?,?)""", (attendance_id, leave_type_id, days_taken, paid, unpaid))

    return lop_days


@app.route("/attendance")
def attendance_month():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    days_in_month = calendar.monthrange(year, month)[1]
    leave_types = get_active_leave_types()
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")

    rows = []
    for e in employees:
        att = get_attendance_month(e["EmployeeID"], year, month)
        if att:
            detail = {d["LeaveTypeID"]: d["DaysTaken"] for d in get_attendance_leave_detail(att["AttendanceID"])}
            present_days, weekly_off_days, lop_days, notes = att["PresentDays"], att["WeeklyOffDays"], att["LOPDays"], att["Notes"] or ""
            saved = True
        else:
            detail = {}
            present_days, weekly_off_days, lop_days, notes = days_in_month, 0, 0, ""
            saved = False
        balance = get_leave_balance_asof(e["EmployeeID"], year, month)
        rows.append(dict(employee=e, present_days=present_days, weekly_off_days=weekly_off_days,
                          lop_days=lop_days, notes=notes, saved=saved, leave_days=detail, balance=balance))

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("attendance_month.html", rows=rows, leave_types=leave_types, year=year, month=month,
                            month_name=MONTH_NAMES[month], days_in_month=days_in_month,
                            prev_month=prev_month, prev_year=prev_year, next_month=next_month, next_year=next_year)


@app.route("/attendance/save", methods=["POST"])
def attendance_save():
    f = request.form
    employee_id = int(f["employee_id"])
    year = int(f["year"])
    month = int(f["month"])
    try:
        present_days = float(f.get("present_days") or 0)
        weekly_off_days = float(f.get("weekly_off_days") or 0)
    except ValueError:
        flash("Present/weekly-off days must be numbers.", "error")
        return redirect(url_for("attendance_month", year=year, month=month))

    leave_inputs = {}
    for lt in get_active_leave_types():
        raw = f.get(f"leave_{lt['LeaveTypeID']}", "")
        try:
            leave_inputs[lt["LeaveTypeID"]] = float(raw) if raw else 0.0
        except ValueError:
            leave_inputs[lt["LeaveTypeID"]] = 0.0

    lop_days = save_attendance(employee_id, year, month, present_days, weekly_off_days,
                                leave_inputs, f.get("notes", ""))
    if lop_days > 0:
        flash(f"Attendance saved — {lop_days:g} loss-of-pay day(s) will be deducted when salary is generated.", "success")
    else:
        flash("Attendance saved.", "success")
    return redirect(url_for("attendance_month", year=year, month=month))


@app.route("/attendance/leave-types")
def leave_types_admin():
    leave_types = db.query("SELECT * FROM LeaveTypes ORDER BY DisplayOrder, LeaveTypeID")
    return render_template("leave_types_admin.html", leave_types=leave_types)


@app.route("/attendance/leave-types/add", methods=["POST"])
def leave_type_add():
    f = request.form
    name = f["leave_type_name"].strip()
    code = f["leave_code"].strip().upper()
    if not name or not code:
        flash("Leave type name and code are required.", "error")
        return redirect(url_for("leave_types_admin"))
    try:
        annual_quota = float(f.get("annual_quota") or 0)
    except ValueError:
        annual_quota = 0
    max_order = db.query("SELECT COALESCE(MAX(DisplayOrder),0) AS mo FROM LeaveTypes", one=True)["mo"]
    try:
        db.execute("""INSERT INTO LeaveTypes (LeaveTypeName, LeaveCode, Active, DisplayOrder, AnnualQuota)
                    VALUES (?,?,1,?,?)""", (name, code, max_order + 1, annual_quota))
        flash(f"Leave type '{name}' added.", "success")
    except Exception:
        flash(f"Could not add leave type — '{code}' may already exist.", "error")
    return redirect(url_for("leave_types_admin"))


@app.route("/attendance/leave-types/<int:leave_type_id>/update", methods=["POST"])
def leave_type_update(leave_type_id):
    try:
        annual_quota = float(request.form.get("annual_quota") or 0)
    except ValueError:
        annual_quota = 0
    lt = db.query("SELECT LeaveTypeName FROM LeaveTypes WHERE LeaveTypeID=?", (leave_type_id,), one=True)
    if lt:
        db.execute("UPDATE LeaveTypes SET AnnualQuota=? WHERE LeaveTypeID=?", (annual_quota, leave_type_id))
        flash(f"Annual quota for '{lt['LeaveTypeName']}' updated to {annual_quota:g} days/year — applies to every employee.", "success")
    return redirect(url_for("leave_types_admin"))


@app.route("/attendance/leave-types/<int:leave_type_id>/toggle", methods=["POST"])
def leave_type_toggle(leave_type_id):
    lt = db.query("SELECT * FROM LeaveTypes WHERE LeaveTypeID=?", (leave_type_id,), one=True)
    if lt:
        db.execute("UPDATE LeaveTypes SET Active=? WHERE LeaveTypeID=?", (0 if lt["Active"] else 1, leave_type_id))
    return redirect(url_for("leave_types_admin"))


@app.route("/attendance/leave-types/<int:leave_type_id>/delete", methods=["POST"])
@admin_required
def leave_type_delete(leave_type_id):
    db.execute("DELETE FROM LeaveTypes WHERE LeaveTypeID=?", (leave_type_id,))
    flash("Leave type deleted (its quotas and any recorded leave days against it are removed too).", "success")
    return redirect(url_for("leave_types_admin"))


@app.route("/attendance/leave-balance")
def leave_balance():
    today = date.today()
    year = int(request.args.get("year", today.year))
    leave_types = get_active_leave_types()
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    quotas = get_shared_leave_quotas()
    rows = []
    for e in employees:
        per_type = []
        for lt in leave_types:
            quota = quotas.get(lt["LeaveTypeID"], 0)
            used = get_leave_used_in_year(e["EmployeeID"], lt["LeaveTypeID"], year)
            per_type.append(dict(leave_type=lt, quota=quota, used=used, balance=max(quota - used, 0)))
        rows.append(dict(employee=e, per_type=per_type))
    return render_template("leave_balance.html", rows=rows, leave_types=leave_types, year=year)


# ---------------------------------------------------------------------
# API (small JSON helper for the sale/purchase line-item forms)
# ---------------------------------------------------------------------

@app.route("/api/product/<int:pid>")
def api_product(pid):
    p = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
    if not p:
        return jsonify({}), 404
    stock = get_product_stock(pid)
    return jsonify(dict(cost_price=p["CostPrice"], selling_price=p["SellingPrice"], stock=stock, unit=p["Unit"]))


if __name__ == "__main__":
    if not os.path.exists(db.DB_PATH):
        db.init_db()
    else:
        db.init_db()  # safe: CREATE TABLE IF NOT EXISTS
    # Flask's debug reloader re-executes this file in a second process;
    # WERKZEUG_RUN_MAIN is only set in that second (actually-serving) one,
    # so this guard stops the reminder thread starting twice and sending
    # every email twice.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        gst_reminders.start_background_reminder_thread()
    app.run(host="0.0.0.0", port=5000, debug=True)
