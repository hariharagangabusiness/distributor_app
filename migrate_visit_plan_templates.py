"""One-time migration: adds Visit Plan Templates to an existing installation.

- VisitPlanTemplates / VisitPlanTemplateStops - a named, reusable list of
  stops an Admin/Manager can save from one day's Visit Plan and apply again
  to any employee/date later (e.g. a recurring weekly route). Not tied to
  the employee/date it was saved from. Brand new tables, so also declared
  directly in schema.sql - this just gets them onto an existing database
  without waiting for the next app restart.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_visit_plan_templates.py
"""
import sqlite3
from db import DB_PATH


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        did_work = False

        if not table_exists(conn, "VisitPlanTemplates"):
            conn.execute("""
                CREATE TABLE VisitPlanTemplates (
                    TemplateID          INTEGER PRIMARY KEY AUTOINCREMENT,
                    Name                TEXT NOT NULL,
                    CreatedByUserID     INTEGER,
                    CreatedByUsername   TEXT,
                    CreatedAt           TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
            print("Created VisitPlanTemplates table.")
            did_work = True
        else:
            print("VisitPlanTemplates already exists - skipping that part.")

        if not table_exists(conn, "VisitPlanTemplateStops"):
            conn.execute("""
                CREATE TABLE VisitPlanTemplateStops (
                    TemplateStopID      INTEGER PRIMARY KEY AUTOINCREMENT,
                    TemplateID          INTEGER NOT NULL,
                    CustomerID          INTEGER,
                    StopName            TEXT,
                    Latitude            REAL,
                    Longitude           REAL,
                    SortOrder           INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (TemplateID) REFERENCES VisitPlanTemplates(TemplateID) ON DELETE CASCADE,
                    FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID)
                )
            """)
            conn.commit()
            print("Created VisitPlanTemplateStops table.")
            did_work = True
        else:
            print("VisitPlanTemplateStops already exists - skipping that part.")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_visitplantemplatestops_template ON VisitPlanTemplateStops(TemplateID)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visitplantemplatestops_customer ON VisitPlanTemplateStops(CustomerID)")
        conn.commit()

        if did_work:
            print("\nWhat's new:")
            print("- Today's Visit Plan panel (Location Tracking) has a 'Save as Template' button -")
            print("  name the current day's stop list and reuse it later for any employee/date via")
            print("  'Apply Template'.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
