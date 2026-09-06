"""One-time migration: adds to an EXISTING database, without touching any
data you've already entered:

  - Users.EmployeeID - optional link from a login account to an Employee
    record, so a Staff/Supervisor's Sales-tab entries can auto-pick (and
    lock) the Salesperson to themselves.
  - StockIssues.ReviewStatus - 'Reviewed' (default, unchanged behavior) or
    'Pending' for a Stock Issue that was auto-created behind a
    salesperson's Sales-tab entry (because they had none open for that
    day yet). A 'Pending' issue must be approved by a Manager/Admin before
    it can be reconciled.
  - Sales.CashAmount / Sales.BankAmount - breakdown of AmountReceived into
    how much was collected as physical cash vs. bank/UPI/card, for every
    sale (mirrors the existing Stock Issue reconciliation Cash/Bank split).

Safe to run multiple times. Safe to run even on a brand-new database
(init_db.py / seed_demo.py already include all of this via schema.sql, so
this becomes a no-op there).

Usage: python migrate_salesperson_auto_features.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def run():
    db.init_db()
    conn = db.get_conn()

    print("Checking Users...")
    if not column_exists(conn, "Users", "EmployeeID"):
        conn.execute("ALTER TABLE Users ADD COLUMN EmployeeID INTEGER")
        print("  Added Users.EmployeeID")
    else:
        print("  Users.EmployeeID already exists - skipping")

    print("Checking StockIssues...")
    if not column_exists(conn, "StockIssues", "ReviewStatus"):
        conn.execute("ALTER TABLE StockIssues ADD COLUMN ReviewStatus TEXT NOT NULL DEFAULT 'Reviewed'")
        print("  Added StockIssues.ReviewStatus")
    else:
        print("  StockIssues.ReviewStatus already exists - skipping")

    print("Checking Sales...")
    if not column_exists(conn, "Sales", "CashAmount"):
        conn.execute("ALTER TABLE Sales ADD COLUMN CashAmount REAL NOT NULL DEFAULT 0")
        print("  Added Sales.CashAmount")
    else:
        print("  Sales.CashAmount already exists - skipping")
    if not column_exists(conn, "Sales", "BankAmount"):
        conn.execute("ALTER TABLE Sales ADD COLUMN BankAmount REAL NOT NULL DEFAULT 0")
        print("  Added Sales.BankAmount")
    else:
        print("  Sales.BankAmount already exists - skipping")

    # Backfill: for existing sales, put the full AmountReceived into CashAmount
    # so historical totals still tally (better than leaving it split as 0/0).
    conn.execute("""UPDATE Sales SET CashAmount = AmountReceived
                     WHERE CashAmount = 0 AND BankAmount = 0 AND AmountReceived != 0""")

    conn.commit()
    conn.close()
    print("\nMigration complete. New capabilities now available:")
    print("  - Manage Users can link a login account to an Employee (Settings > Manage Users).")
    print("    A Staff/Supervisor account linked this way will have its Sales-tab Salesperson")
    print("    field auto-locked to itself, and a Stock Issue will be auto-created (flagged")
    print("    'Pending Review') if none is open yet for that salesperson/day.")
    print("  - The Sales form now records a Cash / Bank split for every sale.")
    print("  - Auto-created Stock Issues need Manager/Admin approval before they can be")
    print("    reconciled (Stock Issues list will show a 'Pending Review' badge/filter).")


if __name__ == "__main__":
    run()
