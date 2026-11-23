"""App-wide Flask hooks: template filters, error handler, login gate, context processors."""
import gst_logic
import logging
from core.auth import PUBLIC_ENDPOINTS, _tab_key_for_path, default_landing_url, get_current_user, user_can_access
from core.helpers import LIST_VIEW_TOGGLE_ENABLED, LOCATION_TRACKED_ROLES, LOCATION_WEEKDAY_ABBR, MOBILE_FRIENDLY_UI_ENABLED, _location_working_days, employees_missing_salary_record, expiring_documents, format_date_dmy, get_company_settings, low_stock_products, over_stock_products, pending_salary_this_month, sale_balance_due, sale_due_amount, upcoming_maintenance
from datetime import date
from flask import flash, redirect, request, session, url_for
from formatting import indian_number_format
from werkzeug.exceptions import HTTPException

logger = logging.getLogger("distributor_app")


def inr_filter(value, decimals=2):
    """₹ + Indian-style grouped number, e.g. 862000 -> ₹8,62,000.00."""
    return "₹" + indian_number_format(value, decimals)


def inrn_filter(value, decimals=0):
    """Indian-style grouped number with no currency symbol, e.g. for quantities/
    counts: 862000 -> 8,62,000."""
    return indian_number_format(value, decimals)


def dmy_filter(value):
    return format_date_dmy(value)


def handle_unexpected_error(e):
    # HTTPException covers Flask/Werkzeug's own intentional responses (404 Not
    # Found, 403 Forbidden, a CSRF 400, etc.) - those already carry the right
    # status code and message, so just let Flask render them normally rather
    # than logging every routine 404 as if it were a crash.
    if isinstance(e, HTTPException):
        return e
    # Everything else is a genuine unhandled bug. Logged with the full
    # traceback plus who/what triggered it, so a night-time failure leaves a
    # real trace in Railway's logs instead of only surfacing if/when a user
    # happens to report it - this was the single biggest observability gap
    # this app had (see the architecture review: multi-day investigations
    # into "why did this number look wrong" that a log line would have
    # answered immediately). Deliberately doesn't extend base.html for the
    # response itself: that template depends on current_user/can_access via
    # a context processor and other app state, which is exactly the kind of
    # thing that might ALSO be broken during a genuine crash - the error
    # page has to render with zero dependencies on anything that could fail.
    try:
        user = get_current_user()
        who = user["Username"] if user else "anonymous"
    except Exception:
        who = "unknown"
    logger.exception("Unhandled exception on %s %s (user=%s)", request.method, request.path, who)
    return (
        "<!DOCTYPE html><html><head><title>Something went wrong</title></head>"
        "<body style=\"font-family:sans-serif;max-width:640px;margin:4rem auto;padding:0 1rem\">"
        "<h1>Something went wrong</h1>"
        "<p>An unexpected error occurred and has been logged. Please try again, "
        "or contact an Admin if this keeps happening.</p>"
        "<p><a href=\"/\">Return to the home page</a></p>"
        "</body></html>",
        500,
    )


def require_login():
    if request.endpoint is None:
        return  # unmatched routes fall through to the normal 404 handling
    endpoint = request.endpoint.rsplit(".", 1)[-1]  # "auth.login" -> "login" (blueprint prefix stripped)
    if endpoint in PUBLIC_ENDPOINTS:
        return
    user = get_current_user()
    if not user:
        return redirect(url_for("login", next=request.path))
    # Location-tracking consent: a hard gate, not a dismissible banner - a tracked-role
    # user can't reach ANYTHING else until they accept it, every single login (the
    # session flag is set only by location_consent()'s POST, and session.clear() at
    # login already wipes it, so this is naturally re-asked each time they sign in).
    # ("logout" never reaches here at all - it's already in PUBLIC_ENDPOINTS above -
    # so declining consent always leaves a way out without a redirect loop.)
    if (user["Role"] in LOCATION_TRACKED_ROLES and not session.get("location_consent_ok")
            and endpoint != "location_consent"):
        return redirect(url_for("location_consent", next=request.path))
    if user["Role"] != "Admin":
        tab_key = _tab_key_for_path(request.path)
        if tab_key and not user_can_access(user, tab_key):
            flash("You don't have access to that section. Ask an Admin to enable it for your "
                  "role under Settings › Access Control.", "error")
            return redirect(default_landing_url(user))


def inject_current_user():
    user = get_current_user()
    return dict(current_user=user, can_access=lambda tab_key: user_can_access(user, tab_key))


def inject_sale_due_helpers():
    return dict(sale_due_amount=sale_due_amount, sale_balance_due=sale_balance_due)


def inject_mobile_ui_flag():
    return dict(MOBILE_FRIENDLY_UI_ENABLED=MOBILE_FRIENDLY_UI_ENABLED)


def inject_list_view_toggle_flag():
    return dict(LIST_VIEW_TOGGLE_ENABLED=LIST_VIEW_TOGGLE_ENABLED)


def inject_location_tracking_working_days():
    """For base.html's location-tracking.js - only computed for a tracked role
    (an extra CompanySettings query otherwise unused by everyone else)."""
    user = get_current_user()
    if user and user["Role"] in LOCATION_TRACKED_ROLES:
        company = get_company_settings()
        configured = _location_working_days(company)
        return dict(location_working_days=[d for d in LOCATION_WEEKDAY_ABBR if d in configured],
                    location_ping_interval_minutes=company["LocationPingIntervalMinutes"] or 30)
    return dict(location_working_days=[], location_ping_interval_minutes=30)


def inject_current_date():
    return dict(current_date=date.today().strftime("%d-%m-%Y"))


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


def register(app):
    app.template_filter("inr")(inr_filter)
    app.template_filter("inrn")(inrn_filter)
    app.template_filter("dmy")(dmy_filter)
    app.errorhandler(Exception)(handle_unexpected_error)
    app.before_request(require_login)
    app.context_processor(inject_current_user)
    app.context_processor(inject_sale_due_helpers)
    app.context_processor(inject_mobile_ui_flag)
    app.context_processor(inject_list_view_toggle_flag)
    app.context_processor(inject_location_tracking_working_days)
    app.context_processor(inject_current_date)
    app.context_processor(inject_alert_counts)
