"""Adds the StockIssueAuditLog table (who created/edited a Stock Issue's
product lines, and when) to an existing installation. Safe to re-run."""
import sqlite3
from db import DB_PATH


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if table_exists(conn, "StockIssueAuditLog"):
            print("StockIssueAuditLog already exists - nothing to do.")
            return
        conn.execute("""
            CREATE TABLE IF NOT EXISTS StockIssueAuditLog (
                LogID           INTEGER PRIMARY KEY AUTOINCREMENT,
                IssueID         INTEGER NOT NULL,
                UserID          INTEGER,
                Username        TEXT,
                Action          TEXT NOT NULL,
                FieldName       TEXT,
                OldValue        TEXT,
                NewValue        TEXT,
                CreatedAt       TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (IssueID) REFERENCES StockIssues(IssueID)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stockissueauditlog_issue ON StockIssueAuditLog(IssueID, CreatedAt)")
        conn.commit()
        print("Created StockIssueAuditLog table.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
