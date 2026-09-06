"""One-time migration: adds to an EXISTING database, without touching any
data you've already entered:

  - Sales.EmployeeID - optional link to the salesperson a Sale is against,
    when it's fulfilled from stock already issued to them that day.
  - SaleStockIssueLinks - a new table tracking exactly how much of a Sale's
    quantity was credited against a Stock Issue's Qty Sold, so editing that
    Sale can precisely reverse and reapply the credit.

Together these let a Sale entered directly on the Sales tab (for a
salesperson who already has stock issued to them that day) automatically
count toward that Stock Issue's Qty Sold and show up on its Reconcile
screen, instead of the reconciler having to enter it by hand and instead of
the sale double-deducting stock that was already deducted when issued.

Safe to run multiple times. Safe to run even on a brand-new database
(init_db.py / seed_demo.py already include both via schema.sql, so this
becomes a no-op there).

Usage: python migrate_sale_stock_issue_link.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def table_exists(conn, table):
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def run():
    db.init_db()
    conn = db.get_conn()

    print("Checking Sales...")
    if not column_exists(conn, "Sales", "EmployeeID"):
        conn.execute("ALTER TABLE Sales ADD COLUMN EmployeeID INTEGER")
        print("  Added Sales.EmployeeID")
    else:
        print("  Sales.EmployeeID already exists - skipping")

    print("Checking SaleStockIssueLinks...")
    if not table_exists(conn, "SaleStockIssueLinks"):
        conn.execute("""CREATE TABLE SaleStockIssueLinks (
            LinkID            INTEGER PRIMARY KEY AUTOINCREMENT,
            SaleID            INTEGER NOT NULL,
            StockIssueLineID  INTEGER NOT NULL,
            QtyApplied        REAL NOT NULL,
            FOREIGN KEY (SaleID) REFERENCES Sales(SaleID),
            FOREIGN KEY (StockIssueLineID) REFERENCES StockIssueLines(LineID)
        )""")
        print("  Created SaleStockIssueLinks table")
    else:
        print("  SaleStockIssueLinks table already exists - skipping")

    conn.commit()
    conn.close()
    print("\nMigration complete. New capability now available:")
    print("  - The Sales form has an optional 'Salesperson' field. When set, and that")
    print("    salesperson has an open (not-yet-reconciled) Stock Issue for that sale's")
    print("    date, the sale's quantities are credited against that Stock Issue's Qty")
    print("    Sold automatically - it'll show up pre-filled on the Reconcile screen,")
    print("    and the stock won't be deducted a second time.")


if __name__ == "__main__":
    run()
