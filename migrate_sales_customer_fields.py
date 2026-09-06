"""One-time migration: adds to an EXISTING database, without touching any
data you've already entered:

  - Customers.Zone - a free-text sales territory/route field, editable from
    the Customer form and directly from the Sales form.
  - SalesLines.DiscountAmount - a flat Rs discount per line item, entered
    next to Rate on the Sales form, subtracted from that line's taxable
    value before GST is calculated.

Safe to run multiple times. Safe to run even on a brand-new database
(init_db.py / seed_demo.py already include both via schema.sql, so this
becomes a no-op there).

Usage: python migrate_sales_customer_fields.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def add_column_if_missing(conn, table, column, coltype_and_default):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_and_default}")
        print(f"  Added {table}.{column}")
    else:
        print(f"  {table}.{column} already exists - skipping")


def run():
    db.init_db()  # creates both tables fresh with the new columns if the DB is brand new
    conn = db.get_conn()

    print("Checking Customers...")
    add_column_if_missing(conn, "Customers", "Zone", "TEXT")

    print("Checking SalesLines...")
    add_column_if_missing(conn, "SalesLines", "DiscountAmount", "REAL NOT NULL DEFAULT 0")

    conn.commit()
    conn.close()
    print("\nMigration complete. New capabilities now available:")
    print("  - Customer records (and the Sales form itself) now have a Zone field.")
    print("  - The Sales form's Customer section is editable in place - correcting the")
    print("    name/address/phone/zone there updates that customer's master record.")
    print("  - Each Sales line item now has a Discount ₹ field next to Rate.")


if __name__ == "__main__":
    run()
