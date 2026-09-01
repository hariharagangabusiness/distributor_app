"""One-time migration for phase 19:

1. Products can now carry an Incentive (flat Rs per unit) - the extra
   margin/income the distributor earns on that product, separate from
   its Cost Price / Selling Price / Scheme discount. This is added to
   the distributor's margin on the Profit & Loss report (as Incentive
   Income, on top of Gross Profit), based on units sold in the P&L's
   date range. Adds: Products.IncentivePerUnit.

2. The Stock Issues Report page (Reports > Stock Issue Schemes & Dues)
   now also shows a day-wise Cost vs Sales breakdown for the selected
   date range - Cost = Products.CostPrice x qty issued that day, Sales
   = that day's Stock Issue Expected/Collected amounts. This view does
   NOT include Incentive, by design - it is meant as a plain day-wise
   cost vs sales check, not a margin/profit view. No schema change
   needed for this part, it's a new query/section on an existing page.

Nothing existing is dropped, renamed, or overwritten. Safe to run
multiple times.

Usage: python migrate_incentive_and_dayview.py
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
    if add_column_if_missing(conn, "Products", "IncentivePerUnit", "REAL NOT NULL DEFAULT 0"):
        added += 1
    conn.commit()

    print("Migration complete.")
    if added:
        print(f"\n{added} new column added.")
    else:
        print("\nNo new columns needed - already up to date.")
    print("\nWhat's new:")
    print("- Products can now have an Incentive (flat Rs per unit) - the extra margin/income the")
    print("  distributor earns on that product. Set it on the Product & Stock add/edit form.")
    print("- Profit & Loss now shows an 'Incentive Income' line (Incentive x units sold in the date")
    print("  range), added on top of Gross Profit before Net Profit.")
    print("- The Stock Issues Report page now has a day-wise Cost vs Sales table for the selected date")
    print("  range - this intentionally excludes Incentive, it's a plain cost-vs-sales check.")


if __name__ == "__main__":
    run()
