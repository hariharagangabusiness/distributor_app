"""One-time migration for two features:

1. Reconciled Stock Issues now auto-create a real GST Sale for the day's
   sold units, billed to a single system "Unassigned" customer, so it
   counts in GST Filing/P&L/Sales reports like any other sale - until you
   use "Reassign" on it to split those lines out to the actual
   customer(s), typed in directly on the same page or picked from your
   existing customer list. Adds:
     - Customers.IsUnassignedBucket (flags the one system customer row -
       created automatically the first time it's needed, not by this
       migration)
     - StockIssues.SaleID (links a reconciled issue to the Sale it
       created)

2. The Salary Schedule gets a "Refresh" button that re-pulls any advances
   given since the schedule was generated and recalculates pay pro-rated
   by days actually worked (using each employee's Join Date, attendance,
   and leave policy), for any salary row not yet marked Paid. This uses
   the Join Date column that already exists on Employees - no schema
   change needed for this part.

Nothing existing is dropped, renamed, or overwritten. Safe to run
multiple times.

Usage: python migrate_unassigned_sales_and_salary.py
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
    added = 0
    if add_column_if_missing(conn, "Customers", "IsUnassignedBucket", "INTEGER NOT NULL DEFAULT 0"):
        added += 1
    if add_column_if_missing(conn, "StockIssues", "SaleID", "INTEGER"):
        added += 1
    conn.commit()

    print("Migration complete.")
    if added:
        print(f"\n{added} new column(s) added.")
    else:
        print("\nNo new columns needed - already up to date.")
    print("\nWhat's new:")
    print("- Reconciling a Stock Issue now also creates a GST Sale for the day's sold units, billed to")
    print("  a system 'Unassigned' customer - it now counts in GST Filing, P&L, and Sales reports.")
    print("  A warning on the Dashboard shows how many Unassigned sales are waiting to be reassigned.")
    print("- On a Sale billed to 'Unassigned', use Reassign to split its lines out to the real")
    print("  customer(s) - type a new customer's details directly on that page, or pick an existing one.")
    print("- The Salary Schedule has a 'Refresh' button (Admin only) that re-pulls active advances and")
    print("  recalculates pay pro-rated by days worked (Join Date, attendance, leave policy) for any")
    print("  salary row that isn't marked Paid yet.")


if __name__ == "__main__":
    run()
