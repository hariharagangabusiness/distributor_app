"""Current-user lookup, admin gate and per-tab role access control."""
import db
from flask import flash, g, redirect, session, url_for
from functools import wraps



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


# Which sidebar tabs a non-Admin user can see/use is data-driven, stored in
# the RoleTabPermissions table (Role, TabKey) -> Allowed, configured by an
# Admin from Settings > Access Control. 'Admin' is never stored there - it
# always has access to every tab and isn't configurable away.
#
# ACCESS_TABS is the registry of every configurable tab: its key (matches
# RoleTabPermissions.TabKey and the URL-prefix routing table below), its
# label (shown in the Access Control matrix and nowhere else), and which
# group heading it's shown under in that matrix (mirrors the sidebar's own
# grouping in base.html, purely for readability there).
#
# Dashboard and Reports are configurable tabs like any other - and, per
# their entries below, are NOT granted to Staff/Supervisor/Manager by
# default (no seed row in RoleTabPermissions for them), same as every other
# tab. Admin is unaffected either way - user_can_access() always returns
# True for Admin regardless of what's configured here. See
# default_landing_url() for what a role with neither granted lands on
# instead, right after login.
ACCESS_TABS = [
    ("dashboard", "Dashboard", "General"),
    ("reports", "Reports", "General"),
    ("inventory", "Products & Stock", "Inventory"),
    ("suppliers", "Suppliers", "Purchasing"),
    ("purchases", "Purchases", "Purchasing"),
    ("customers", "Customers", "Sales"),
    ("sales", "Sales", "Sales"),
    ("sales_live", "Live Sales Monitor", "Sales"),
    ("stock_issues", "Stock Issues", "Sales"),
    ("stock_issues_report", "Stock Issue Schemes & Dues", "Sales"),
    ("targets", "Targets", "Sales"),
    ("location_tracking", "Location Tracking", "Sales"),
    ("expenses", "Operating Expenses", "Finance"),
    ("gst", "GST Filing", "Finance"),
    ("pnl", "Profit & Loss", "Finance"),
    ("scheme_claims", "Scheme Claims", "Finance"),
    ("accounts_receivable", "Accounts Receivable", "Finance"),
    ("vehicles", "Vehicles", "Fleet"),
    ("maintenance", "Maintenance", "Fleet"),
    ("attendance", "Leave & Attendance", "Payroll"),
    ("employees", "Employees", "Payroll"),
    ("salary", "Salary Schedule", "Payroll"),
    ("advances", "Advance Payments", "Payroll"),
    ("settings", "Company / GST Settings", "Setup"),
    ("custom_fields", "Custom Fields", "Setup"),
    ("dashboard_customize", "Customize Dashboard", "Setup"),
]


# "Manage Users" and "Access Control" itself are deliberately NOT
# configurable tabs - only Admin can ever manage other logins, reset
# passwords, or change Access Control (letting a non-Admin grant themselves
# Admin via a role dropdown would defeat the whole feature). Their routes
# carry a hard @admin_required regardless of anything set here.
ACCESS_TAB_KEYS = {key for key, _, _ in ACCESS_TABS}


ACCESS_TAB_LABELS = {key: label for key, label, _ in ACCESS_TABS}


CONFIGURABLE_ROLES = ["Staff", "Supervisor", "Manager"]  # Admin is deliberately excluded - always full access


ALL_ROLES = CONFIGURABLE_ROLES + ["Admin"]


# Maps a request path to the ACCESS_TABS key that governs it. Checked in
# order, first (most specific) prefix match wins - e.g. "/settings/custom-
# fields" must be checked before the bare "/settings" prefix. A path that
# matches nothing here (/account/change-password, /api/* helpers, etc.) is
# left unrestricted for any signed-in user. Note: /settings/access-control
# itself is intentionally left OUT of this table (and off the "settings"
# tab) - its route carries its own hard @admin_required, so it can never be
# handed to Staff/Supervisor/Manager no matter how "settings" is configured.
TAB_PATH_RULES = [
    ("/settings/access-control", None),  # always Admin-only, see admin_required on the route itself
    ("/settings/custom-fields", "custom_fields"),
    ("/settings", "settings"),
    ("/dashboard/customize", "dashboard_customize"),
    ("/dashboard", "dashboard"),
    ("/reports/stock-issues", "stock_issues_report"),
    ("/reports/sales-live", "sales_live"),
    ("/reports/pnl", "pnl"),
    ("/reports/accounts-receivable", "accounts_receivable"),
    ("/reports", "reports"),
    ("/inventory", "inventory"),
    ("/suppliers", "suppliers"),
    ("/purchases", "purchases"),
    ("/customers", "customers"),
    ("/sales", "sales"),
    ("/stock-issues", "stock_issues"),
    ("/targets", "targets"),
    ("/location-tracking", "location_tracking"),
    ("/expenses", "expenses"),
    ("/gst", "gst"),
    ("/scheme-claims", "scheme_claims"),
    ("/vehicles", "vehicles"),
    ("/maintenance", "maintenance"),
    ("/attendance", "attendance"),
    ("/employees", "employees"),
    ("/salary", "salary"),
    ("/advances", "advances"),
]


def _tab_key_for_path(path):
    for prefix, key in TAB_PATH_RULES:
        if path.startswith(prefix):
            return key
    return None


def get_role_permissions():
    """{Role: set(allowed TabKeys)} for the configurable roles, cached for
    the lifetime of this request (it's consulted once per sidebar item plus
    once in require_login() below, and the underlying table rarely changes)."""
    if not hasattr(g, "_role_tab_perms"):
        rows = db.query("SELECT Role, TabKey FROM RoleTabPermissions WHERE Allowed=1")
        perms = {role: set() for role in CONFIGURABLE_ROLES}
        for r in rows:
            perms.setdefault(r["Role"], set()).add(r["TabKey"])
        g._role_tab_perms = perms
    return g._role_tab_perms


def user_can_access(user, tab_key):
    """True if this user's role may use the given ACCESS_TABS tab. Admin
    always can; an unrecognized tab_key (None, or a typo) is always denied
    rather than silently allowed."""
    if not user or not tab_key:
        return False
    if user["Role"] == "Admin":
        return True
    return tab_key in get_role_permissions().get(user["Role"], set())


# Which route to send a user to for each ACCESS_TABS key, when default_landing_url()
# below needs to find the first tab a role actually has - covers every tab except
# "dashboard"/"reports" themselves (handled directly by default_landing_url).
TAB_HOME_ENDPOINT = {
    "inventory": "inventory_list", "suppliers": "suppliers_list", "purchases": "purchases_list",
    "customers": "customers_list", "sales": "sales_list", "sales_live": "sales_live_report",
    "stock_issues": "stock_issues_list", "stock_issues_report": "stock_issues_report",
    "targets": "targets_view", "location_tracking": "location_tracking_view",
    "expenses": "expenses_list", "gst": "gst_dashboard",
    "pnl": "pnl_report", "scheme_claims": "scheme_claims_list", "vehicles": "vehicles_list",
    "maintenance": "maintenance_list", "attendance": "attendance_month", "employees": "employees_list",
    "salary": "salary_month", "advances": "advances_list", "settings": "settings_form",
    "custom_fields": "custom_fields_admin", "dashboard_customize": "dashboard_customize",
}


def default_landing_url(user):
    """Where a signed-in user should land when no specific page was requested
    (right after login, visiting '/', or bounced back by an access-control
    check) - Dashboard if their role can see it, else Reports, else the
    first module tab their role has actually been granted, else the always-
    accessible Change Password page as a last resort (a role with nothing
    granted yet). Dashboard and Reports are NOT granted to Staff/Supervisor/
    Manager by default (see ACCESS_TABS) - only Admin always gets Dashboard,
    everyone else needs it explicitly enabled from Access Control."""
    if not user:
        return url_for("login")
    if user["Role"] == "Admin" or user_can_access(user, "dashboard"):
        return url_for("dashboard")
    if user_can_access(user, "reports"):
        return url_for("reports_hub")
    for key, _, _ in ACCESS_TABS:
        if key in ("dashboard", "reports"):
            continue
        if user_can_access(user, key) and key in TAB_HOME_ENDPOINT:
            return url_for(TAB_HOME_ENDPOINT[key])
    return url_for("change_password")
