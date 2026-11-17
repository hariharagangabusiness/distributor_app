"""One-time migration: adds Accounts Receivable payment reminders to an
existing installation.

- CompanySettings.ARReminderOverdueDays - how many days overdue (with no
  reminder logged in that same window) before an invoice/customer is
  flagged "reminder due". Admin-configurable from Company / GST Settings.
  Defaults to 7 for the existing row (there is only ever one).
- ARReminderLog - one row per Call/WhatsApp reminder actually sent from
  Accounts Receivable, against a single invoice or a customer's whole
  balance. Brand new table, so also declared directly in schema.sql - this
  just gets it onto an existing database without waiting for the next app
  restart.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_ar_reminders.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        did_work = False

        if not column_exists(conn, "CompanySettings", "ARReminderOverdueDays"):
            conn.execute("ALTER TABLE CompanySettings ADD COLUMN ARReminderOverdueDays INTEGER NOT NULL DEFAULT 7")
            conn.commit()
            print("Added CompanySettings.ARReminderOverdueDays.")
            did_work = True
        else:
            print("CompanySettings.ARReminderOverdueDays already exists - skipping that part.")

        if not table_exists(conn, "ARReminderLog"):
            conn.execute("""
                CREATE TABLE ARReminderLog (
                    ReminderLogID       INTEGER PRIMARY KEY AUTOINCREMENT,
                    CustomerID          INTEGER NOT NULL,
                    SaleID              INTEGER,
                    Channel             TEXT NOT NULL,
                    SentByUserID        INTEGER,
                    SentByUsername      TEXT,
                    SentAt              TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID),
                    FOREIGN KEY (SaleID) REFERENCES Sales(SaleID)
                )
            """)
            conn.commit()
            print("Created ARReminderLog table.")
            did_work = True
        else:
            print("ARReminderLog already exists - skipping that part.")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_arreminderlog_customer ON ARReminderLog(CustomerID)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_arreminderlog_sale ON ARReminderLog(SaleID)")
        conn.commit()

        if did_work:
            print("\nWhat's new:")
            print("- Accounts Receivable's Invoice-wise and Customer Summary tabs now have Call/")
            print("  WhatsApp buttons (using the customer's saved Phone) for nudging a payment -")
            print("  these open your own dialer/WhatsApp, nothing is sent automatically. An invoice/")
            print("  customer overdue past the configured threshold with no recent reminder is")
            print("  flagged 'Reminder due'.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
