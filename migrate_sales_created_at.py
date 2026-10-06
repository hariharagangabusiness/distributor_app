"""One-time migration: adds Sales.CreatedAt.

Set once, automatically, the moment a Sale is first created (see
create_sale() in app.py) - never touched again on a later edit, unlike
SaleDate which the salesperson can pick/backdate freely. Stored in IST.

This cannot be backfilled for any Sale created before this column existed -
that history genuinely isn't recorded anywhere in the database (SaleDate
was always just a plain, user-entered calendar date, never an automatic
timestamp). Existing rows are left NULL rather than given a fabricated
value. Going forward, every new Sale gets a real creation time.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_sales_created_at.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if column_exists(conn, "Sales", "CreatedAt"):
            print("Sales.CreatedAt already exists - nothing to do.")
            return
        conn.execute("ALTER TABLE Sales ADD COLUMN CreatedAt TEXT")
        conn.commit()
        print("Added Sales.CreatedAt.")
        print("\nWhat's new:")
        print("- Every new Sale now records the exact IST date+time it was created, automatically -")
        print("  shown on the Sale View page as '(booked HH:MM IST)' next to the Sale Date.")
        print("- Existing Sales (created before this migration) show nothing there - that information")
        print("  was never recorded and can't be recovered from the database.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
