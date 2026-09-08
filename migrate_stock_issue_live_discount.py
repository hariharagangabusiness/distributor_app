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


def backfill_existing_credits(conn):
    """One-time catch-up for SaleStockIssueLinks rows created BEFORE this fix
    existed (DiscountApplied still 0 because the old create_sale() never set
    it). For each such link, look up the actual SalesLines discount for that
    Sale+Product, prorate it by how much of that line's qty this link
    covered (mirrors the proportional logic now built into create_sale()),
    and add it into both SaleStockIssueLinks.DiscountApplied and
    StockIssueLines.DiscountAmount. Only touches links whose DiscountApplied
    is still exactly 0, so it is safe to run more than once - a link this
    already fixed (or one that legitimately carried zero discount) is left
    alone either way."""
    rows = conn.execute("""
        SELECT ssl.LinkID, ssl.SaleID, ssl.StockIssueLineID, ssl.QtyApplied, sil.ProductID
        FROM SaleStockIssueLinks ssl
        JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
        WHERE COALESCE(ssl.DiscountApplied, 0) = 0
    """).fetchall()
    fixed = 0
    for link_id, sale_id, sil_id, qty_applied, product_id in rows:
        sl = conn.execute("""SELECT Qty, DiscountAmount FROM SalesLines
                           WHERE SaleID=? AND ProductID=?""", (sale_id, product_id)).fetchone()
        if not sl:
            continue
        sl_qty, sl_discount = sl
        if not sl_qty or not sl_discount:
            continue
        discount_share = round(sl_discount * (qty_applied / sl_qty), 2)
        if discount_share <= 0:
            continue
        conn.execute("UPDATE SaleStockIssueLinks SET DiscountApplied=? WHERE LinkID=?", (discount_share, link_id))
        conn.execute("UPDATE StockIssueLines SET DiscountAmount = COALESCE(DiscountAmount,0) + ? WHERE LineID=?",
                     (discount_share, sil_id))
        fixed += 1
    conn.commit()
    return fixed, len(rows)


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        if not column_exists(conn, "SaleStockIssueLinks", "DiscountApplied"):
            conn.execute("ALTER TABLE SaleStockIssueLinks ADD COLUMN DiscountApplied REAL NOT NULL DEFAULT 0")
            conn.commit()
            print("Added SaleStockIssueLinks.DiscountApplied")
        else:
            print("SaleStockIssueLinks.DiscountApplied already exists - nothing to do")

        fixed, candidates = backfill_existing_credits(conn)
        print(f"Backfill: checked {candidates} existing credit link(s), corrected {fixed} that were "
              f"missing their discount.")
    finally:
        conn.close()

    print("""
Migration complete. What's fixed:
  - Live Sales Monitor's "Expected (stock sold)" figure, and a Stock
    Issue's Expected Amount before it's formally reconciled, now correctly
    subtract the discount given on any Sales-tab entry that auto-credited
    against that issue - previously that discount was silently dropped
    from Expected until someone reconciled the issue by hand.
  - This migration ALSO backfills any Sales-tab credits already recorded
    before this fix existed, so today's/earlier's figures correct
    themselves immediately - no need to re-save any existing Sale.
""")


if __name__ == "__main__":
    main()
