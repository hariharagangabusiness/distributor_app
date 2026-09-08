"""READ-ONLY diagnostic. Makes NO changes to the database.

Finds every Reconciled Stock Issue whose auto-created 'Unassigned' Sale
appears to have re-invoiced units that were ALSO already billed on a
direct Sales-tab entry via SaleStockIssueLinks (the double-invoicing bug
fixed in stock_issue_reconcile() on 2026-09-08).

For each StockIssueLine on a reconciled issue with an auto-Sale:
  - credited_qty = how much of that line is currently linked to a direct
    Sale via SaleStockIssueLinks (QtyApplied)
  - auto_line_qty = the Qty currently on that SAME product's line in the
    issue's auto-created Sale
  - overlap = min(credited_qty, auto_line_qty) - the quantity that looks
    double-billed (once on the direct Sale, again on the auto Sale)

Prints one line per affected Stock Issue plus a grand total, so nothing
here should be trusted as a "safe to delete" list - it's meant to be
reviewed by a human (and cross-checked against what's already been
GST-filed) before anything is corrected.

Run this against PRODUCTION via `railway ssh` then:
    python review_duplicate_reconciliation_invoices.py
"""
import sqlite3
from db import DB_PATH


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    issues = conn.execute("""
        SELECT si.IssueID, si.EmployeeID, si.IssueDate, si.SaleID, e.EmployeeName,
               s.InvoiceNumber, s.TotalAmount AS AutoSaleTotal
        FROM StockIssues si
        JOIN Employees e ON e.EmployeeID = si.EmployeeID
        JOIN Sales s ON s.SaleID = si.SaleID
        WHERE si.Status='Reconciled' AND si.SaleID IS NOT NULL
        ORDER BY si.IssueDate, si.IssueID
    """).fetchall()

    print(f"Checking {len(issues)} reconciled Stock Issue(s) with an auto-created Sale...\n")

    grand_total_overlap_value = 0.0
    grand_total_overlap_qty = 0.0
    affected_count = 0

    for issue in issues:
        lines = conn.execute("""
            SELECT sil.LineID, sil.ProductID, sil.UnitPrice, sil.QtySold, pr.ProductName
            FROM StockIssueLines sil JOIN Products pr ON pr.ProductID = sil.ProductID
            WHERE sil.IssueID=?
        """, (issue["IssueID"],)).fetchall()

        issue_overlap_qty = 0.0
        issue_overlap_value = 0.0
        detail_rows = []

        for line in lines:
            credited_qty = conn.execute("""
                SELECT COALESCE(SUM(QtyApplied), 0) q FROM SaleStockIssueLinks WHERE StockIssueLineID=?
            """, (line["LineID"],)).fetchone()["q"]

            auto_line = conn.execute("""
                SELECT Qty, UnitPrice, DiscountAmount FROM SalesLines
                WHERE SaleID=? AND ProductID=?
            """, (issue["SaleID"], line["ProductID"])).fetchone()

            if not auto_line or credited_qty <= 0:
                continue

            auto_qty = auto_line["Qty"] or 0
            overlap = min(credited_qty, auto_qty)
            if overlap <= 0:
                continue

            effective_rate = (auto_line["Qty"] * auto_line["UnitPrice"] - (auto_line["DiscountAmount"] or 0)) / auto_line["Qty"] \
                if auto_line["Qty"] else 0
            overlap_value = round(overlap * effective_rate, 2)

            issue_overlap_qty += overlap
            issue_overlap_value += overlap_value
            detail_rows.append(
                f"      - {line['ProductName']}: credited={credited_qty}, auto-invoiced={auto_qty}, "
                f"overlap={overlap} (~Rs {overlap_value})"
            )

        if issue_overlap_qty > 0:
            affected_count += 1
            grand_total_overlap_qty += issue_overlap_qty
            grand_total_overlap_value += issue_overlap_value
            print(f"Issue #{issue['IssueID']} ({issue['IssueDate']}, {issue['EmployeeName']}) -> "
                  f"auto-Sale {issue['InvoiceNumber']} (Rs {issue['AutoSaleTotal']}):")
            print(f"    Overlap: {issue_overlap_qty} units, ~Rs {round(issue_overlap_value, 2)} likely double-invoiced")
            for d in detail_rows:
                print(d)
            print()

    print("=" * 70)
    print(f"TOTAL: {affected_count} Stock Issue(s) with likely double-invoiced auto-Sales")
    print(f"       {round(grand_total_overlap_qty, 2)} units, ~Rs {round(grand_total_overlap_value, 2)} of "
          f"apparent duplicate revenue (this is a taxable-value estimate at the auto-Sale's effective rate, "
          f"not GST-inclusive - actual invoice impact including GST will be somewhat higher).")
    print("\nThis is diagnostic only - nothing was changed. Review this list (and cross-check against any")
    print("GST returns already filed covering these dates) before deciding how to correct it.")

    conn.close()


if __name__ == "__main__":
    main()
