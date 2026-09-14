"""Adds the Zones table (admin-managed list of sales-territory names) to an
existing installation, and seeds it with every distinct non-blank Zone value
already in use on Customers.Zone - so existing customers' free-text zones
aren't orphaned once the Customer form switches to a dropdown backed by this
table. Safe to re-run."""
import sqlite3
from db import DB_PATH


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        created = False
        if not table_exists(conn, "Zones"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS Zones (
                    ZoneID          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ZoneName        TEXT NOT NULL UNIQUE,
                    Active          INTEGER NOT NULL DEFAULT 1,
                    DisplayOrder    INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()
            created = True
            print("Created Zones table.")
        else:
            print("Zones table already exists.")

        # Seed with any distinct existing Customers.Zone values not already present,
        # so no existing customer's zone becomes an orphaned/unmatched value once the
        # Customer form switches from free text to a dropdown of Zones.
        existing_names = {r[0].strip().lower() for r in conn.execute(
            "SELECT ZoneName FROM Zones").fetchall()}
        rows = conn.execute(
            "SELECT DISTINCT Zone FROM Customers WHERE Zone IS NOT NULL AND TRIM(Zone) <> ''").fetchall()
        max_order = conn.execute("SELECT COALESCE(MAX(DisplayOrder),0) FROM Zones").fetchone()[0]
        added = 0
        for (zone,) in rows:
            name = zone.strip()
            if not name or name.lower() in existing_names:
                continue
            max_order += 1
            conn.execute("INSERT INTO Zones (ZoneName, Active, DisplayOrder) VALUES (?,1,?)",
                         (name, max_order))
            existing_names.add(name.lower())
            added += 1
        if added:
            conn.commit()
            print(f"Seeded {added} zone(s) from existing customer data.")
        elif not created:
            print("No new zones to seed.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
