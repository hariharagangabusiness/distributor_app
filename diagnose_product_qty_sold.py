"""READ-ONLY diagnostic. Makes NO changes.

For a given product and date (defaults to today and "TATA Copper+ 2000ml
Shrink pack9"), shows EVERY angle on how much was actually sold, so a
mismatch like "reconciliation only shows Qty Sold 20" can be traced to
its real cause:

  1. Every Stock Issue today with a line for this product - QtyIssued,
     QtySold (as currently saved), QtyReturned, QtyFree, and how much of
     that QtySold is backed by SaleStockIssueLinks (i.e. actually credited
     from a direct Sales-tab entry) vs. typed directly on the Reconcile
     form.
  2. The TRUE total sold today for this product, computed independently
     from SalesLines across every Sale today (the ground truth - this
     doesn't rely on StockIssueLines.QtySold at all).
  3. A flag if StockIssueLines.QtySold looks LOWER than what
     SaleStockIssueLinks says was credited to it - which happens when
     Reconcile is submitted with a typed-over Qty Sold value that's
     smaller than the pre-filled, already-credited amount (Reconcile's
     qty_sold_<LineID> form field REPLACES QtySold, it does not add to
     it - so if the reconciler cleared/retyped a smaller number, the real
     credited quantity gets silently overwritten downward).

Usage:
    python diagnose_product_qty_sold.py
    python diagnose_product_qty_sold.py "Product Name" 2026-09-09
"""
import sys
import sqlite3
from datetime import date
from db import DB_PATH

DEFAULT_PRODUCT = "TATA Copper+ 2000ml Shrink pack9"


def main():
    product_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PRODUCT
    report_date = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    product = conn.execute("SELECT ProductID, ProductName, Unit FROM Products WHERE ProductName=?",
                           (product_name,)).fetchone()
    if not product:
        candidates = conn.execute("SELECT ProductID, ProductName FROM Products WHERE ProductName LIKE ?",
                                  (f"%{product_name}%",)).fetchall()
        if len(candidates) == 1:
            product = conn.execute("SELECT ProductID, ProductName, Unit FROM Products WHERE ProductID=?",
                                   (candidates[0]["ProductID"],)).fetchone()
        else:
            print(f"Could not resolve a single product matching '{product_name}'. Candidates:")
            for c in candidates:
                print(f"  - {c['ProductName']}")
            conn.close()
            return

    print(f"Product: {product['ProductName']} (ProductID={product['ProductID']})  |  Date: {report_date}\n")

    # 1. Every Stock Issue line for this product today
    lines = conn.execute("""
        SELECT sil.LineID, sil.IssueID, sil.QtyIssued, sil.QtySold, sil.QtyReturned, sil.QtyFree,
               sil.UnitPrice, sil.DiscountAmount, si.Status, si.ReviewStatus, si.EmployeeID, e.EmployeeName
        FROM StockIssueLines sil
        JOIN StockIssues si ON si.IssueID = sil.IssueID
        JOIN Employees e ON e.EmployeeID = si.EmployeeID
        WHERE sil.ProductID = ? AND si.IssueDate = ?
        ORDER BY si.IssueID
    """, (product["ProductID"], report_date)).fetchall()

    if not lines:
        print("No Stock Issue has a line for this product on this date.")
    else:
        print("Stock Issue lines for this product today:")
        for l in lines:
            credited = conn.execute(
                "SELECT COALESCE(SUM(QtyApplied),0) q FROM SaleStockIssueLinks WHERE StockIssueLineID=?",
                (l["LineID"],)).fetchone()["q"]
            flag = ""
            if l["QtySold"] is not None and credited > (l["QtySold"] or 0) + 0.001:
                flag = f"  <-- MISMATCH: {credited} were credited via direct Sales, but QtySold is only " \
                       f"{l['QtySold']}. Reconcile was likely submitted with a smaller typed-in Qty Sold " \
                       f"that overwrote the real, already-credited amount."
            print(f"  Issue #{l['IssueID']} ({l['EmployeeName']}, {l['Status']}"
                  f"{', Pending Review' if l['ReviewStatus']=='Pending' else ''}): "
                  f"QtyIssued={l['QtyIssued']}, QtySold={l['QtySold']}, QtyReturned={l['QtyReturned']}, "
                  f"QtyFree={l['QtyFree']}, credited-from-direct-Sales={credited}{flag}")

    # 2. Ground truth: total actually sold today, from SalesLines directly (independent of
    #    StockIssueLines entirely - this is what really left the shop).
    sale_rows = conn.execute("""
        SELECT c.CustomerName, s.InvoiceNumber, s.SaleID, s.EmployeeID, e.EmployeeName, sl.Qty
        FROM SalesLines sl
        JOIN Sales s ON s.SaleID = sl.SaleID
        JOIN Customers c ON c.CustomerID = s.CustomerID
        LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
        WHERE sl.ProductID = ? AND s.SaleDate = ? AND s.Status <> 'Cancelled'
        ORDER BY s.SaleID
    """, (product["ProductID"], report_date)).fetchall()

    true_total = sum(r["Qty"] or 0 for r in sale_rows)
    print(f"\nGround truth from Sales/SalesLines (every invoice today, any source): "
          f"{round(true_total, 2)} {product['Unit'] or ''} across {len(sale_rows)} line item(s)")
    for r in sale_rows:
        print(f"  {r['InvoiceNumber']} -> {r['CustomerName']}: {r['Qty']} "
              f"{'(via ' + r['EmployeeName'] + ')' if r['EmployeeName'] else '(no salesperson / direct)'}")

    stock_issue_qty_sold_total = sum((l["QtySold"] or 0) for l in lines)
    print(f"\nSum of StockIssueLines.QtySold across all of today's issues for this product: "
          f"{round(stock_issue_qty_sold_total, 2)}")
    if abs(stock_issue_qty_sold_total - true_total) > 0.01:
        print(f"  <-- These do not match (difference of {round(true_total - stock_issue_qty_sold_total, 2)}). "
              f"See the MISMATCH line(s) above for the likely cause.")
    else:
        print("  These match - no discrepancy found for this product/date.")

    conn.close()


if __name__ == "__main__":
    main()
