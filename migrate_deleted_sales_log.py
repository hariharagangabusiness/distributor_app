"""Adds DeletedSalesLog - a snapshot table capturing what a Sale looked
like right before an Admin permanently deleted it (sale_delete()), since
the Sale/SalesLines rows themselves are removed with no other trace. Once
this migration runs, every future Sale deletion is recorded here (who,
when, invoice, customer, amount) and the new 'Deleted Sales' report can
answer questions like 'what did admin delete today'. Deletions from
BEFORE this migration are not recoverable - there was nowhere for that
data to have been kept. Safe to run more than once."""
import sqlite3
from db import DB_PATH


def table_exists(conn, table):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


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
                    DeletedAt       TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX idx_deletedsaleslog_deletedat ON DeletedSalesLog(DeletedAt)")
            conn.commit()
            print("Created DeletedSalesLog table + index.")
        else:
            print("DeletedSalesLog already exists - nothing to do.")
    finally:
        conn.close()

    print("""
Migration complete. New capability now available:
  - Every time an Admin permanently deletes a Sale from now on, a snapshot
    (invoice #, customer, date, salesperson, total, who deleted it, when)
    is saved to DeletedSalesLog before the Sale itself is removed.
  - A new 'Deleted Sales' report (Reports > Deleted Sales, Admin only)
    lets you see what was deleted and by whom, filterable by date range.
  - This does NOT retroactively recover anything deleted before this
    migration ran - that data was never kept anywhere.
""")


if __name__ == "__main__":
    main()
