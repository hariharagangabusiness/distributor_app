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
    db.init_db()
    conn = db.get_conn()
    added = add_column_if_missing(conn, "CompanySettings", "CreditBlockDays", "INTEGER NOT NULL DEFAULT 0")
    conn.commit()

    print("Migration complete.")
    if added:
        print("\n1 new column added.")
    else:
        print("\nNo new columns needed - already up to date.")
    print("\nWhat's new:")
    print("- Company / GST Settings now has a 'Block sales for overdue customers after (days)' field")
    print("  under Customer Credit Control. Leave it at 0 to keep the old behaviour (warning only,")
    print("  nothing blocked). Set it to e.g. 30 and a non-Admin can no longer save a NEW sale for a")
    print("  customer whose oldest unpaid invoice is more than 30 days overdue - Admin accounts can")
    print("  always save regardless.")
    print("- The New Sale and Edit Sale screens now show a red customer-name highlight plus a warning")
    print("  banner (outstanding balance amount) whenever the selected customer has any positive due.")


if __name__ == "__main__":
    run()
