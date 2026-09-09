"""READ-ONLY deep trace. Makes NO changes.

For a given product/date, and (optionally) an employee name, traces EVERY
Sale that sold this product today and shows exactly what happened to each
line's quantity:
  - CREDITED to a Stock Issue line (via SaleStockIssueLinks) - the normal,
    expected path when an open Stock Issue existed for that employee/date
    at the moment the Sale was saved
  - DEDUCTED DIRECTLY from warehouse stock (a plain InventoryTransactions
    row, RefType='Sale') - happens when NO open Stock Issue existed yet
    for that employee/date/product at the moment that particular Sale was
    saved (e.g. the sale was entered before the day's Stock Issue existed,
    or a Stock Issue line for this product hadn't been added to it yet)
  - UNACCOUNTED (neither) - would indicate a real data problem, flagged
    clearly if found

Also lists every Stock Issue (any status) for the employee on that date,
so a case where a SECOND issue was created partway through the day (and
some sales credited against a different issue's line than expected) is
visible too.

Usage:
    python diagnose_sale_credit_trace.py
    python diagnose_sale_credit_trace.py "Product Name" 2026-09-09 "Employee Name"
"""
import sys
import sqlite3
from datetime import date
from db import DB_PATH

DEFAULT_PRODUCT = "TATA Copper+ 2000ml Shrink pack9"


def main():
    product_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PRODUCT
    report_date = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
    employee_filter = sys.argv[3] if len(sys.argv) > 3 else None

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
            print(f"Could not resolve a single product matching '{product_name}'.")
            conn.close()
            return

    print(f"Product: {product['ProductName']} (ProductID={product['ProductID']})  |  Date: {report_date}\n")

    # Every Stock Issue (any status) for employees active on this product/date, for context.
    all_issues = conn.execute("""
        SELECT si.IssueID, si.EmployeeID, e.EmployeeName, si.Status, si.ReviewStatus, si.CreatedAt
        FROM StockIssues si JOIN Employees e ON e.EmployeeID = si.EmployeeID
        WHERE si.IssueDate = ?
        ORDER BY si.EmployeeID, si.IssueID
    """, (report_date,)).fetchall()
    print("All Stock Issues today (any status), for context:")
    for i in all_issues:
        if employee_filter and employee_filter.strip().lower() not in (i["EmployeeName"] or "").strip().lower():
            continue
        created = i["CreatedAt"] if "CreatedAt" in i.keys() else "?"
        print(f"  Issue #{i['IssueID']} - {i['EmployeeName']} - {i['Status']}"
              f"{' (Pending Review)' if i['ReviewStatus']=='Pending' else ''}")

    # Every Sale line for this product today.
    sale_rows = conn.execute("""
        SELECT sl.SaleID, s.InvoiceNumber, s.EmployeeID, e.EmployeeName, s.Status AS SaleStatus,
               c.CustomerName, sl.Qty
        FROM SalesLines sl
        JOIN Sales s ON s.SaleID = sl.SaleID
        JOIN Customers c ON c.CustomerID = s.CustomerID
        LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
        WHERE sl.ProductID = ? AND s.SaleDate = ? AND s.Status <> 'Cancelled'
        ORDER BY sl.SaleID
    """, (product["ProductID"], report_date)).fetchall()

    print(f"\nTracing each of {len(sale_rows)} Sale line(s) for this product today:\n")
    total_credited = total_direct_deduction = total_unaccounted = 0.0
    for r in sale_rows:
        if employee_filter and employee_filter.strip().lower() not in (r["EmployeeName"] or "").strip().lower():
            continue
        credit = conn.execute("""
            SELECT ssl.QtyApplied, sil.IssueID FROM SaleStockIssueLinks ssl
            JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
            WHERE ssl.SaleID = ? AND sil.ProductID = ?
        """, (r["SaleID"], product["ProductID"])).fetchall()
        credited_qty = sum(c["QtyApplied"] for c in credit)

        direct_tx = conn.execute("""
            SELECT COALESCE(SUM(-QtyChange), 0) q FROM InventoryTransactions
            WHERE RefType='Sale' AND RefID=? AND ProductID=? AND TransactionType='Sale'
        """, (r["SaleID"], product["ProductID"])).fetchone()["q"]

        remainder = round((r["Qty"] or 0) - credited_qty - direct_tx, 4)
        total_credited += credited_qty
        total_direct_deduction += direct_tx
        total_unaccounted += remainder

        issue_ids = ", ".join(str(c["IssueID"]) for c in credit) if credit else "-"
        flag = f"  <-- {remainder} units UNACCOUNTED FOR (neither credited nor deducted directly)" \
               if abs(remainder) > 0.01 else ""
        print(f"  {r['InvoiceNumber']} (SaleID={r['SaleID']}) -> {r['CustomerName']}: Qty={r['Qty']}  "
              f"credited={credited_qty} (Issue {issue_ids})  direct-warehouse-deduction={direct_tx}{flag}")

    print(f"\nTotals: credited={round(total_credited,2)}, direct-warehouse-deduction={round(total_direct_deduction,2)}, "
          f"unaccounted={round(total_unaccounted,2)}")
    if total_direct_deduction > 0:
        print(f"\n  -> {round(total_direct_deduction,2)} unit(s) were sold and deducted DIRECTLY from warehouse "
              f"stock rather than credited to any Stock Issue line - this happens when a Sale was saved before "
              f"an open Stock Issue existed for that employee/date/product (e.g. entered before the day's Stock "
              f"Issue was created, or before this product's line was added to it). This is a SEPARATE, valid "
              f"stock movement - it does not need to be 'fixed', but it explains why Qty Sold on the Stock Issue "
              f"line looks lower than the true total sold: the difference already left the warehouse through a "
              f"different, correctly-accounted path.")
    if total_unaccounted > 0.01:
        print(f"\n  -> {round(total_unaccounted,2)} unit(s) are genuinely unaccounted for - neither credited nor "
              f"deducted. This would need manual investigation.")

    conn.close()


if __name__ == "__main__":
    main()
