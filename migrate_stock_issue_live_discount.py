"""Fixes the Live Sales Monitor / Stock Issue "Expected" figure not
accounting for discounts given on Sales-tab entries that auto-credit
against an open Stock Issue (before that issue is formally reconciled).

Root cause: when a Sale's line quantity is credited against an open Stock
Issue line (StockIssueLines.QtySold += apply_qty), the discount actually
given on that Sale line was never carried over to
StockIssueLines.DiscountAmount - only Reconcile's manual entry ever wrote
that column. So "Expected" (Qty Sold x Unit Price - Discount) came out
too high by the discount amount for any not-yet-reconciled issue with
credited Sales-tab activity.

Adds SaleStockIssueLinks.DiscountApplied (how much discount this specific
credit carried, so an edit/delete can reverse exactly that amount) - safe
to run more than once."""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if not column_exists(conn, "SaleStockIssueLinks", "DiscountApplied"):
            conn.execute("ALTER TABLE SaleStockIssueLinks ADD COLUMN DiscountApplied REAL NOT NULL DEFAULT 0")
            print("Added SaleStockIssueLinks.DiscountApplied")
        else:
            print("SaleStockIssueLinks.DiscountApplied already exists - nothing to do")
        conn.commit()
    finally:
        conn.close()

    print("""
Migration complete. What's fixed:
  - Live Sales Monitor's "Expected (stock sold)" figure, and a Stock
    Issue's Expected Amount before it's formally reconciled, now correctly
    subtract the discount given on any Sales-tab entry that auto-credited
    against that issue - previously that discount was silently dropped
    from Expected until someone reconciled the issue by hand.
  - This only affects NEW Sales going forward - it does not retroactively
    recompute discount already missing from an issue's current
    StockIssueLines.DiscountAmount for past/existing credited Sales. If
    today's Live Sales Monitor figures look off for an issue that already
    has credited Sales from before this fix, editing and re-saving one of
    those Sales (even with no changes) will re-credit it correctly, or
    just proceed to Reconcile as normal - the reconcile form's Discount
    field can always be corrected by hand regardless.
""")


if __name__ == "__main__":
    main()
