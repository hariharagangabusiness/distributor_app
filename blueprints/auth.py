"""Auth, home and dashboard routes."""
import db
import gst_logic
import logging
import threading
import time
from core.auth import default_landing_url, get_current_user
from core.helpers import DASHBOARD_WIDGET_LABELS, MONTH_NAMES, _selected_month_from_request, active_advances, employees_missing_salary_record, expiring_documents, get_company_settings, get_company_target_summary, get_dashboard_widgets, get_stock_returns_summary, low_stock_products, over_stock_products, pending_salary_this_month, upcoming_maintenance
from datetime import date
from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from flask import Blueprint

logger = logging.getLogger("distributor_app")
bp = Blueprint("auth", __name__)


@bp.route("/")
def home():
    user = get_current_user()
    if user:
        return redirect(default_landing_url(user))
    return render_template("home.html")


# Login rate-limiting: nothing previously stopped an attacker (or a script)
# from hammering /login with unlimited password guesses. This is a simple
# in-memory sliding-window lock, not a database table - the app runs as a
# single gunicorn worker (see Procfile), so one process's memory is the
# whole picture, and losing these counters on a redeploy just means a clean
# slate, not a security hole. Tracked per *username* (so an attacker can't
# brute-force one account forever) and per *IP* (so an attacker can't just
# cycle through usernames from the same place) - either one tripping locks
# the attempt out.
LOGIN_MAX_ATTEMPTS = 5


LOGIN_WINDOW_SECONDS = 15 * 60


LOGIN_LOCKOUT_SECONDS = 15 * 60


_login_lock = threading.Lock()


_login_failures = {}  # "user:<username lower>" or "ip:<addr>" -> [failure timestamps]


def _login_rate_keys(username, ip):
    keys = [f"ip:{ip}"]
    if username:
        keys.append(f"user:{username.strip().lower()}")
    return keys


def _login_lockout_remaining(keys):
    """Returns the number of seconds any of these keys is still locked out for, or None."""
    now = time.time()
    remaining = None
    with _login_lock:
        for key in keys:
            attempts = [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
            _login_failures[key] = attempts
            if len(attempts) >= LOGIN_MAX_ATTEMPTS:
                key_remaining = LOGIN_LOCKOUT_SECONDS - (now - attempts[-1])
                if key_remaining > 0:
                    remaining = max(remaining or 0, key_remaining)
    return int(remaining) + 1 if remaining else None


def _record_login_failure(keys):
    now = time.time()
    with _login_lock:
        for key in keys:
            _login_failures.setdefault(key, []).append(now)


def _clear_login_failures(keys):
    with _login_lock:
        for key in keys:
            _login_failures.pop(key, None)


@bp.route("/login", methods=["GET", "POST"])
def login():
    current = get_current_user()
    if current:
        return redirect(default_landing_url(current))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = request.form.get("remember") == "on"
        rate_keys = _login_rate_keys(username, request.remote_addr)
        locked_for = _login_lockout_remaining(rate_keys)
        if locked_for:
            minutes = max(locked_for // 60, 1)
            logger.warning("Login blocked by rate limit: username=%r ip=%s", username, request.remote_addr)
            flash(f"Too many failed login attempts. Please try again in {minutes} minute(s).", "error")
            return render_template("login.html", next=request.args.get("next", ""))
        user = db.query("SELECT * FROM Users WHERE Username=? COLLATE NOCASE", (username,), one=True)
        if user and user["Active"] and check_password_hash(user["PasswordHash"], password):
            _clear_login_failures(rate_keys)
            session.clear()
            session["user_id"] = user["UserID"]
            session.permanent = remember
            db.execute("UPDATE Users SET LastLoginAt=datetime('now') WHERE UserID=?", (user["UserID"],))
            nxt = request.form.get("next") or request.args.get("next")
            flash(f"Welcome back, {user['FullName'] or user['Username']}.", "success")
            return redirect(nxt if nxt and nxt.startswith("/") else default_landing_url(user))
        _record_login_failure(rate_keys)
        flash("Incorrect username or password.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You've been signed out.", "success")
    return redirect(url_for("home"))


@bp.route("/account/change-password", methods=["GET", "POST"])
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


@bp.route("/dashboard/customize", methods=["GET", "POST"])
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


@bp.route("/dashboard")
def dashboard():
    today = date.today()
    sel = _selected_month_from_request()
    month_start, month_end = sel["month_start"], sel["month_end"]

    sales_today = db.query("SELECT COALESCE(SUM(TotalAmount),0) AS t FROM Sales WHERE SaleDate=? AND Status<>'Cancelled'",
                            (today.isoformat(),), one=True)["t"]
    sales_month = db.query("SELECT COALESCE(SUM(TotalAmount),0) AS t FROM Sales WHERE SaleDate BETWEEN ? AND ? AND Status<>'Cancelled'",
                            (month_start, month_end), one=True)["t"]
    purchases_month = db.query("SELECT COALESCE(SUM(TotalAmount),0) AS t FROM Purchases WHERE PurchaseDate BETWEEN ? AND ? AND Status<>'Cancelled'",
                                (month_start, month_end), one=True)["t"]
    expenses_month = db.query("SELECT COALESCE(SUM(Amount),0) AS t FROM Expenses WHERE ExpenseDate BETWEEN ? AND ?",
                               (month_start, month_end), one=True)["t"]
    # Receivable/Payable/Van Sales Due/Scheme Claims Pending are point-in-time
    # outstanding balances (as of now), not scoped to the month being viewed.
    # Reverse Charge sales are only receivable up to their taxable (product)
    # value - the GST portion is paid by the customer straight to the
    # government, not collected by us.
    receivable = db.query("""SELECT COALESCE(SUM((CASE WHEN ReverseCharge THEN TaxableAmount ELSE TotalAmount END)
                           - AmountReceived),0) AS t FROM Sales WHERE PaymentStatus<>'Paid' AND Status<>'Cancelled'""",
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
    # the month being viewed, combined across every salesperson that has one set.
    target_summary = get_company_target_summary(sel["year"], sel["month"])
    stock_returns_summary = get_stock_returns_summary(sel["year"], sel["month"])

    return render_template("dashboard.html",
                            sales_today=sales_today, sales_month=sales_month,
                            purchases_month=purchases_month, expenses_month=expenses_month,
                            receivable=receivable, payable=payable,
                            stock_issue_due=stock_issue_due, scheme_claim_pending=scheme_claim_pending,
                            unassigned_sales_count=unassigned_sales_count, unassigned_sales_amount=unassigned_sales_amount,
                            low_stock=low_stock_products(), over_stock=over_stock_products(),
                            maintenance=upcoming_maintenance(), docs=expiring_documents(),
                            pending_salary=pending_salary_this_month(sel["year"], sel["month"]),
                            missing_salary=employees_missing_salary_record(sel["year"], sel["month"]),
                            advances=active_advances(), gst_due=gst_due, target_summary=target_summary,
                            stock_returns_summary=stock_returns_summary,
                            month_name=MONTH_NAMES[sel["month"]], year=sel["year"], month=sel["month"],
                            is_current_month=sel["is_current"],
                            prev_year=sel["prev_year"], prev_month=sel["prev_month"],
                            next_year=sel["next_year"], next_month=sel["next_month"],
                            widget_visible=widget_visible, widget_order=widget_order)
