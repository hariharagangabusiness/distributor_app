"""One-time migration for the new Targets tab (phase 20):

Adds the new Targets table - sales targets to drive the distributor's
business, set per Employee (required) x Product (optional, NULL =
applies across all products for that employee) x calendar Month:
  - Qty Sold Target
  - Sales Value Target (Rs) - compared against both Expected Amount and
    Cash Collected actuals from Stock Issues
  - Discount Cap Amount (Rs, fixed ceiling for the month)
  - Discount Cap Rate Per Unit (Rs/unit, effective allowed = rate x
    actual qty sold) - shown alongside the fixed cap, not instead of it

Week/day/MTD/WTD analysis on the new Targets tab is derived from these
month-level targets by pro-rating across the days in that month, rather
than needing separate week/day rows - one Target per Employee+Product+
Month is the unit of data entry.

Nothing existing is dropped, renamed, or overwritten. Safe to run
multiple times.

Usage: python migrate_targets.py
"""
import db


def run():
    db.init_db()  # creates Targets if missing (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='Targets'")]
    conn.commit()

    print("Migration complete.")
    if tables:
        print("\nTargets table is ready.")
    print("\nWhat's new:")
    print("- A new 'Targets' tab (Admin sets targets; everyone can view) lets you set monthly")
    print("  sales targets per salesperson, optionally broken down by product: Qty Sold, Sales")
    print("  Value (Rs), and a Discount reduction cap (a fixed Rs ceiling and a Rs/unit rate,")
    print("  shown side by side).")
    print("- The Targets tab shows MTD/WTD progress, day-wise trends, and per-employee/per-product")
    print("  breakdowns, all measured against actual figures already recorded in Stock Issues.")
    print("- Target vs Actual progress now also shows on the Dashboard, Reports hub, the Stock")
    print("  Issues list, and the Stock Issue view/Reconcile screens, wherever a target exists for")
    print("  that employee's month.")


if __name__ == "__main__":
    run()
