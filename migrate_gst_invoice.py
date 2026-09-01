"""One-time migration: adds GST invoicing support (company profile,
HSN/GST rate on products, state on customers, tax breakup on sales) to an
EXISTING database without touching any data you've already entered.

Safe to run multiple times. Safe to run even on a brand-new database
(init_db.py / seed_demo.py already include these fields, so this becomes
a no-op there).

Usage: python migrate_gst_invoice.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def add_column_if_missing(conn, table, column, coltype_and_default):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_and_default}")
        print(f"  Added {table}.{column}")


def run():
    db.init_db()  # ensures base tables/CompanySettings exist (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()

    print("Checking Products...")
    add_column_if_missing(conn, "Products", "HSNCode", "TEXT")
    add_column_if_missing(conn, "Products", "GSTRate", "REAL NOT NULL DEFAULT 0")

    print("Checking Customers...")
    add_column_if_missing(conn, "Customers", "State", "TEXT")
    add_column_if_missing(conn, "Customers", "StateCode", "TEXT")

    print("Checking Sales...")
    add_column_if_missing(conn, "Sales", "PlaceOfSupplyState", "TEXT")
    add_column_if_missing(conn, "Sales", "PlaceOfSupplyStateCode", "TEXT")
    add_column_if_missing(conn, "Sales", "IsInterState", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "Sales", "TaxableAmount", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "Sales", "CGSTAmount", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "Sales", "SGSTAmount", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "Sales", "IGSTAmount", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "Sales", "RoundOff", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "Sales", "ReverseCharge", "INTEGER NOT NULL DEFAULT 0")

    print("Checking SalesLines...")
    add_column_if_missing(conn, "SalesLines", "HSNCode", "TEXT")
    add_column_if_missing(conn, "SalesLines", "GSTRate", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "SalesLines", "TaxableValue", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "SalesLines", "CGSTRate", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "SalesLines", "CGSTAmount", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "SalesLines", "SGSTRate", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "SalesLines", "SGSTAmount", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "SalesLines", "IGSTRate", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "SalesLines", "IGSTAmount", "REAL NOT NULL DEFAULT 0")

    # Existing sales rows made before this migration have no tax breakup —
    # treat their existing TotalAmount as fully taxable at 0% so invoices
    # for old sales still render (just without a tax split) rather than
    # crash. New sales going forward will always have a proper breakup.
    conn.execute("""UPDATE Sales SET TaxableAmount = TotalAmount
                     WHERE TaxableAmount = 0 AND TotalAmount <> 0""")

    conn.commit()
    conn.close()
    print("\nMigration complete. Go to Settings in the app and fill in your")
    print("company GST details before generating your first invoice.")


if __name__ == "__main__":
    run()
