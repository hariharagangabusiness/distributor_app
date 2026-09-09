"""READ-ONLY. Makes NO changes.

Looks up a Sale by its InvoiceNumber and prints full details: customer, salesperson,
date, status, payment info, line items, and (if applicable) which Stock Issue it's
linked to.

Usage:
    python lookup_invoice.py "HG/2026-27/0067"
"""
import sys
import sqlite3
from db import DB_PATH


def main():
    if len(sys.argv) < 2:
        print('Usage: python lookup_invoice.py "HG/2026-27/0067"')
        return
    invoice_no = sys.argv[1]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sale = conn.execute("""
        SELECT s.*, c.CustomerName, c.Phone AS CustomerPhone, e.EmployeeName
        FROM Sales s
        JOIN Customers c ON c.CustomerID = s.CustomerID
        LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
        WHERE s.InvoiceNumber = ?
    """, (invoice_no,)).fetchone()

    if not sale:
        print(f"No sale found with invoice number '{invoice_no}'.")
        # Helpful fallback: nearby invoice numbers, in case of a typo
        similar = conn.execute("SELECT InvoiceNumber FROM Sales WHERE InvoiceNumber LIKE ? ORDER BY InvoiceNumber",
                               (f"%{invoice_no[-8:]}%",)).fetchall()
        if similar:
            print("Similar invoice numbers found:")
            for s in similar:
                print(f"  - {s['InvoiceNumber']}")
        conn.close()
        return

    print(f"Invoice: {sale['InvoiceNumber']}  (SaleID={sale['SaleID']})")
    print(f"Date: {sale['SaleDate']}   Status: {sale['Status']}")
    print(f"Customer: {sale['CustomerName']}" + (f" ({sale['CustomerPhone']})" if sale['CustomerPhone'] else ""))
    print(f"Salesperson: {sale['EmployeeName'] or '-'}")
    print(f"Payment Status: {sale['PaymentStatus']}   Amount Received: Rs {sale['AmountReceived'] or 0}")
    print(f"Cash: Rs {sale['CashAmount'] or 0}   Bank: Rs {sale['BankAmount'] or 0}")
    print(f"Total Amount: Rs {sale['TotalAmount'] or 0}")
    if sale["Notes"]:
        print(f"Notes: {sale['Notes']}")

    lines = conn.execute("""
        SELECT sl.*, pr.ProductName, pr.Unit
        FROM SalesLines sl JOIN Products pr ON pr.ProductID = sl.ProductID
        WHERE sl.SaleID = ?
    """, (sale["SaleID"],)).fetchall()

    print(f"\nLine items ({len(lines)}):")
    for l in lines:
        line_total = (l["TaxableValue"] or 0) + (l["CGSTAmount"] or 0) + (l["SGSTAmount"] or 0) + (l["IGSTAmount"] or 0)
        print(f"  {l['ProductName']}: Qty={l['Qty']} {l['Unit'] or ''}  Rate=Rs {l['UnitPrice']}  "
              f"Discount=Rs {l['DiscountAmount'] or 0}  Line Total=Rs {round(line_total, 2)}")

    linked_issue = conn.execute("""
        SELECT DISTINCT sil.IssueID FROM SaleStockIssueLinks ssl
        JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
        WHERE ssl.SaleID = ?
    """, (sale["SaleID"],)).fetchall()
    if linked_issue:
        print(f"\nCredited against Stock Issue(s): {', '.join(str(r['IssueID']) for r in linked_issue)}")

    conn.close()


if __name__ == "__main__":
    main()
