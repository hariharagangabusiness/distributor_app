"""One-time migration: adds configurable location-tracking working days, an
Admin-editable consent notice, and the LocationConsentLog audit table.

- CompanySettings.LocationTrackingWorkingDays: replaces the previously
  hardcoded "every day except Monday" rule. Defaults to 'Tue,Wed,Thu,Fri,
  Sat,Sun' - the exact days that rule already meant - so an existing
  install's tracking behaviour doesn't silently change.
- CompanySettings.LocationConsentText: Admin-editable wording for the
  mandatory consent screen shown at every login for a tracked role
  (Staff/Supervisor/Manager). NULL by default - app.py falls back to a
  built-in placeholder notice until an Admin fills in their own
  (ideally legally reviewed) text from Company / GST Settings.
- LocationConsentLog: permanent record of every time someone accepted that
  screen.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_location_consent.py
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
        changed = False
        if not column_exists(conn, "CompanySettings", "LocationTrackingWorkingDays"):
            conn.execute("ALTER TABLE CompanySettings ADD COLUMN LocationTrackingWorkingDays "
                         "TEXT NOT NULL DEFAULT 'Tue,Wed,Thu,Fri,Sat,Sun'")
            print("Added CompanySettings.LocationTrackingWorkingDays.")
            changed = True
        if not column_exists(conn, "CompanySettings", "LocationConsentText"):
            conn.execute("ALTER TABLE CompanySettings ADD COLUMN LocationConsentText TEXT")
            print("Added CompanySettings.LocationConsentText.")
            changed = True
        if not table_exists(conn, "LocationConsentLog"):
            conn.execute("""
                CREATE TABLE LocationConsentLog (
                    ConsentID       INTEGER PRIMARY KEY AUTOINCREMENT,
                    UserID          INTEGER NOT NULL,
                    ConsentedAt     TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (UserID) REFERENCES Users(UserID)
                )
            """)
            print("Created LocationConsentLog table.")
            changed = True
        conn.commit()

        if not changed:
            print("Nothing to do - already up to date.")
        else:
            print("\nWhat's new:")
            print("- Company / GST Settings has a new Location Tracking section: configurable working")
            print("  days (replacing the old hardcoded 'every day except Monday'), and an editable consent")
            print("  notice shown as a mandatory accept-to-continue screen at every login for Staff/")
            print("  Supervisor/Manager accounts.")
            print("- Until an Admin fills in the consent text, a built-in placeholder notice is shown -")
            print("  replace it with your own (ideally legally reviewed) wording when ready.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
