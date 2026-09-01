"""GST due-date email reminders.

Runs as a lightweight background thread inside the running app process -
there's no external cron in this app's design, so email reminders only
fire while `python app.py` is running (same as the rest of the app; see
the README section on keeping it running on an always-on office PC).
In-app dashboard alerts (the other half of the "Both" reminder choice)
don't need this thread — they're computed fresh on every page load from
gst_logic.upcoming_due_dates(), same pattern as every other dashboard
alert in this app.
"""
import smtplib
import threading
import time
from datetime import timedelta
from email.mime.text import MIMEText

import db
import gst_logic


def _send(company, recipients, msg):
    host = company["SmtpHost"]
    port = int(company["SmtpPort"] or 587)
    with smtplib.SMTP(host, port, timeout=20) as server:
        if company["SmtpUseTLS"]:
            server.starttls()
        if company["SmtpUsername"]:
            server.login(company["SmtpUsername"], company["SmtpPassword"] or "")
        server.sendmail(company["SmtpFromEmail"] or company["SmtpUsername"], recipients, msg.as_string())


def send_test_email(company, to_address):
    """Sends a one-off test email using the SMTP settings in `company`.
    Returns (ok, message). Used by the settings page's 'Send test email'
    button — never called from the background thread."""
    if not company["SmtpHost"] or not company["SmtpFromEmail"]:
        return False, "Set SMTP Host and 'From' Email on this page first, then save before testing."
    msg = MIMEText("This is a test email from the GST Filing reminders in your Distributor Ops app. "
                    "If you're reading this, your email reminder settings are working.")
    msg["Subject"] = "Distributor Ops — GST reminder test email"
    msg["From"] = company["SmtpFromEmail"]
    msg["To"] = to_address
    try:
        _send(company, [to_address], msg)
        return True, f"Test email sent to {to_address}."
    except Exception as e:
        return False, f"Could not send test email: {e}"


def check_and_send_due_reminders():
    """Looks at upcoming GST due dates; for any due within the configured
    lead time that this exact (return type, period, due date) hasn't
    already been emailed for (per GstReminderLog), sends one email
    covering all of them together and logs it so it's never sent twice."""
    company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
    if not company or not company["GstRemindersEnabled"]:
        return
    recipients = [e.strip() for e in (company["GstReminderEmails"] or "").split(",") if e.strip()]
    if not recipients or not company["SmtpHost"]:
        return

    days_before = company["GstReminderDaysBefore"] or 3
    items = gst_logic.upcoming_due_dates(company, days_ahead=days_before)
    to_send = []
    for item in items:
        already = db.query("SELECT 1 FROM GstReminderLog WHERE ReturnType=? AND Period=? AND DueDate=?",
                            (item["return_type"], item["period"], item["due_date"].isoformat()), one=True)
        if not already:
            to_send.append(item)
    if not to_send:
        return

    lines = ["Upcoming GST due date(s), from your Distributor Ops app:", ""]
    for item in to_send:
        lines.append(f"- {item['return_type']} ({item['period']}): due {item['due_date'].strftime('%d %b %Y')} "
                      f"— {item['description']}")
    lines.append("")
    lines.append("This is a computed reminder, not a filing. Please file the actual return on the GST portal "
                  "(services.gst.gov.in). Due dates are set by the Government of India and are subject to "
                  "change — confirm on the portal if in doubt. This isn't a substitute for advice from your "
                  "tax professional.")
    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = f"GST reminder: {len(to_send)} return(s) due soon"
    msg["From"] = company["SmtpFromEmail"] or company["SmtpUsername"] or ""
    msg["To"] = ", ".join(recipients)

    try:
        _send(company, recipients, msg)
    except Exception:
        return  # transient SMTP failure — the next periodic check will retry; nothing logged as sent

    now = db.query("SELECT datetime('now') AS n", one=True)["n"]
    for item in to_send:
        db.execute("""INSERT OR IGNORE INTO GstReminderLog (ReturnType, Period, DueDate, SentAt, Recipients)
                    VALUES (?,?,?,?,?)""",
                   (item["return_type"], item["period"], item["due_date"].isoformat(), now, ", ".join(recipients)))


def start_background_reminder_thread(check_interval_seconds=3600):
    """Starts a daemon thread that checks for due reminders immediately
    and then every `check_interval_seconds` (default: hourly). Call this
    once; app.py guards the call against Flask's debug-mode reloader
    starting the process twice."""
    def loop():
        while True:
            try:
                check_and_send_due_reminders()
            except Exception:
                pass  # a reminder-check error must never crash the app
            time.sleep(check_interval_seconds)
    t = threading.Thread(target=loop, daemon=True, name="gst-reminder-thread")
    t.start()
    return t
