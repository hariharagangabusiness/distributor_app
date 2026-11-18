"""One-time migration: adds the daily-reminder-cadence setting for badly
overdue Accounts Receivable invoices to an existing installation.

- CompanySettings.ARReminderDailyAfterDays - once an invoice is overdue past
  this many days, "Reminder due" is re-flagged every day instead of once
  every ARReminderOverdueDays. Admin-configurable from Company / GST
  Settings. Defaults to 14 for the existing row (there is only ever one).

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_ar_reminder_daily.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if not column_exists(conn, "CompanySettings", "ARReminderDailyAfterDays"):
            conn.execute(
                "ALTER TABLE CompanySettings ADD COLUMN ARReminderDailyAfterDays INTEGER NOT NULL DEFAULT 14")
            conn.commit()
            print("Added CompanySettings.ARReminderDailyAfterDays.")
            print("\nWhat's new:")
            print("- An Accounts Receivable invoice overdue past 14 days (configurable under Company /")
            print("  GST Settings) is now flagged 'Reminder due' every day until it's paid, instead of")
            print("  only once every ARReminderOverdueDays - and a customer's Customer Summary badge now")
            print("  always matches whether any of their own invoices on the Invoice-wise tab are due.")
        else:
            print("CompanySettings.ARReminderDailyAfterDays already exists - nothing to do.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
