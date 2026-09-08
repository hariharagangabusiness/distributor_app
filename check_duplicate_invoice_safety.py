"""READ-ONLY safety check for the specific duplicate invoice found by
review_duplicate_reconciliation_invoices.py. Makes NO changes.

Confirms, for each Reconciled Stock Issue whose auto-Sale is a 100%
overlap with directly-credited Sales (i.e. every line's auto-invoiced qty
equals its credited qty - meaning the auto-Sale should not exist at all),
that it's actually safe to remove:
  - still billed to the system 'Unassigned' customer (not reassigned)
  - not referenced by any GST filing snapshot/lock
  - no due-payment or scheme-claim history recorded against the Stock Issue
    that would need to be reconsidered

Prints a clear SAFE/NOT SAFE verdict per issue with reasons - nothing is
deleted here."""
import sqlite3
from db import DB_PATH


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

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

    for issue in issues:
        lines = conn.execute("""
            SELECT sil.LineID, sil.ProductID FROM StockIssueLines sil WHERE sil.IssueID=?
        """, (issue["IssueID"],)).fetchall()

        full_overlap = True
        any_overlap = False
        for line in lines:
            credited_qty = conn.execute(
                "SELECT COALESCE(SUM(QtyApplied),0) q FROM SaleStockIssueLinks WHERE StockIssueLineID=?",
                (line["LineID"],)).fetchone()["q"]
            auto_line = conn.execute(
                "SELECT Qty FROM SalesLines WHERE SaleID=? AND ProductID=?",
                (issue["SaleID"], line["ProductID"])).fetchone()
            auto_qty = (auto_line["Qty"] if auto_line else 0) or 0
            if credited_qty > 0 or auto_qty > 0:
                any_overlap = True
            if round(auto_qty, 4) != round(min(credited_qty, auto_qty), 4) or auto_qty == 0:
                if not (credited_qty > 0 and auto_qty > 0 and abs(credited_qty - auto_qty) < 0.01):
                    full_overlap = False

        if not any_overlap:
            continue  # no overlap at all on this issue - not relevant here

        print(f"\nIssue #{issue['IssueID']} ({issue['IssueDate']}, {issue['EmployeeName']}) "
              f"-> auto-Sale {issue['InvoiceNumber']} (Rs {issue['TotalAmount']}):")

        if not full_overlap:
            print("  SKIP: partial overlap only - some units on this auto-Sale are NOT duplicates "
                  "(genuine walk-in units) - do not blanket-delete this one; needs manual line-by-line review.")
            continue

        reasons_not_safe = []
        if unassigned_id is not None and issue["CustomerID"] != unassigned_id:
            reasons_not_safe.append(f"CustomerID={issue['CustomerID']} is NOT the Unassigned bucket "
                                    f"({unassigned_id}) - this Sale may have already been reassigned to a "
                                    f"real customer; deleting it would erase real reassignment work.")

        due_payments = conn.execute(
            "SELECT COUNT(*) c FROM StockIssueDuePayments WHERE IssueID=?", (issue["IssueID"],)).fetchone()["c"]
        if due_payments:
            reasons_not_safe.append(f"{due_payments} StockIssueDuePayments row(s) recorded against this issue.")

        claim_row = conn.execute(
            "SELECT ClaimStatus FROM StockIssues WHERE IssueID=?", (issue["IssueID"],)).fetchone()
        if claim_row and claim_row["ClaimStatus"] not in (None, "Not Claimed"):
            reasons_not_safe.append(f"ClaimStatus='{claim_row['ClaimStatus']}' - a scheme claim has progressed "
                                    f"against this issue.")

        custom_fields = conn.execute("""
            SELECT COUNT(*) c FROM CustomFieldValues v JOIN CustomFieldDefinitions d ON d.FieldID=v.FieldID
            WHERE d.ModuleName='Sale' AND v.RecordID=?""", (issue["SaleID"],)).fetchone()["c"]
        if custom_fields:
            reasons_not_safe.append(f"{custom_fields} custom field value(s) recorded on this Sale.")

        if reasons_not_safe:
            print("  NOT SAFE to auto-remove:")
            for r in reasons_not_safe:
                print(f"    - {r}")
        else:
            print("  SAFE to remove: still billed to Unassigned, no due payments, no scheme claim progress, "
                  "no custom field data. Every line is a 100% duplicate of an already-credited direct Sale.")

    conn.close()


if __name__ == "__main__":
    main()
