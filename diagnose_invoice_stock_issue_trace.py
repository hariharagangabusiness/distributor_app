"""READ-ONLY deep trace. Makes NO changes.

For ONE Sale (looked up by Invoice Number), traces every line item and
shows exactly what happened to it in create_sale()'s stock-posting logic.
Each line's Qty ends up in one of FOUR buckets:
  - CREDITED to a Stock Issue line (via SaleStockIssueLinks) - the normal
    path, when the salesperson had an open (Status='Issued') Stock Issue
    for that exact date, with that product already on it, and capacity
    (QtyIssued - QtySold - QtyReturned - QtyFree) still unaccounted-for
  - COVERED BY AN ALREADY-RECONCILED ISSUE'S CAPACITY - a late sale entered
    AFTER that day's Stock Issue was already reconciled doesn't get a
    SaleStockIssueLinks row at all (that mechanism only exists for an open
    issue) - instead create_sale() checks the reconciled issue's own
    remaining unaccounted-for capacity and, if there's room, silently skips
    posting ANY ledger entry for that portion (it was already deducted once
    when the stock was issued, so posting a second deduction - or a link -
    would double-count it). This is real, intentional stock, it's just not
    recorded anywhere queryable, which is exactly what makes it look like a
    mystery gap. This script recomputes that same capacity check to show it.
  - DEDUCTED DIRECTLY from warehouse stock (a plain InventoryTransactions
    row, RefType='Sale') and parked in DirectSaleReviews for Admin review -
    whatever's left after the two paths above
  - UNACCOUNTED (none of the above) - would indicate a real data problem

Also prints every Stock Issue (any status) for the sale's salesperson on
that date, with each line's Issued/Sold-so-far/Available capacity, so you
can see exactly what was open and how much room was left at query time.

This directly answers "why doesn't this invoice show up in / count toward
Stock Issues" - see find_open_stock_issue() and create_sale() in app.py for
the exact rules being traced here. The two most common reasons: (1) the
Sale has no EmployeeID (salesperson) attached at all, in which case
find_open_stock_issue() always returns None and 100% of the sale is
deducted directly from warehouse stock regardless of what was open that
day; or (2) that day's Stock Issue was already Reconciled by the time this
Sale was entered, so it falls into the "covered by reconciled capacity"
bucket above instead of ever linking back to the issue.

Usage:
    python diagnose_invoice_stock_issue_trace.py "HHG/2026-27/0320"
"""
import sys
import sqlite3
from db import DB_PATH


def true_product_qty_sold(conn, employee_id, sale_date, product_id):
    """Mirrors app.py's true_product_qty_sold(): total Qty sold by this
    employee, on this date, of this product, across every non-cancelled
    Sale - independent of any Stock Issue line's QtySold field."""
    row = conn.execute("""SELECT COALESCE(SUM(sl.Qty), 0) AS q FROM SalesLines sl
                        JOIN Sales s ON s.SaleID = sl.SaleID
                        WHERE s.EmployeeID=? AND s.SaleDate=? AND sl.ProductID=?
                          AND s.Status <> 'Cancelled'""",
                       (employee_id, sale_date, product_id)).fetchone()
    return row["q"] or 0


def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose_invoice_stock_issue_trace.py <InvoiceNumber>")
        return
    invoice_no = sys.argv[1]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sale = conn.execute("""SELECT s.*, e.EmployeeName, c.CustomerName FROM Sales s
                         LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
                         JOIN Customers c ON c.CustomerID = s.CustomerID
                         WHERE s.InvoiceNumber = ?""", (invoice_no,)).fetchone()
    if not sale:
        print(f"No sale found with invoice number '{invoice_no}'.")
        candidates = conn.execute("SELECT InvoiceNumber FROM Sales WHERE InvoiceNumber LIKE ? LIMIT 10",
                                  (f"%{invoice_no}%",)).fetchall()
        if candidates:
            print("Closest matches on invoice number:")
            for c in candidates:
                print(f"  {c['InvoiceNumber']}")
        conn.close()
        return

    print(f"Invoice {sale['InvoiceNumber']}  (SaleID={sale['SaleID']})")
    print(f"  Date            : {sale['SaleDate']}")
    print(f"  Status          : {sale['Status']}")
    print(f"  Customer        : {sale['CustomerName']}")
    print(f"  Salesperson     : {sale['EmployeeName'] or '-- none set --'} (EmployeeID={sale['EmployeeID']})")
    print()

    if sale["Status"] != "Completed":
        print(f"  NOTE: Status is '{sale['Status']}', not 'Completed'. create_sale() only posts ANY stock "
              f"movement (credit to a Stock Issue, OR a direct warehouse deduction) when Status='Completed'. "
              f"A Draft/Cancelled sale posts nothing at all, so its lines won't show up in Stock Issues, "
              f"Direct Sale Review, or the inventory ledger - by itself this fully explains it being "
              f"'excluded'.\n")

    if not sale["EmployeeID"]:
        print("  NOTE: This sale has NO salesperson (EmployeeID) attached. find_open_stock_issue() always "
              "returns None when employee_id is empty, so a sale like this can NEVER credit a Stock Issue, "
              "no matter what was open for anyone that day - every line below was deducted straight from "
              "warehouse stock instead. This is the most common reason an otherwise-ordinary invoice is "
              "'excluded' from Stock Issues: it was entered as a plain counter/customer sale, not against "
              "a route salesperson's stock.\n")

    # Every Stock Issue (any status) for this employee on this exact date, for context -
    # what was actually open, and how much capacity each line had.
    if sale["EmployeeID"]:
        issues = conn.execute("""SELECT * FROM StockIssues WHERE EmployeeID=? AND IssueDate=?
                               ORDER BY IssueID""", (sale["EmployeeID"], sale["SaleDate"])).fetchall()
        print(f"Stock Issues for {sale['EmployeeName']} on {sale['SaleDate']}:")
        if not issues:
            print("  (none - no Stock Issue exists for this employee on this date at all)")
        for iss in issues:
            print(f"  Issue #{iss['IssueID']} - Status={iss['Status']} "
                  f"(ReviewStatus={iss['ReviewStatus']}) - created {iss['CreatedAt']}")
            lines = conn.execute("""SELECT sil.*, p.ProductName FROM StockIssueLines sil
                                  JOIN Products p ON p.ProductID = sil.ProductID
                                  WHERE sil.IssueID=? ORDER BY p.ProductName""", (iss["IssueID"],)).fetchall()
            if not lines:
                print("      (no lines on this issue)")
            for l in lines:
                available = max((l["QtyIssued"] or 0) - (l["QtySold"] or 0)
                                 - (l["QtyReturned"] or 0) - (l["QtyFree"] or 0), 0)
                print(f"      {l['ProductName']}: Issued={l['QtyIssued']}  Sold-so-far={l['QtySold'] or 0}  "
                      f"Returned={l['QtyReturned'] or 0}  Free={l['QtyFree'] or 0}  Available={available}")
        print()

    # The most-recent Reconciled Stock Issue for this employee/date (if any) -
    # mirrors create_sale()'s own `reconciled_issue` lookup exactly.
    reconciled_issue = None
    if sale["EmployeeID"]:
        reconciled_issue = conn.execute("""SELECT * FROM StockIssues WHERE EmployeeID=? AND IssueDate=?
                                         AND Status='Reconciled' ORDER BY IssueID DESC LIMIT 1""",
                                        (sale["EmployeeID"], sale["SaleDate"])).fetchone()

    # Each SalesLine on this invoice - what actually happened to it.
    lines = conn.execute("""SELECT sl.*, p.ProductName, p.Unit FROM SalesLines sl
                          JOIN Products p ON p.ProductID = sl.ProductID
                          WHERE sl.SaleID=? ORDER BY sl.LineID""", (sale["SaleID"],)).fetchall()

    print(f"Tracing {len(lines)} line item(s) on this invoice:\n")
    for l in lines:
        credit_rows = conn.execute("""SELECT ssl.QtyApplied, sil.IssueID FROM SaleStockIssueLinks ssl
                                    JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                                    WHERE ssl.SaleID=? AND sil.ProductID=?""",
                                   (sale["SaleID"], l["ProductID"])).fetchall()
        credited_qty = sum(c["QtyApplied"] for c in credit_rows)
        issue_ids = ", ".join(str(c["IssueID"]) for c in credit_rows) if credit_rows else "-"

        direct_row = conn.execute("""SELECT it.TransactionID, COALESCE(-it.QtyChange, 0) AS q, dsr.Status AS ReviewStatus
                                   FROM InventoryTransactions it
                                   LEFT JOIN DirectSaleReviews dsr ON dsr.TransactionID = it.TransactionID
                                   WHERE it.RefType='Sale' AND it.RefID=? AND it.ProductID=?
                                     AND it.TransactionType='Sale'""",
                                  (sale["SaleID"], l["ProductID"])).fetchone()
        direct_qty = direct_row["q"] if direct_row else 0
        review_status = direct_row["ReviewStatus"] if direct_row else None

        remainder = round((l["Qty"] or 0) - credited_qty - direct_qty, 4)

        # If anything's left unexplained, check whether it matches what create_sale()
        # would have silently covered from an already-Reconciled issue's remaining
        # capacity - that path posts NO SaleStockIssueLinks row and NO separate
        # InventoryTransactions row (the stock already left the warehouse once, when
        # it was originally issued), so it's otherwise invisible to any query.
        covered_qty = 0.0
        if abs(remainder) > 0.01 and reconciled_issue:
            sil2 = conn.execute("""SELECT QtyIssued, QtySold, QtyReturned, QtyFree FROM StockIssueLines
                                 WHERE IssueID=? AND ProductID=?""",
                                (reconciled_issue["IssueID"], l["ProductID"])).fetchone()
            if sil2:
                already_sold_other = round((sil2["QtySold"] or 0)
                                            + true_product_qty_sold(conn, sale["EmployeeID"], sale["SaleDate"], l["ProductID"])
                                            - (l["Qty"] or 0), 4)
                available2 = max((sil2["QtyIssued"] or 0) - (sil2["QtyReturned"] or 0)
                                  - (sil2["QtyFree"] or 0) - already_sold_other, 0)
                covered_qty = round(min(remainder, available2), 4)
        remainder = round(remainder - covered_qty, 4)

        print(f"  {l['ProductName']}  (Qty={l['Qty']} {l['Unit']})")
        print(f"      Credited to a Stock Issue        : {credited_qty}" + (f"  (Issue {issue_ids})" if credit_rows else ""))
        if covered_qty > 0.01:
            print(f"      Covered by reconciled Issue #{reconciled_issue['IssueID']}'s remaining capacity: {covered_qty}"
                  f"  (no ledger entry posted - avoids double-deducting stock already accounted for at reconciliation)")
        print(f"      Deducted directly from warehouse : {direct_qty}"
              + (f"  [Direct Sale Review status: {review_status}]" if review_status else ""))
        if abs(remainder) > 0.01:
            print(f"      *** {remainder} unit(s) UNACCOUNTED FOR (neither credited, covered, nor deducted) - "
                  f"needs manual investigation ***")
        print()

    conn.close()


if __name__ == "__main__":
    main()
