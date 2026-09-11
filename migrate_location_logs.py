"""Adds the LocationLogs table (field location tracking for Staff/Supervisor/
Manager logins) to an existing installation. Safe to re-run."""
import sqlite3
from db import DB_PATH


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if table_exists(conn, "LocationLogs"):
            print("LocationLogs already exists - nothing to do.")
            return
        conn.execute("""
            CREATE TABLE IF NOT EXISTS LocationLogs (
                LogID           INTEGER PRIMARY KEY AUTOINCREMENT,
                UserID          INTEGER NOT NULL,
                EmployeeID      INTEGER,
                Role            TEXT NOT NULL,
                Latitude        REAL NOT NULL,
                Longitude       REAL NOT NULL,
                Accuracy        REAL,
                RecordedAt      TEXT NOT NULL,
                RecordedDate    TEXT NOT NULL,
                FOREIGN KEY (UserID) REFERENCES Users(UserID),
                FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_locationlogs_user_date ON LocationLogs(UserID, RecordedDate)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_locationlogs_date ON LocationLogs(RecordedDate)")
        conn.commit()
        print("Created LocationLogs table.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
