"""One-time migration: adds the Visit Plan geofence check to an existing
installation.

- CompanySettings.VisitGeofenceRadiusMeters - how close (meters) a rep must
  be to their own Visit Plan stop to mark it visited, Admin-configurable
  from Company / GST Settings. Defaults to 200 for every existing row (there
  is only ever one CompanySettings row).
- VisitPlanStops.VisitedDistanceMeters - how far the rep's device actually
  was when they marked a stop visited (NULL for an Admin/Manager override,
  or while still pending). NULL for every existing row - nothing to
  backfill, there's no prior data to infer a distance from.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_visit_geofence.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        did_work = False

        if not column_exists(conn, "CompanySettings", "VisitGeofenceRadiusMeters"):
            conn.execute("ALTER TABLE CompanySettings ADD COLUMN VisitGeofenceRadiusMeters INTEGER NOT NULL DEFAULT 200")
            conn.commit()
            print("Added CompanySettings.VisitGeofenceRadiusMeters.")
            did_work = True
        else:
            print("CompanySettings.VisitGeofenceRadiusMeters already exists - skipping that part.")

        if not column_exists(conn, "VisitPlanStops", "VisitedDistanceMeters"):
            conn.execute("ALTER TABLE VisitPlanStops ADD COLUMN VisitedDistanceMeters REAL")
            conn.commit()
            print("Added VisitPlanStops.VisitedDistanceMeters.")
            did_work = True
        else:
            print("VisitPlanStops.VisitedDistanceMeters already exists - skipping that part.")

        if did_work:
            print("\nWhat's new:")
            print("- A rep marking their OWN Visit Plan stop visited must now be within a")
            print("  configurable radius (default 200m, see Company / GST Settings) of it - captured")
            print("  from the browser's current location at the moment they tap 'Mark Visited'.")
            print("  An Admin/Manager marking a stop visited on someone else's behalf is unaffected -")
            print("  that's an override, not a location claim.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
