"""Adds the FormFieldLayout table (Admin-customizable Sale form field
visibility/placement) to an existing installation. Safe to re-run."""
import sqlite3
from db import DB_PATH


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if table_exists(conn, "FormFieldLayout"):
            print("FormFieldLayout already exists - nothing to do.")
            return
        conn.execute("""
            CREATE TABLE IF NOT EXISTS FormFieldLayout (
                ModuleName      TEXT NOT NULL,
                FieldKey        TEXT NOT NULL,
                Visible         INTEGER NOT NULL DEFAULT 1,
                DisplayOrder    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (ModuleName, FieldKey)
            )
        """)
        conn.commit()
        print("Created FormFieldLayout table.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
