"""One-time migration for the GST Filing feature (GSTR-1 / GSTR-2B /
GSTR-3B reference reports, due-date reminders).

This does NOT delete or change any of your existing data. It only:
  - Adds GST columns to Purchases and PurchaseLines (SupplierGSTIN,
    tax rate/amount breakup, etc.) - mirrors the GST columns Sales and
    SalesLines already have, so GSTR-3B can work out an "ITC as per our
    purchase records" figure. Purchases you already entered keep every
    value they had; these new columns simply start at 0/blank on those
    old rows, exactly like every other migration in this app. They won't
    be counted in GST reports unless you re-enter them - there's no
    "edit an existing purchase" screen in this app (same as Sales), so
    this only affects purchases entered from now on.
  - Adds GST-filing-scheme and email-reminder columns to CompanySettings
    (defaults: Monthly scheme, reminders off until you turn them on and
    fill in email/SMTP settings on the Company / GST Settings page).
  - Creates two new tables: Gstr2bUploads (stores each GSTR-2B Excel file
    you upload plus its parsed ITC totals) and GstReminderLog (keeps track
    of which reminder emails have already been sent, so the same due date
    is never emailed twice).

Nothing existing is dropped, renamed, or overwritten. Safe to run
multiple times.

Usage: python migrate_gst_filing.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def add_column_if_missing(conn, table, column, ddl):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        print(f"  Added {table}.{column}")
        return True
    return False


def run():
    db.init_db()  # ensures Gstr2bUploads/GstReminderLog exist (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()
    added = 0

    # --- Purchases: GST breakup columns (mirrors Sales) -----------------
    purchase_columns = [
        ("SupplierGSTIN", "TEXT"),
        ("SupplierStateCode", "TEXT"),
        ("IsInterState", "INTEGER NOT NULL DEFAULT 0"),
        ("TaxableAmount", "REAL NOT NULL DEFAULT 0"),
        ("CGSTAmount", "REAL NOT NULL DEFAULT 0"),
        ("SGSTAmount", "REAL NOT NULL DEFAULT 0"),
        ("IGSTAmount", "REAL NOT NULL DEFAULT 0"),
        ("RoundOff", "REAL NOT NULL DEFAULT 0"),
        ("ReverseCharge", "INTEGER NOT NULL DEFAULT 0"),
        ("ITCEligible", "INTEGER NOT NULL DEFAULT 1"),
    ]
    for col, ddl in purchase_columns:
        if add_column_if_missing(conn, "Purchases", col, ddl):
            added += 1

    # --- PurchaseLines: GST breakup columns (mirrors SalesLines) --------
    purchase_line_columns = [
        ("HSNCode", "TEXT"),
        ("GSTRate", "REAL NOT NULL DEFAULT 0"),
        ("TaxableValue", "REAL NOT NULL DEFAULT 0"),
        ("CGSTRate", "REAL NOT NULL DEFAULT 0"),
        ("CGSTAmount", "REAL NOT NULL DEFAULT 0"),
        ("SGSTRate", "REAL NOT NULL DEFAULT 0"),
        ("SGSTAmount", "REAL NOT NULL DEFAULT 0"),
        ("IGSTRate", "REAL NOT NULL DEFAULT 0"),
        ("IGSTAmount", "REAL NOT NULL DEFAULT 0"),
    ]
    for col, ddl in purchase_line_columns:
        if add_column_if_missing(conn, "PurchaseLines", col, ddl):
            added += 1

    # --- CompanySettings: GST scheme + reminder/SMTP settings -----------
    settings_columns = [
        ("GstFilingScheme", "TEXT NOT NULL DEFAULT 'Monthly'"),
        ("GstRemindersEnabled", "INTEGER NOT NULL DEFAULT 0"),
        ("GstReminderEmails", "TEXT"),
        ("GstReminderDaysBefore", "INTEGER NOT NULL DEFAULT 3"),
        ("SmtpHost", "TEXT"),
        ("SmtpPort", "INTEGER NOT NULL DEFAULT 587"),
        ("SmtpUsername", "TEXT"),
        ("SmtpPassword", "TEXT"),
        ("SmtpFromEmail", "TEXT"),
        ("SmtpUseTLS", "INTEGER NOT NULL DEFAULT 1"),
    ]
    for col, ddl in settings_columns:
        if add_column_if_missing(conn, "CompanySettings", col, ddl):
            added += 1

    conn.commit()
    conn.close()

    print("\nMigration complete.")
    if added:
        print(f"{added} new column(s) added. Every existing Purchase, Sale, and setting keeps its old values -")
        print("the new GST columns on Purchases simply start at 0 until you record new purchases.")
    else:
        print("Nothing to do - this database already has the GST Filing columns/tables.")
    print("\nWhat's new: a 'GST Filing' section in the sidebar with computed GSTR-1 and GSTR-3B")
    print("summaries by month, a place to upload your GSTR-2B Excel download for comparison, and")
    print("due-date reminders. Configure the filing scheme (Monthly/QRMP) and, if you want email")
    print("reminders, the recipient list and SMTP settings, on the Company / GST Settings page.")


if __name__ == "__main__":
    run()
