"""One-time migration: adds CompanySettings.UpiId.

Lets Admin configure a UPI ID (e.g. "9959484777@axisbank") under Company /
GST Settings, next to Bank Details. When set, a scannable UPI payment QR
code is generated from it and shown on the Tax Invoice (HTML + PDF) and
the 58mm thermal receipt. A brand-new nullable column with no backfill
needed - existing installs are unaffected until an Admin fills it in.

Usage: python migrate_upi_qr.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if not column_exists(conn, "CompanySettings", "UpiId"):
            conn.execute("ALTER TABLE CompanySettings ADD COLUMN UpiId TEXT")
            conn.commit()
            print("Added CompanySettings.UpiId.")
            print("\nWhat's new:")
            print("- Company / GST Settings' Bank Details section now has a 'UPI ID' field.")
            print("  Once set, a scannable UPI payment QR code is shown on the Tax Invoice")
            print("  (both the on-screen/PDF version) and the 58mm thermal receipt.")
        else:
            print("CompanySettings.UpiId already exists - nothing to do.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
