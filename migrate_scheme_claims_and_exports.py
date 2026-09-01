"""One-time migration for three features:

1. Excel export ("Export to Excel") is added to every major list page
   (Products, Suppliers, Customers, Purchases, Sales, Stock Issues,
   Expenses, Vehicles, Maintenance, Employees, Salary Schedule, Advance
   Payments) - no schema change needed for this, it's a new route/button.

2. A new "Scheme Claims" tab (Admin only, under Reports) for
   distributor-level scheme claims that aren't tied to any single product
   or Stock Issue - e.g. an annual volume rebate, or a promo claim
   covering multiple products/months. Manually add a claim with a Scheme
   Name, the products/line it applies to, a description, and the claim
   amount, then mark it Claimed and/or Received once the company pays it
   out. Adds a new table: SchemeClaims.

3. Products can now carry a Scheme % (of Cost Price) as a running
   discount/promo - e.g. "Diwali Scheme, 10% of cost price". Wherever
   that product's price is used (Issue Stock, Sales, Stock Issue
   reconciliation's discount suggestion), the app now suggests the
   scheme-discounted price/discount automatically - always editable
   before you save. Adds: Products.SchemeName, Products.SchemePercent.

Nothing existing is dropped, renamed, or overwritten. Safe to run
multiple times.

Usage: python migrate_scheme_claims_and_exports.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def add_column_if_missing(conn, table, column, ddl):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        print(f"  Added {table}.{column}")
        return True
    return False


def run():
    db.init_db()
    conn = db.get_conn()
    added = 0
    if add_column_if_missing(conn, "Products", "SchemeName", "TEXT"):
        added += 1
    if add_column_if_missing(conn, "Products", "SchemePercent", "REAL NOT NULL DEFAULT 0"):
        added += 1
    conn.execute("""CREATE TABLE IF NOT EXISTS SchemeClaims (
        ClaimID         INTEGER PRIMARY KEY AUTOINCREMENT,
        SchemeName      TEXT NOT NULL,
        ClaimDate       TEXT NOT NULL,
        ApplicableProducts TEXT,
        Description     TEXT,
        ClaimAmount     REAL NOT NULL DEFAULT 0,
        Status          TEXT NOT NULL DEFAULT 'Pending',
        ClaimedAt       TEXT,
        ReceivedAt      TEXT,
        ReceivedAmount  REAL,
        Notes           TEXT,
        CreatedAt       TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.commit()

    print("Migration complete.")
    if added:
        print(f"\n{added} new column(s) added, plus the new SchemeClaims table.")
    else:
        print("\nNo new columns needed (SchemeClaims table created if it wasn't already there).")
    print("\nWhat's new:")
    print("- 'Export to Excel' button on Products, Suppliers, Customers, Purchases, Sales, Stock Issues,")
    print("  Expenses, Vehicles, Maintenance, Employees, Salary Schedule, and Advance Payments.")
    print("- A new 'Scheme Claims' tab (Reports > Scheme Claims, Admin only) to manually track")
    print("  distributor-level scheme claims not tied to any one product/Stock Issue - add the scheme")
    print("  name, applicable products, amount, and mark Claimed / Received as the company pays it out.")
    print("- Products can now have a Scheme % (of Cost Price) - Issue Stock, Sales, and Stock Issue")
    print("  reconciliation now suggest the scheme-discounted price/discount automatically wherever that")
    print("  product's price is used (always editable before saving).")


if __name__ == "__main__":
    run()
