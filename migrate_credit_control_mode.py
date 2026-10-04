"""One-time migration: adds CompanySettings.CreditControlMode.

Replaces the bare CreditBlockDays number (added in migrate_credit_block_days.py)
with an explicit 3-way Admin choice: 'Informational' (never block, warning
only - the default), 'BlockAfterDays' (block once overdue past
CreditBlockDays days), or 'BlockImmediate' (block immediately on any
positive due, no grace period). CreditBlockDays itself is unchanged and
still used when the mode is 'BlockAfterDays'.

If an Admin had already set CreditBlockDays > 0 before this mode switch
existed, this migration preserves that behavior by defaulting their mode to
'BlockAfterDays' rather than silently turning the block off.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_credit_control_mode.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if column_exists(conn, "CompanySettings", "CreditControlMode"):
            print("CompanySettings.CreditControlMode already exists - nothing to do.")
            return
        conn.execute("ALTER TABLE CompanySettings ADD COLUMN CreditControlMode TEXT NOT NULL DEFAULT 'Informational'")
        conn.execute("UPDATE CompanySettings SET CreditControlMode='BlockAfterDays' WHERE CreditBlockDays > 0")
        conn.commit()
        print("Added CompanySettings.CreditControlMode.")
        print("\nWhat's new:")
        print("- Company / GST Settings' Customer Credit Control section now has an explicit mode choice:")
        print("  Informational only / Block after N days overdue / Block immediately on any due.")
        print("- Any install that already had a CreditBlockDays > 0 configured was switched to")
        print("  'Block after N days overdue' automatically, so existing behaviour is unchanged.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
