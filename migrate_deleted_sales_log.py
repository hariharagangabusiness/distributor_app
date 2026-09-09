"""Adds DeletedSalesLog - a snapshot table capturing what a Sale looked
like right before an Admin permanently deleted it (sale_delete()), since
the Sale/SalesLines rows themselves are removed with no other trace. Once
this migration runs, every future Sale deletion is recorded here (who,
when, invoice, customer, amount, and why) and the new 'Deleted Sales'
report can answer questions like 'what did admin delete today'. Deletions
from BEFORE this migration are not recoverable - there was nowhere for
that data to have been kept.

Also adds the Reason column (a required note typed on the delete-
confirmation page explaining why) to an existing DeletedSalesLog table
that predates it, so this same script covers both a first-time install
and an upgrade from the earlier reason-less version. Safe to run more
than once either way."""
import sqlite3
from db import DB_PATH


def table_exists(conn, table):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if not table_exists(conn, "DeletedSalesLog"):
            conn.execute("""
                CREATE TABLE DeletedSalesLog (
                    LogID           INTEGER PRIMARY KEY AUTOINCREMENT,
                    SaleID          INTEGER NOT NULL,
                    InvoiceNumber   TEXT NOT NULL,
                    CustomerName    TEXT,
                    SaleDate        TEXT,
                    EmployeeName    TEXT,
                    TotalAmount     REAL NOT NULL DEFAULT 0,
                    LineCount       INTEGER NOT NULL DEFAULT 0,
                    DeletedByUserID   INTEGER,
                    DeletedByUsername TEXT,
                    Reason          TEXT,
                    DeletedAt       TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX idx_deletedsaleslog_deletedat ON DeletedSalesLog(DeletedAt)")
            conn.commit()
            print("Created DeletedSalesLog table + index (including Reason column).")
        else:
            print("DeletedSalesLog already exists.")
            if not column_exists(conn, "DeletedSalesLog", "Reason"):
                conn.execute("ALTER TABLE DeletedSalesLog ADD COLUMN Reason TEXT")
                conn.commit()
                print("Added Reason column to existing DeletedSalesLog table.")
            else:
                print("Reason column already present - nothing to do.")
    finally:
        conn.close()

    print("""
Migration complete. New capability now available:
  - Every time an Admin permanently deletes a Sale from now on, a snapshot
    (invoice #, customer, date, salesperson, total, who deleted it, when,
    and the reason they typed) is saved to DeletedSalesLog before the
    Sale itself is removed.
  - A new 'Deleted Sales' report (Reports > Deleted Sales, Admin only)
    lets you see what was deleted, by whom, and why, filterable by date
    range.
  - This does NOT retroactively recover anything deleted before this
    migration ran - that data was never kept anywhere. Sales deleted
    between the original migration and this one will show a blank Reason
    (they didn't have a reason field yet).
""")


if __name__ == "__main__":
    main()
