"""One-time migration: adds CompanySettings.LocationPingIntervalMinutes and
LocationLogs.RefType/RefID.

LocationPingIntervalMinutes makes the periodic location-ping interval (used
to be a hardcoded 30 minutes in static/js/location-tracking.js) Admin-
configurable from Company / GST Settings. Existing installs default to 30,
so behaviour doesn't silently change on migration.

RefType/RefID let a LocationLogs row be tied to a specific record instead of
only ever being a routine periodic ping - used for a location captured at
the moment a Sale is saved (RefType='Sale', RefID=<SaleID>), for every role,
best-effort (see log_sale_location() in app.py). Shown as its own marker
style on the Location Tracking map.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_sale_location_and_ping_interval.py
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

        if not column_exists(conn, "CompanySettings", "LocationPingIntervalMinutes"):
            conn.execute(
                "ALTER TABLE CompanySettings ADD COLUMN LocationPingIntervalMinutes INTEGER NOT NULL DEFAULT 30")
            conn.commit()
            print("Added CompanySettings.LocationPingIntervalMinutes (default 30).")
            did_work = True
        else:
            print("CompanySettings.LocationPingIntervalMinutes already exists - skipping that part.")

        if not column_exists(conn, "LocationLogs", "RefType"):
            conn.execute("ALTER TABLE LocationLogs ADD COLUMN RefType TEXT")
            conn.commit()
            print("Added LocationLogs.RefType.")
            did_work = True
        else:
            print("LocationLogs.RefType already exists - skipping that part.")

        if not column_exists(conn, "LocationLogs", "RefID"):
            conn.execute("ALTER TABLE LocationLogs ADD COLUMN RefID INTEGER")
            conn.commit()
            print("Added LocationLogs.RefID.")
            did_work = True
        else:
            print("LocationLogs.RefID already exists - skipping that part.")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_locationlogs_ref ON LocationLogs(RefType, RefID)")
        conn.commit()

        if did_work:
            print("\nWhat's new:")
            print("- Company / GST Settings' Location Tracking section now has a 'Ping interval (minutes)'")
            print("  field - previously a hardcoded 30. Existing installs keep behaving exactly as before")
            print("  (default 30) until an Admin changes it.")
            print("- Every Sale now best-effort captures the device's location at the moment it's saved -")
            print("  for every role, not just tracked ones - and shows it on the Location Tracking map as")
            print("  its own marker, with the invoice/customer/amount in its popup.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
