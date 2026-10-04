"""One-time migration: adds CompanySettings.CreditBlockDays.

Lets an Admin set how many days a customer's oldest unpaid invoice can go
overdue before a non-Admin is blocked from saving a NEW sale for that
customer (0 = off, the default - only the informational "customer has
dues" warning shows, nothing is blocked). Admin accounts always bypass the
block regardless of this setting.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_credit_block_days.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if column_exists(conn, "CompanySettings", "CreditBlockDays"):
            print("CompanySettings.CreditBlockDays already exists - nothing to do.")
            return
        conn.execute("ALTER TABLE CompanySettings ADD COLUMN CreditBlockDays INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        print("Added CompanySettings.CreditBlockDays.")
        print("\nWhat's new:")
        print("- Company / GST Settings now has a 'Block sales for overdue customers after (days)' field")
        print("  under Customer Credit Control. Leave it at 0 to keep the old behaviour (warning only,")
        print("  nothing blocked). Set it to e.g. 30 and a non-Admin can no longer save a NEW sale for a")
        print("  customer whose oldest unpaid invoice is more than 30 days overdue - Admin accounts can")
        print("  always save regardless.")
        print("- The New Sale and Edit Sale screens now show a red customer-name highlight plus a warning")
        print("  banner (outstanding balance amount) whenever the selected customer has any positive due.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
