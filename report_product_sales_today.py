"""READ-ONLY report: for a given product (default: 'TATA Copper+ 2000ml
Shrink pack9') and date (default: today), lists every customer who bought
it, how much, and the totals. Makes no changes.

Usage (defaults to today + the product named above):
    python report_product_sales_today.py
    python report_product_sales_today.py "Product Name" 2026-09-09
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
        # fall back to a fuzzy match in case of a slight name mismatch
        candidates = conn.execute("SELECT ProductID, ProductName FROM Products WHERE ProductName LIKE ?",
                                  (f"%{product_name}%",)).fetchall()
        if not candidates:
            print(f"No product found matching '{product_name}'.")
            conn.close()
            return
        if len(candidates) > 1:
            print(f"Multiple products match '{product_name}':")
            for c in candidates:
                print(f"  - {c['ProductName']}")
            print("Re-run with the exact name.")
            conn.close()
            return
        product = conn.execute("SELECT ProductID, ProductName, Unit FROM Products WHERE ProductID=?",
                               (candidates[0]["ProductID"],)).fetchone()

    rows = conn.execute("""
        SELECT c.CustomerName, s.InvoiceNumber, s.SaleID, sl.Qty, sl.UnitPrice, sl.DiscountAmount,
               (sl.TaxableValue + sl.CGSTAmount + sl.SGSTAmount + sl.IGSTAmount) AS LineTotal,
               e.EmployeeName
        FROM SalesLines sl
        JOIN Sales s ON s.SaleID = sl.SaleID
        JOIN Customers c ON c.CustomerID = s.CustomerID
        LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
        WHERE sl.ProductID = ? AND s.SaleDate = ? AND s.Status <> 'Cancelled'
        ORDER BY c.CustomerName
    """, (product["ProductID"], report_date)).fetchall()

    if not rows:
        print(f"No sales of '{product['ProductName']}' found on {report_date}.")
        conn.close()
        return

    by_customer = {}
    for r in rows:
        key = r["CustomerName"]
        by_customer.setdefault(key, {"qty": 0.0, "amount": 0.0, "invoices": []})
        by_customer[key]["qty"] += r["Qty"] or 0
        by_customer[key]["amount"] += r["LineTotal"] or 0
        by_customer[key]["invoices"].append((r["InvoiceNumber"], r["Qty"], r["EmployeeName"]))

    total_qty = sum(v["qty"] for v in by_customer.values())
    total_amount = sum(v["amount"] for v in by_customer.values())

    print(f"Product: {product['ProductName']}  |  Date: {report_date}")
    print(f"Customers: {len(by_customer)}   Total Qty Sold: {round(total_qty, 2)} {product['Unit'] or ''}   "
          f"Total Value: Rs {round(total_amount, 2)}\n")
    print(f"{'Customer':<35} {'Qty':>10} {'Value (Rs)':>14}   Invoices")
    print("-" * 90)
    for name, v in sorted(by_customer.items(), key=lambda x: -x[1]["qty"]):
        invoice_str = ", ".join(f"{inv}({qty})" + (f" via {emp}" if emp else "") for inv, qty, emp in v["invoices"])
        print(f"{name:<35} {round(v['qty'], 2):>10} {round(v['amount'], 2):>14}   {invoice_str}")

    conn.close()


if __name__ == "__main__":
    main()
