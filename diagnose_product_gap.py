"""Read-only diagnostic: prints the FULL reason behind a product's Inventory
Reconciliation Gap - everything the /inventory/<pid>/reconciliation detail
page shows, plus a day-by-day breakdown of exactly which Stock Issue(s) the
Unaccounted quantity comes from, in one script so the output can be pasted
back for analysis. Makes NO changes to any table - pure SELECTs.

Usage:
    python3 diagnose_product_gap.py "TATA Copper"
(matches ProductName LIKE %arg%; if more than one product matches, prints
the list and exits so you can re-run with a more specific name/SKU).
"""
import sys
import sqlite3
from db import DB_PATH


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 diagnose_product_gap.py \"<product name or part of it>\"")
        sys.exit(1)
    needle = sys.argv[1]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    matches = conn.execute(
        "SELECT ProductID, ProductName, SKU, Unit FROM Products WHERE ProductName LIKE ? OR SKU LIKE ? ORDER BY ProductName",
        (f"%{needle}%", f"%{needle}%")
    ).fetchall()
    if not matches:
        print(f"No product matched '{needle}'.")
        return
    if len(matches) > 1:
        print(f"{len(matches)} products matched '{needle}' - re-run with a more specific name:")
        for m in matches:
            print(f"  [{m['ProductID']}] {m['ProductName']} (SKU: {m['SKU']})")
        return

    product = matches[0]
    pid = product["ProductID"]
    print("=" * 100)
    print(f"PRODUCT: {product['ProductName']}  (ProductID {pid}, SKU {product['SKU']}, Unit {product['Unit']})")
    print("=" * 100)

    # ---- Bucket breakdown (same as summary/detail page) ----
    buckets = {}
    for r in conn.execute(
        "SELECT TransactionType, SUM(QtyChange) amt FROM InventoryTransactions WHERE ProductID=? GROUP BY TransactionType",
        (pid,)
    ):
        buckets[r["TransactionType"]] = round(r["amt"] or 0, 2)
    ledger_stock = round(sum(buckets.values()), 2)

    print("\n--- Ledger bucket breakdown (all-time) ---")
    for k in ["Opening Stock", "Purchase", "Issue", "Return-In", "Free Scheme", "Sale",
              "Adjustment-In", "Adjustment-Out"]:
        if k in buckets:
            print(f"  {k:<16} {buckets[k]:>12,.2f}")
    for k, v in buckets.items():
        if k not in ["Opening Stock", "Purchase", "Issue", "Return-In", "Free Scheme", "Sale",
                     "Adjustment-In", "Adjustment-Out"]:
            print(f"  {k:<16} {v:>12,.2f}   <-- unexpected transaction type, worth a look")
    print(f"  {'LEDGER STOCK':<16} {ledger_stock:>12,.2f}   (this is what Inventory list/report show as current stock)")

    # ---- true_product_qty_sold lookup, grouped ----
    true_sold = {}
    for r in conn.execute("""
        SELECT s.EmployeeID, s.SaleDate, sl.ProductID, SUM(sl.Qty) q
        FROM SalesLines sl JOIN Sales s ON s.SaleID = sl.SaleID
        WHERE s.Status <> 'Cancelled'
        GROUP BY s.EmployeeID, s.SaleDate, sl.ProductID
    """):
        true_sold[(r["EmployeeID"], r["SaleDate"], r["ProductID"])] = r["q"] or 0

    # ---- Corrected lookup: units still billed on a Stock Issue's own auto-created
    # "Unassigned" invoice (StockIssues.SaleID), which true_sold above can NEVER see
    # because that invoice is deliberately created with no EmployeeID. This is the fix
    # for the day-by-day report overstating the gap. ----
    unassigned_sold = {}
    for r in conn.execute("""
        SELECT si.IssueID, sl.ProductID, SUM(sl.Qty) q
        FROM StockIssues si
        JOIN Sales s ON s.SaleID = si.SaleID
        JOIN SalesLines sl ON sl.SaleID = s.SaleID
        WHERE si.SaleID IS NOT NULL AND s.Status <> 'Cancelled'
        GROUP BY si.IssueID, sl.ProductID
    """):
        unassigned_sold[(r["IssueID"], r["ProductID"])] = r["q"] or 0

    # ---- Second correction: subtract out genuine Direct Sale deductions from the same
    # employee/date/product, which true_sold above also (wrongly) counts as if it came
    # from the issue, double-crediting it. ----
    direct_sold = {}
    for r in conn.execute("""
        SELECT s.EmployeeID, s.SaleDate, it.ProductID, SUM(-it.QtyChange) q
        FROM InventoryTransactions it
        JOIN Sales s ON s.SaleID = it.RefID AND it.RefType = 'Sale'
        WHERE it.TransactionType = 'Sale' AND s.Status <> 'Cancelled'
        GROUP BY s.EmployeeID, s.SaleDate, it.ProductID
    """):
        direct_sold[(r["EmployeeID"], r["SaleDate"], r["ProductID"])] = r["q"] or 0

    # ---- Day-by-day Stock Issue breakdown ----
    issue_lines = conn.execute("""
        SELECT sil.*, si.EmployeeID, si.IssueDate, si.Status, si.IssueID, e.EmployeeName
        FROM StockIssueLines sil
        JOIN StockIssues si ON si.IssueID = sil.IssueID
        JOIN Employees e ON e.EmployeeID = si.EmployeeID
        WHERE sil.ProductID=?
        ORDER BY si.IssueDate, si.IssueID
    """, (pid,)).fetchall()

    print("\n--- Day-by-day Stock Issue breakdown (OLD figure vs CORRECTED figure) ---")
    print(f"{'Date':<12} {'Salesperson':<20} {'Status':<11} {'Issued':>7} {'OldTrueSold':>11} "
          f"{'+Unassign':>10} {'-DirectSale':>11} {'=NewSold':>9} {'Returned':>9} {'Free':>5} "
          f"{'OldUnacc':>9} {'NewUnacc':>9}")
    total_unaccounted_old = 0.0
    total_unaccounted_new = 0.0
    total_pending = 0.0
    flagged_days = []
    for line in issue_lines:
        if line["Status"] == "Reconciled":
            key = (line["EmployeeID"], line["IssueDate"], pid)
            old_true_qty = true_sold.get(key, 0)
            unassigned_qty = unassigned_sold.get((line["IssueID"], pid), 0)
            direct_qty = direct_sold.get(key, 0)
            new_true_qty = old_true_qty + unassigned_qty - direct_qty
            old_unacc = round((line["QtyIssued"] or 0) - old_true_qty - (line["QtyReturned"] or 0) - (line["QtyFree"] or 0), 2)
            new_unacc = round((line["QtyIssued"] or 0) - new_true_qty - (line["QtyReturned"] or 0) - (line["QtyFree"] or 0), 2)
            total_unaccounted_old = round(total_unaccounted_old + old_unacc, 2)
            total_unaccounted_new = round(total_unaccounted_new + new_unacc, 2)
            print(f"{line['IssueDate']:<12} {line['EmployeeName']:<20} {'Reconciled':<11} "
                  f"{line['QtyIssued'] or 0:>7,.1f} {old_true_qty:>11,.1f} {unassigned_qty:>10,.1f} "
                  f"{direct_qty:>11,.1f} {new_true_qty:>9,.1f} {line['QtyReturned'] or 0:>9,.1f} "
                  f"{line['QtyFree'] or 0:>5,.1f} {old_unacc:>9,.1f} {new_unacc:>9,.1f}"
                  + ("   <== still contributes to Gap" if abs(new_unacc) > 0.01 else ""))
            if abs(new_unacc) > 0.01:
                flagged_days.append((line["IssueDate"], line["EmployeeName"], line["IssueID"], new_unacc))
        else:
            total_pending = round(total_pending + (line["QtyIssued"] or 0), 2)
            print(f"{line['IssueDate']:<12} {line['EmployeeName']:<20} {'Pending':<11} "
                  f"{line['QtyIssued'] or 0:>7,.1f} {'--':>11} {'--':>10} {'--':>11} {'--':>9} {'--':>9} "
                  f"{'--':>5} {'not reconciled yet':>9}")

    print(f"\n  TOTAL UNACCOUNTED - OLD (buggy) figure: {total_unaccounted_old:,.2f}")
    print(f"  TOTAL UNACCOUNTED - CORRECTED figure  : {total_unaccounted_new:,.2f}   <-- trust this one")
    print(f"  TOTAL PENDING (issued, not yet reconciled - not a problem): {total_pending:,.2f}")

    # ---- Total Units Sold / Gap section (same formula as the app) ----
    total_units_sold = conn.execute(
        "SELECT COALESCE(SUM(sl.Qty),0) q FROM SalesLines sl JOIN Sales s ON s.SaleID=sl.SaleID "
        "WHERE sl.ProductID=? AND s.Status <> 'Cancelled'", (pid,)
    ).fetchone()["q"] or 0
    total_units_sold = round(total_units_sold, 2)
    direct_sale_total = round(buckets.get("Sale", 0), 2)  # stored negative
    sold_via_issue = round(total_units_sold - abs(direct_sale_total), 2)
    should_be_sold = round(-buckets.get("Issue", 0) - buckets.get("Return-In", 0) + (-buckets.get("Free Scheme", 0)), 2)
    sales_gap = round(should_be_sold - sold_via_issue, 2)

    print("\n--- Total Units Sold (true, all-time) / Gap ---")
    print(f"  Total units sold (all-time, ground truth)      : {total_units_sold:,.2f}")
    print(f"  ...of which via Direct Sale (warehouse-direct)  : {abs(direct_sale_total):,.2f}")
    print(f"  ...of which via stock issued to a salesperson   : {sold_via_issue:,.2f}")
    print(f"  Should-be-sold (Issued - Returned - Free)       : {should_be_sold:,.2f}")
    print(f"  GAP (Should-be-sold - sold_via_issue)           : {sales_gap:,.2f}")

    if flagged_days:
        print(f"\n  --> {len(flagged_days)} reconciled day(s) actually account for this Gap:")
        for d, emp, issue_id, unacc in flagged_days:
            print(f"      {d}  {emp}  (StockIssue #{issue_id})  Unaccounted = {unacc:,.2f}")

    # ---- Direct Sale transactions for this product ----
    direct_rows = conn.execute("""
        SELECT it.TransactionID, it.TransactionDate, it.QtyChange, it.RefID AS SaleID, it.Notes,
               s.CustomerID, c.CustomerName, s.EmployeeID, e.EmployeeName, s.Status AS SaleStatus
        FROM InventoryTransactions it
        LEFT JOIN Sales s ON s.SaleID = it.RefID AND it.RefType='Sale'
        LEFT JOIN Customers c ON c.CustomerID = s.CustomerID
        LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
        WHERE it.ProductID=? AND it.TransactionType='Sale'
        ORDER BY it.TransactionDate DESC, it.TransactionID DESC
    """, (pid,)).fetchall()
    print(f"\n--- Direct Sale transactions ({len(direct_rows)} total, {abs(direct_sale_total):,.2f} units) ---")
    for r in direct_rows:
        sale_note = "SALE NOT FOUND (deleted?)" if r["SaleID"] and not r["CustomerName"] and not r["SaleStatus"] else ""
        print(f"  {r['TransactionDate']}  TxnID {r['TransactionID']:<6}  qty {abs(r['QtyChange']):>8,.2f}  "
              f"SaleID {r['SaleID']}  Customer: {r['CustomerName'] or '-'}  "
              f"Salesperson: {r['EmployeeName'] or '-'}  {sale_note}")

    # ---- Direct Sale Review status for these transactions ----
    review_counts = conn.execute("""
        SELECT dsr.Status, COUNT(*) c FROM DirectSaleReviews dsr WHERE dsr.ProductID=? GROUP BY dsr.Status
    """, (pid,)).fetchall()
    if review_counts:
        print("\n--- Direct Sale Review queue status for this product ---")
        for rc in review_counts:
            print(f"  {rc['Status']}: {rc['c']}")

    # ---- Manual Adjustments (often the actual culprit once the two known bugs are ruled out) ----
    adjustments = conn.execute("""
        SELECT TransactionID, TransactionDate, TransactionType, QtyChange, RefType, RefID, Notes
        FROM InventoryTransactions
        WHERE ProductID=? AND TransactionType IN ('Adjustment-In','Adjustment-Out')
        ORDER BY TransactionDate DESC, TransactionID DESC
    """, (pid,)).fetchall()
    if adjustments:
        print(f"\n--- Manual/correction Adjustments ({len(adjustments)}) ---")
        for a in adjustments:
            print(f"  {a['TransactionDate']}  TxnID {a['TransactionID']:<6}  {a['TransactionType']:<15} "
                  f"qty {a['QtyChange']:>10,.2f}  RefType={a['RefType']:<14} RefID={a['RefID']}  {a['Notes'] or ''}")

    print("\n" + "=" * 100)
    print("Done. Paste this whole output back for analysis.")
    conn.close()


if __name__ == "__main__":
    main()
