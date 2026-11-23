import logging
import os
import secrets
from datetime import timedelta

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

import blueprints
import core.context
import db
import gst_reminders

# Not used here: tests (and a few scripts) reach these as `app.<name>`.
from blueprints.admin import _within_location_tracking_hours, get_stock_returns_detail, get_visit_plan  # noqa: F401
from blueprints.auth import LOGIN_MAX_ATTEMPTS, _login_failures  # noqa: F401
from blueprints.customers import CUSTOMERS_LIST_PAGE_SIZE, _build_customer_ledger, _customer_group_ids, _ledger_sales_for_group, customer_parent_candidates, validate_customer_parent  # noqa: F401
from blueprints.inventory import get_unassigned_customer_id, product_qty_sold_for_issue  # noqa: F401
from blueprints.payroll import ADVANCES_LIST_PAGE_SIZE, EMPLOYEES_LIST_PAGE_SIZE, VEHICLES_LIST_PAGE_SIZE  # noqa: F401
from blueprints.purchases import PURCHASES_LIST_PAGE_SIZE, SUPPLIERS_LIST_PAGE_SIZE  # noqa: F401
from blueprints.reports_gst import _ar_invoice_pdf_row, _ar_phone_e164, get_ar_base_rows, get_ar_customer_summary, get_ar_invoice_rows  # noqa: F401
from blueprints.sales import SALES_LIST_PAGE_SIZE, check_credit_block, log_sale_location, resolve_sale_customer  # noqa: F401
from core.helpers import DEFAULT_LOCATION_WORKING_DAYS, SaleStockIssueLockedError, already_direct_deducted_qty, create_sale, get_customer_due_info, get_effective_columns, get_financial_year_label, get_stock_returns_summary, next_customer_account_number, next_stock_issue_number, stock_issue_post_line, true_product_qty_sold  # noqa: F401
from formatting import indian_number_format  # noqa: F401

# Structured logging to stderr, which Railway (and any other host running
# this under gunicorn/systemd) captures into its own log viewer - previously
# nothing in this app called `logging` at all, so an unhandled error at 2am
# left no trace anywhere except a user eventually reporting it. Format
# mirrors gunicorn's own access-log style (timestamp, level, message) so
# both interleave readably in the same log stream.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("distributor_app")

app = Flask(__name__)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_KEY_FILE = os.path.join(BASE_DIR, "secret_key.txt")


def _load_or_create_secret_key():
    """Session cookies are signed with this key - it must stay the same
    across restarts (or everyone gets logged out) and must stay private
    (anyone with it could forge a login). Preferred source is the SECRET_KEY
    environment variable (set this in production - e.g. Railway - since a
    platform's filesystem is typically wiped on every redeploy, which would
    otherwise silently rotate the key and log everyone out). Falls back to
    generating one on first run and keeping it in secret_key.txt next to
    app.py, which is fine for local/dev use on a persistent disk; don't
    share that file or check it into version control."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
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

# Railway (like most hosts) puts this app behind its own reverse proxy, so
# every request's real remote_addr is that proxy's IP, not the visitor's -
# ProxyFix reads the one hop of X-Forwarded-For that proxy adds and swaps it
# in, so request.remote_addr (used below for login rate-limiting) reflects
# the actual client instead of "everyone looks like the same IP."
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# CSRF protection: every POST form must submit a token proving it was rendered
# by this app for this signed-in session, not from a malicious page tricking a
# logged-in browser into submitting a request here. Every form template
# carries a hidden csrf_token() field (Flask-WTF injects that helper once this
# is initialized). The one JS-driven POST that isn't a form submit
# (/api/location-ping) sends the same token in an X-CSRFToken header, read from
# the <meta name="csrf-token"> tag in base.html. The token's one-hour default
# expiry is disabled below: a staff phone keeps one tab open all shift, and an
# expiring token would silently stop their location pings; it's still tied to
# the signed session, so it dies when the session does.
app.config["WTF_CSRF_TIME_LIMIT"] = None
csrf = CSRFProtect(app)
core.context.register(app)
blueprints.init_app(app)


# Runs on import, not just under `python app.py` - a production WSGI server
# (gunicorn, per the Procfile) imports this module and calls the `app`
# object directly, it never executes the __main__ block below, so anything
# needed at boot (creating tables, starting the reminder thread) has to live
# here instead of down there.
db.init_db()  # safe: CREATE TABLE IF NOT EXISTS - never touches existing data

# FLASK_DEBUG=1 opts into Flask's debug reloader + interactive debugger for
# local dev; leave it unset (the default) anywhere internet-facing, since
# Flask's debugger allows remote code execution from any page that hits an
# unhandled error. Set on app.config here (rather than only passed to
# app.run() below) so it's correct at *this* point too, for the reminder-
# thread guard right after - not just once app.run() eventually applies it.
app.debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")

# Flask's debug reloader re-executes this file in a second process;
# WERKZEUG_RUN_MAIN is only set in that second (actually-serving) one, so
# this guard stops the reminder thread starting twice (and double-sending
# every email) when running locally with the reloader on. Under gunicorn
# WERKZEUG_RUN_MAIN is never set, so `not app.debug` (true in production)
# takes over and it starts normally, once per worker process - keep
# `web: gunicorn app:app --workers 1 ...` in the Procfile (see
# RAILWAY_DEPLOY.md) so that's once total, not once per worker.
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    gst_reminders.start_background_reminder_thread()

if __name__ == "__main__":
    # Local/dev convenience only - production runs via gunicorn (see Procfile),
    # which never executes this block.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=app.debug)
