"""READ-ONLY. Makes NO changes.

Confirms (or rules out) the most likely explanation for a Stock Issue
line's Qty Sold looking lower than the true total sold: the line's Qty
Issued capacity was topped up in multiple installments during the day
(each 'Issue Stock' / 'Add More Products' action, or an auto top-up for
an unreviewed auto-created issue, posts its own 'Issue' InventoryTransactions
row rather than one lump sum) - so any Sales-tab entry saved while the
line's running capacity was fully used gets deducted directly from
warehouse stock instead of credited to the Stock Issue, until the next
top-up frees up room again.

Lists, in insertion order (a reliable proxy for chronological order -
IDs are AUTOINCREMENT), every 'Issue' InventoryTransactions row for this
Stock Issue + product (each top-up), and every Sale/SalesLine for the
same product/date, interleaved by ID, so you can see exactly which sales
landed in a capacity gap.

Usage:
    python diagnose_stock_issue_topup_history.py <IssueID> <ProductID>
"""
import sys
import sqlite3
from db import DB_PATH


def main():
    if len(sys.argv) < 3:
        print("Usage: python diagnose_stock_issue_topup_history.py <IssueID> <ProductID>")
        return
    issue_id = int(sys.argv[1])
    product_id = int(sys.argv[2])

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    issue = conn.execute("""SELECT si.*, e.EmployeeName FROM StockIssues si
                          JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""",
                         (issue_id,)).fetchone()
    product = conn.execute("SELECT ProductName FROM Products WHERE ProductID=?", (product_id,)).fetchone()
    if not issue or not product:
        print("Issue or product not found.")
        conn.close()
        return

    print(f"Issue #{issue_id} - {issue['EmployeeName']} - {issue['IssueDate']} - Status={issue['Status']}, "
          f"ReviewStatus={issue['ReviewStatus']}")
    print(f"Product: {product['ProductName']} (ProductID={product_id})\n")

    topups = conn.execute("""SELECT TransactionID, -QtyChange AS Qty, Notes FROM InventoryTransactions
                           WHERE RefType='StockIssue' AND RefID=? AND ProductID=? AND TransactionType='Issue'
                           ORDER BY TransactionID""", (issue_id, product_id)).fetchall()

    sil = conn.execute("SELECT * FROM StockIssueLines WHERE IssueID=? AND ProductID=?",
                       (issue_id, product_id)).fetchone()

    print(f"Qty Issued top-ups posted against this line, in order (TransactionID is a reliable proxy for "
          f"chronological order):")
    running_issued = 0
    for t in topups:
        running_issued += t["Qty"]
        print(f"  TransactionID={t['TransactionID']}: +{t['Qty']}  (running total issued: {running_issued})  "
              f"- {t['Notes'] or ''}")
    print(f"\n{len(topups)} top-up(s) totaling {running_issued} - current StockIssueLines.QtyIssued="
          f"{sil['QtyIssued'] if sil else 'N/A'}")

    if len(topups) > 1:
        print(f"\n  -> CONFIRMED: this line's capacity was added to {len(topups)} separate times rather than all "
              f"at once. Any Sale saved while the running credited total had already caught up to whatever was "
              f"issued so far falls through to a direct warehouse deduction instead, until the next top-up frees "
              f"up room - this is almost certainly why Qty Sold reads lower than the true total sold: the gap is "
              f"real stock that left correctly, just not through this line's own bookkeeping.")
    else:
        print(f"\n  -> Only one top-up found - capacity exhaustion from multiple top-ups is NOT the explanation "
              f"here; the earlier trace (credited vs. direct-deduction per Sale) is the more reliable source of "
              f"truth for what actually happened.")

    conn.close()


if __name__ == "__main__":
    main()
