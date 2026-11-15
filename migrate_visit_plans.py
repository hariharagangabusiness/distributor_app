"""One-time migration: adds the Guided Visit Plan feature to an existing
installation.

- Customers.Latitude/Longitude - a map location saved once (Customer form's
  "Use my current location" button), letting that customer be put on a
  Visit Plan and shown as a pin on the Location Tracking map. NULL for
  every existing customer - nothing to backfill, there's no prior data to
  infer a GPS location from.
- VisitPlans / VisitPlanStops - one Visit Plan per salesperson per day
  (Admin/Manager-built "who should this rep visit today"), and its stops
  (an existing Customer, or a one-off ad-hoc place). Brand new tables, so
  also declared directly in schema.sql - this just gets them onto an
  existing database without waiting for the next app restart.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_visit_plans.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        did_work = False

        for column in ("Latitude", "Longitude"):
            if not column_exists(conn, "Customers", column):
                conn.execute(f"ALTER TABLE Customers ADD COLUMN {column} REAL")
                conn.commit()
                print(f"Added Customers.{column}.")
                did_work = True
            else:
                print(f"Customers.{column} already exists - skipping that part.")

        if not table_exists(conn, "VisitPlans"):
            conn.execute("""
                CREATE TABLE VisitPlans (
                    VisitPlanID         INTEGER PRIMARY KEY AUTOINCREMENT,
                    EmployeeID          INTEGER NOT NULL,
                    VisitDate           TEXT NOT NULL,
                    CreatedByUserID     INTEGER,
                    CreatedByUsername   TEXT,
                    CreatedAt           TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE (EmployeeID, VisitDate),
                    FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID)
                )
            """)
            conn.commit()
            print("Created VisitPlans table.")
            did_work = True
        else:
            print("VisitPlans already exists - skipping that part.")

        if not table_exists(conn, "VisitPlanStops"):
            conn.execute("""
                CREATE TABLE VisitPlanStops (
                    StopID              INTEGER PRIMARY KEY AUTOINCREMENT,
                    VisitPlanID         INTEGER NOT NULL,
                    CustomerID          INTEGER,
                    StopName            TEXT,
                    Latitude            REAL,
                    Longitude           REAL,
                    SortOrder           INTEGER NOT NULL DEFAULT 0,
                    VisitedAt           TEXT,
                    VisitedByUserID     INTEGER,
                    VisitedByUsername   TEXT,
                    Notes               TEXT,
                    CreatedAt           TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (VisitPlanID) REFERENCES VisitPlans(VisitPlanID) ON DELETE CASCADE,
                    FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID)
                )
            """)
            conn.commit()
            print("Created VisitPlanStops table.")
            did_work = True
        else:
            print("VisitPlanStops already exists - skipping that part.")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_visitplanstops_plan ON VisitPlanStops(VisitPlanID)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visitplanstops_customer ON VisitPlanStops(CustomerID)")
        conn.commit()

        if did_work:
            print("\nWhat's new:")
            print("- The Customer form has a new 'Map Location' field (Latitude/Longitude, with a")
            print("  'Use my current location' button) - set it once to make that customer eligible")
            print("  for a Visit Plan.")
            print("- Location Tracking now has a 'Today's Visit Plan' panel: Admin/Manager picks which")
            print("  Customers (or one-off stops) a salesperson should visit that day. Planned stops show")
            print("  as their own pin on the map, with one-tap Google Maps directions and a way to mark")
            print("  each one visited.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
