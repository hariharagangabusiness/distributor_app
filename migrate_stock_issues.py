"""One-time migration: adds the "Issue Stock to Salesperson" / end-of-day
reconciliation feature (StockIssues + StockIssueLines), and doesn't touch
anything else. This does NOT create Sales records or change GST reporting
- it's a separate same-day custody/cash-tally tool for route/van
salespeople.

Also required for the Admin-only Edit screens on Purchases/Sales and the
Profit & Loss report - those don't add any tables/columns (they reuse
what's already there), so there's nothing extra to run for them, but this
migration is a good time to make sure you're on the latest schema.

Safe to run multiple times.

Usage: python migrate_stock_issues.py
"""
import db


def run():
    db.init_db()  # creates StockIssues/StockIssueLines if missing (CREATE TABLE IF NOT EXISTS)
    print("Migration complete.")
    print("\nWhat's new:")
    print("- A 'Stock Issues' section lets you issue a batch of products to a salesperson in the")
    print("  morning, then reconcile at day's end (qty sold, qty returned, cash collected per product)")
    print("  with automatic discrepancy flagging.")
    print("- Purchases and Sales now have an Edit screen for Admin accounts, to correct a mistake")
    print("  on an already-saved record without deleting and re-entering it.")
    print("- A Profit & Loss report (Reports > Profit & Loss) computed from Sales, Purchases,")
    print("  Operating Expenses, and Salary Payments over any date range.")


if __name__ == "__main__":
    run()
