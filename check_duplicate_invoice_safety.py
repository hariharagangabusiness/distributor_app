"""READ-ONLY safety check for duplicate reconciliation invoices. Makes NO changes.

For each Reconciled Stock Issue with an auto-created Sale, computes the
same per-line overlap as review_duplicate_reconciliation_invoices.py
(overlap = min(credited_qty, auto_qty), only where credited_qty > 0 - i.e.
actual double-invoiced quantity, not just "this line has some quantity").

Classifies each issue's auto-Sale as:
  NO DUPLICATION  - no line has any overlap; not affected by the bug at all
  FULL DUPLICATE  - every line's auto-invoiced qty is entirely covered by
                    the overlap (nothing genuine on this invoice) - a
                    candidate for outright removal
  PARTIAL         - some lines/quantity are duplicated, others are genuine
                    (walk-in units never billed elsewhere) - needs a
                    line-level qty reduction, not a blanket delete

For FULL DUPLICATE and PARTIAL cases, also checks whether it's safe to
touch at all: still billed to the Unassigned customer (not reassigned),
no due-payment or scheme-claim history, no custom field data."""
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

    no_dup_count = full_dup_count = partial_count = 0

    for issue in issues:
        lines = conn.execute("""
            SELECT sil.LineID, sil.ProductID FROM StockIssueLines sil WHERE sil.IssueID=?
        """, (issue["IssueID"],)).fetchall()

        total_overlap_qty = 0.0
        total_auto_qty = 0.0
        line_details = []

        for line in lines:
            credited_qty = conn.execute(
                "SELECT COALESCE(SUM(QtyApplied),0) q FROM SaleStockIssueLinks WHERE StockIssueLineID=?",
                (line["LineID"],)).fetchone()["q"]
            auto_line = conn.execute(
                "SELECT Qty FROM SalesLines WHERE SaleID=? AND ProductID=?",
                (issue["SaleID"], line["ProductID"])).fetchone()
            auto_qty = (auto_line["Qty"] if auto_line else 0) or 0
            if auto_qty <= 0:
                continue
            total_auto_qty += auto_qty
            overlap = min(credited_qty, auto_qty) if credited_qty > 0 else 0
            total_overlap_qty += overlap
            line_details.append((line["ProductID"], credited_qty, auto_qty, overlap))

        if total_overlap_qty <= 0:
            no_dup_count += 1
            continue  # genuinely no duplication on this issue - not affected

        print(f"\nIssue #{issue['IssueID']} ({issue['IssueDate']}, {issue['EmployeeName']}) "
              f"-> auto-Sale {issue['InvoiceNumber']} (Rs {issue['TotalAmount']}):")

        is_full_duplicate = round(total_overlap_qty, 4) >= round(total_auto_qty, 4)

        for product_id, credited_qty, auto_qty, overlap in line_details:
            if overlap > 0:
                tag = "FULL duplicate line" if round(overlap, 4) == round(auto_qty, 4) else "PARTIAL duplicate line"
                print(f"    - ProductID {product_id}: credited={credited_qty}, auto-invoiced={auto_qty}, "
                      f"overlap={overlap} ({tag})")
            else:
                print(f"    - ProductID {product_id}: credited=0, auto-invoiced={auto_qty} (genuine, not a duplicate)")

        if is_full_duplicate:
            full_dup_count += 1
            print("  Verdict: FULL DUPLICATE - every unit on this auto-Sale is already billed elsewhere; "
                  "the whole invoice is a candidate for removal.")
        else:
            partial_count += 1
            print(f"  Verdict: PARTIAL - {total_overlap_qty} of {total_auto_qty} units are duplicated; the rest "
                  f"are genuine and must be KEPT. Needs a per-line quantity reduction, not a full delete.")

        reasons_not_safe = []
        if unassigned_id is not None and issue["CustomerID"] != unassigned_id:
            reasons_not_safe.append(f"CustomerID={issue['CustomerID']} is NOT the Unassigned bucket "
                                    f"({unassigned_id}) - this Sale may have already been reassigned to a "
                                    f"real customer.")
        due_payments = conn.execute(
            "SELECT COUNT(*) c FROM StockIssueDuePayments WHERE IssueID=?", (issue["IssueID"],)).fetchone()["c"]
        if due_payments:
            reasons_not_safe.append(f"{due_payments} StockIssueDuePayments row(s) recorded against this issue.")
        claim_row = conn.execute(
            "SELECT ClaimStatus FROM StockIssues WHERE IssueID=?", (issue["IssueID"],)).fetchone()
        if claim_row and claim_row["ClaimStatus"] not in (None, "Not Claimed"):
            reasons_not_safe.append(f"ClaimStatus='{claim_row['ClaimStatus']}' - a scheme claim has progressed.")
        custom_fields = conn.execute("""
            SELECT COUNT(*) c FROM CustomFieldValues v JOIN CustomFieldDefinitions d ON d.FieldID=v.FieldID
            WHERE d.ModuleName='Sale' AND v.RecordID=?""", (issue["SaleID"],)).fetchone()["c"]
        if custom_fields:
            reasons_not_safe.append(f"{custom_fields} custom field value(s) recorded on this Sale.")

        if reasons_not_safe:
            print("  NOT SAFE to auto-correct without manual review:")
            for r in reasons_not_safe:
                print(f"    - {r}")
        else:
            print("  Safe to correct programmatically (still Unassigned, no due payments/claims/custom data).")

    print(f"\n{'=' * 70}\nSummary: {no_dup_count} issue(s) with NO duplication, "
          f"{full_dup_count} FULL DUPLICATE, {partial_count} PARTIAL.")

    conn.close()


if __name__ == "__main__":
    main()
