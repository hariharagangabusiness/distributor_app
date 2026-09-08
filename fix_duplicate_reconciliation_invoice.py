"""Corrects FULL-DUPLICATE auto-created reconciliation Sales - ones where
EVERY unit on the auto-Sale was already billed on a direct Sales-tab entry
(SaleStockIssueLinks), so the auto-Sale should never have existed.

By default this is a DRY RUN: it prints exactly what it would do and
changes nothing. Pass --apply to actually perform the fix.

For each affected Stock Issue, applies the exact same safety gates as
check_duplicate_invoice_safety.py (still billed to Unassigned, no due
payments, no scheme claim progress, no custom field data) and REFUSES to
touch anything that doesn't pass - print a message instead so it can be
handled manually.

What it does for each issue that passes:
  1. Deletes the auto-Sale's SalesLines rows
  2. Deletes the auto-Sale (Sales row) itself
  3. Sets StockIssues.SaleID = NULL for that issue
  (No InventoryTransactions exist for this Sale to reverse - it was always
  created with post_inventory=False, since the Stock Issue's own Issue/
  Return-In/Free-Scheme transactions already cover the stock movement.)

Run against PRODUCTION via `railway ssh`:
    python fix_duplicate_reconciliation_invoice.py            # dry run
    python fix_duplicate_reconciliation_invoice.py --apply    # actually fix
"""
import sys
import sqlite3
from db import DB_PATH


def find_full_duplicates(conn):
    unassigned = conn.execute("SELECT CustomerID FROM Customers WHERE IsUnassignedBucket=1").fetchone()
    unassigned_id = unassigned["CustomerID"] if unassigned else None

    issues = conn.execute("""
        SELECT si.IssueID, si.EmployeeID, si.IssueDate, si.SaleID, e.EmployeeName,
               s.InvoiceNumber, s.CustomerID, s.TotalAmount
        FROM StockIssues si
        JOIN Employees e ON e.EmployeeID = si.EmployeeID
        JOIN Sales s ON s.SaleID = si.SaleID
        WHERE si.Status='Reconciled' AND si.SaleID IS NOT NULL
        ORDER BY si.IssueDate, si.IssueID
    """).fetchall()

    results = []
    for issue in issues:
        lines = conn.execute("SELECT sil.LineID, sil.ProductID FROM StockIssueLines sil WHERE sil.IssueID=?",
                             (issue["IssueID"],)).fetchall()
        total_overlap_qty = 0.0
        total_auto_qty = 0.0
        for line in lines:
            credited_qty = conn.execute(
                "SELECT COALESCE(SUM(QtyApplied),0) q FROM SaleStockIssueLinks WHERE StockIssueLineID=?",
                (line["LineID"],)).fetchone()["q"]
            auto_line = conn.execute("SELECT Qty FROM SalesLines WHERE SaleID=? AND ProductID=?",
                                     (issue["SaleID"], line["ProductID"])).fetchone()
            auto_qty = (auto_line["Qty"] if auto_line else 0) or 0
            if auto_qty <= 0:
                continue
            total_auto_qty += auto_qty
            total_overlap_qty += min(credited_qty, auto_qty) if credited_qty > 0 else 0

        if total_overlap_qty <= 0 or round(total_overlap_qty, 4) < round(total_auto_qty, 4):
            continue  # no duplication, or only partial - not handled by this script

        reasons_not_safe = []
        if unassigned_id is not None and issue["CustomerID"] != unassigned_id:
            reasons_not_safe.append("Sale is not billed to the Unassigned customer bucket (may be reassigned)")
        due_payments = conn.execute("SELECT COUNT(*) c FROM StockIssueDuePayments WHERE IssueID=?",
                                    (issue["IssueID"],)).fetchone()["c"]
        if due_payments:
            reasons_not_safe.append(f"{due_payments} due-payment row(s) recorded")
        claim_row = conn.execute("SELECT ClaimStatus FROM StockIssues WHERE IssueID=?",
                                 (issue["IssueID"],)).fetchone()
        if claim_row and claim_row["ClaimStatus"] not in (None, "Not Claimed"):
            reasons_not_safe.append(f"scheme claim status is '{claim_row['ClaimStatus']}'")
        custom_fields = conn.execute("""SELECT COUNT(*) c FROM CustomFieldValues v
                                      JOIN CustomFieldDefinitions d ON d.FieldID=v.FieldID
                                      WHERE d.ModuleName='Sale' AND v.RecordID=?""",
                                     (issue["SaleID"],)).fetchone()["c"]
        if custom_fields:
            reasons_not_safe.append(f"{custom_fields} custom field value(s) recorded")

        results.append({
            "issue": issue,
            "safe": not reasons_not_safe,
            "reasons_not_safe": reasons_not_safe,
        })
    return results


def main():
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    candidates = find_full_duplicates(conn)
    if not candidates:
        print("No fully-duplicated reconciliation invoices found. Nothing to do.")
        conn.close()
        return

    to_fix = [c for c in candidates if c["safe"]]
    to_skip = [c for c in candidates if not c["safe"]]

    for c in to_skip:
        i = c["issue"]
        print(f"SKIPPING Issue #{i['IssueID']} ({i['IssueDate']}, {i['EmployeeName']}) -> {i['InvoiceNumber']}: "
              f"NOT SAFE - {', '.join(c['reasons_not_safe'])}. Needs manual review, not touched.")

    if not to_fix:
        print("\nNothing safe to auto-correct. Review the skipped issue(s) above manually.")
        conn.close()
        return

    print(f"\n{'APPLYING' if apply else 'DRY RUN - would apply'} the following correction(s):\n")
    for c in to_fix:
        i = c["issue"]
        line_count = conn.execute("SELECT COUNT(*) c FROM SalesLines WHERE SaleID=?", (i["SaleID"],)).fetchone()["c"]
        print(f"  Issue #{i['IssueID']} ({i['IssueDate']}, {i['EmployeeName']}): delete Sale {i['InvoiceNumber']} "
              f"(SaleID={i['SaleID']}, Rs {i['TotalAmount']}, {line_count} line item(s)) and unlink it from the "
              f"Stock Issue - every unit on it is already billed on a direct Sales-tab invoice.")

    if not apply:
        print("\nThis was a DRY RUN - nothing was changed. Re-run with --apply to actually perform the fix.")
        conn.close()
        return

    for c in to_fix:
        i = c["issue"]
        conn.execute("UPDATE StockIssues SET SaleID=NULL WHERE IssueID=?", (i["IssueID"],))
        conn.execute("DELETE FROM SalesLines WHERE SaleID=?", (i["SaleID"],))
        conn.execute("DELETE FROM Sales WHERE SaleID=?", (i["SaleID"],))
    conn.commit()
    print(f"\nApplied: removed {len(to_fix)} duplicate invoice(s). Nothing else was touched - the underlying "
          f"direct Sales-tab invoices (the real, non-duplicate ones) are untouched, and the Stock Issue's own "
          f"reconciled figures (Qty Sold, Expected, Cash/Bank Collected) are unaffected by this.")
    conn.close()


if __name__ == "__main__":
    main()
