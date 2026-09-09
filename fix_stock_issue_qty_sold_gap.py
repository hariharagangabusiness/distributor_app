"""Corrects StockIssueLines.QtySold for one Issue+Product to match the true total sold,
when the stored figure under-reports because the line's Qty Issued capacity was topped up
in multiple installments during the day (see diagnose_stock_issue_topup_history.py and
true_product_qty_sold() in app.py for the full explanation).

This does NOT touch money, GST, or P&L - it only corrects the Stock Issue line's own
QtySold figure so the sheet reads accurately. It refuses to run if the issue has already
been reconciled (Status='Reconciled'), since at that point QtySold also drives Expected
Rs/Discrepancy and a blind correction could throw those off; reconcile normally instead
(the reconcile form now auto-corrects the pre-filled Qty Sold for exactly this case).

Usage:
    python fix_stock_issue_qty_sold_gap.py <IssueID> <ProductID>          (dry run - shows what would change)
    python fix_stock_issue_qty_sold_gap.py <IssueID> <ProductID> --apply  (applies the correction)
"""
import sys
import sqlite3
from db import DB_PATH
from app import true_product_qty_sold


def main():
    if len(sys.argv) < 3:
        print("Usage: python fix_stock_issue_qty_sold_gap.py <IssueID> <ProductID> [--apply]")
        return
    issue_id = int(sys.argv[1])
    product_id = int(sys.argv[2])
    apply_fix = "--apply" in sys.argv[3:]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    issue = conn.execute("""SELECT si.*, e.EmployeeName FROM StockIssues si
                          JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""",
                         (issue_id,)).fetchone()
    line = conn.execute("SELECT * FROM StockIssueLines WHERE IssueID=? AND ProductID=?",
                        (issue_id, product_id)).fetchone()
    product = conn.execute("SELECT ProductName, Unit FROM Products WHERE ProductID=?", (product_id,)).fetchone()
    if not issue or not line or not product:
        print("Issue, line, or product not found.")
        conn.close()
        return

    if issue["Status"] == "Reconciled":
        print(f"Issue #{issue_id} is already Reconciled - refusing to run. QtySold on a reconciled "
              f"issue also drives Expected Rs/Discrepancy, so blindly bumping it here could throw "
              f"those off. Use 'Edit Reconciliation' instead - the form now auto-shows the true "
              f"total for this exact situation.")
        conn.close()
        return

    stored = line["QtySold"] or 0
    true_total = true_product_qty_sold(issue["EmployeeID"], issue["IssueDate"], product_id)

    print(f"Issue #{issue_id} - {issue['EmployeeName']} - {issue['IssueDate']}")
    print(f"Product: {product['ProductName']} (ProductID={product_id})")
    print(f"Stored QtySold: {stored} {product['Unit'] or ''}")
    print(f"True total sold (ground truth from SalesLines): {true_total} {product['Unit'] or ''}")

    if abs(true_total - stored) < 0.01:
        print("\nNo gap found - nothing to correct.")
        conn.close()
        return

    print(f"\nWould update StockIssueLines.QtySold from {stored} to {true_total} for LineID={line['LineID']}.")
    if not apply_fix:
        print("\nDry run only - no changes made. Re-run with --apply to make this change.")
        conn.close()
        return

    conn.execute("UPDATE StockIssueLines SET QtySold=? WHERE LineID=?", (true_total, line["LineID"]))
    conn.commit()
    print(f"\nApplied: QtySold is now {true_total}.")
    conn.close()


if __name__ == "__main__":
    main()
