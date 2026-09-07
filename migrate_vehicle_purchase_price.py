"""Adds Vehicles.PurchasePrice, needed for the new vehicle depreciation
calculation on the Profit & Loss report. Safe to run more than once."""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if not column_exists(conn, "Vehicles", "PurchasePrice"):
            conn.execute("ALTER TABLE Vehicles ADD COLUMN PurchasePrice REAL")
            print("Added Vehicles.PurchasePrice")
        else:
            print("Vehicles.PurchasePrice already exists - nothing to do")
        conn.commit()
    finally:
        conn.close()

    print("""
Migration complete. What's new:
  - The Vehicle form (Fleet > Vehicles) now has a Purchase Price field.
  - The Profit & Loss report now includes two new lines under Operating
    Expenses:
      - "Vehicle Depreciation" - computed automatically using the Income
        Tax Act's 15% WDV method for motor vehicles (with the 180-day
        half-rate rule for the year of purchase), for any vehicle that
        has a Purchase Date and Purchase Price filled in.
      - "Vehicle Maintenance & Repairs" - the total Cost of all Fleet >
        Maintenance records in the report's date range (this is revenue
        expenditure, fully deductible in the year incurred - separate
        from the capitalised depreciation above).
  - Existing vehicles with no Purchase Price will show Rs. 0 depreciation
    until you fill that field in on their Edit Vehicle screen.
""")


if __name__ == "__main__":
    main()
