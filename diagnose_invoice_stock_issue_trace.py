"""READ-ONLY deep trace. Makes NO changes.

For ONE Sale (looked up by Invoice Number), traces every line item and
shows exactly what happened to it in create_sale()'s stock-posting logic:
  - CREDITED to a Stock Issue line (via SaleStockIssueLinks) - the normal
    path, when the salesperson had an open (Status='Issued') Stock Issue
    for that exact date, with that product already on it, and capacity
    (QtyIssued - QtySold - QtyReturned - QtyFree) still unaccounted-for
  - DEDUCTED DIRECTLY from warehouse stock (a plain InventoryTransactions
    row, RefType='Sale') and parked in DirectSaleReviews for Admin review -
    happens for whatever portion of a line create_sale() could NOT cover
    from an open Stock Issue
  - UNACCOUNTED (neither) - would indicate a real data problem

Also prints every Stock Issue (any status) for the sale's salesperson on
that date, with each line's Issued/Sold-so-far/Available capacity, so you
can see exactly what was open and how much room was left at query time.

This directly answers "why doesn't this invoice show up in / count toward
Stock Issues" - see find_open_stock_issue() and create_sale() in app.py for
the exact rules being traced here. The single most common reason: the Sale
has no EmployeeID (salesperson) attached at all, in which case
find_open_stock_issue() always returns None and 100% of the sale is
deducted directly from warehouse stock, regardless of what Stock Issue was
open that day for that person - a Stock Issue only gets credited by a Sale
that's explicitly tied to that same salesperson.

Usage:
    python diagnose_invoice_stock_issue_trace.py "HHG/2026-27/0320"
"""
import sys
import sqlite3
from db import DB_PATH


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

        print(f"  {l['ProductName']}  (Qty={l['Qty']} {l['Unit']})")
        print(f"      Credited to a Stock Issue     : {credited_qty}" + (f"  (Issue {issue_ids})" if credit_rows else ""))
        print(f"      Deducted directly from warehouse: {direct_qty}"
              + (f"  [Direct Sale Review status: {review_status}]" if review_status else ""))
        if abs(remainder) > 0.01:
            print(f"      *** {remainder} unit(s) UNACCOUNTED FOR (neither credited nor deducted) - "
                  f"needs manual investigation ***")
        print()

    conn.close()


if __name__ == "__main__":
    main()
