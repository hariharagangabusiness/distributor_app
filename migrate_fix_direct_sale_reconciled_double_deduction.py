"""Fixes a second genuine bug (a sibling of migrate_fix_free_scheme_double_deduction.py):
a Sale entered or edited for a salesperson/date/product AFTER that salesperson's Stock Issue
for that day had already been Reconciled was always treated as a fresh warehouse deduction
("Direct Sale") - even when the reconciled issue still had unaccounted-for capacity left
(QtyIssued minus QtySold/QtyReturned/QtyFree), meaning those units genuinely came from that
same day's already-issued van stock, not a new warehouse pickup. This double-deducted them:
once implicitly via the original Issue transaction (now finalized at reconcile), and again
explicitly via the Sale's own "Sale" ledger row. create_sale() no longer does this going
forward (it now fills a reconciled issue's remaining capacity first, in the same
chronological order sales were saved, before falling back to a genuine deduction) - this
script finds every historical "Sale" transaction affected by the OLD behavior and corrects it.

Like its Free Scheme sibling, this does NOT delete or alter the original rows (full audit
trail preserved) - it adds one compensating "Adjustment-In" row per over-deducted unit,
tagged RefType='DirectSaleFix' pointing back at the original TransactionID it corrects.
Safe to re-run: a TransactionID that already has a matching correction is skipped.
"""
import sqlite3
from db import DB_PATH


def already_corrected(conn, original_txn_id):
    return conn.execute(
        "SELECT 1 FROM InventoryTransactions WHERE RefType='DirectSaleFix' AND RefID=?",
        (original_txn_id,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Every historical "Sale" ledger deduction, with the Sale's own EmployeeID/SaleDate -
        # only ones with an EmployeeID even have a chance of matching a Reconciled Stock Issue.
        sale_txns = conn.execute("""
            SELECT it.TransactionID, it.ProductID, it.QtyChange, s.EmployeeID, s.SaleDate
            FROM InventoryTransactions it
            JOIN Sales s ON s.SaleID = it.RefID AND it.RefType = 'Sale'
            WHERE it.TransactionType = 'Sale' AND s.EmployeeID IS NOT NULL
            ORDER BY it.ProductID, s.EmployeeID, s.SaleDate, it.TransactionID
        """).fetchall()

        if not sale_txns:
            print("No employee-linked Direct Sale transactions found - nothing to check.")
            return

        # Group by (EmployeeID, SaleDate, ProductID) so each Reconciled issue's capacity is
        # divided up across its own transactions in the same order they were originally saved.
        groups = {}
        for t in sale_txns:
            key = (t["EmployeeID"], t["SaleDate"], t["ProductID"])
            groups.setdefault(key, []).append(t)

        corrected = 0
        skipped = 0
        already_fixed = 0
        total_qty_restored = 0.0
        by_product = {}

        for (employee_id, sale_date, product_id), txns in groups.items():
            issue = conn.execute("""
                SELECT si.IssueID FROM StockIssues si
                WHERE si.EmployeeID=? AND si.IssueDate=? AND si.Status='Reconciled'
                ORDER BY si.IssueID DESC LIMIT 1
            """, (employee_id, sale_date)).fetchone()
            if not issue:
                skipped += len(txns)
                continue
            line = conn.execute("""
                SELECT QtyIssued, QtySold, QtyReturned, QtyFree FROM StockIssueLines
                WHERE IssueID=? AND ProductID=?
            """, (issue["IssueID"], product_id)).fetchone()
            if not line:
                skipped += len(txns)
                continue
            capacity = max((line["QtyIssued"] or 0) - (line["QtySold"] or 0)
                            - (line["QtyReturned"] or 0) - (line["QtyFree"] or 0), 0)
            if capacity <= 0:
                skipped += len(txns)
                continue

            for t in txns:
                if already_corrected(conn, t["TransactionID"]):
                    already_fixed += 1
                    continue
                if capacity <= 0:
                    break
                qty = abs(t["QtyChange"] or 0)
                covered = min(qty, capacity)
                if covered <= 0:
                    continue
                conn.execute("""
                    INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                        QtyChange, RefType, RefID, Notes)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    product_id, sale_date, "Adjustment-In", covered,
                    "DirectSaleFix", t["TransactionID"],
                    f"Correction: this Direct Sale deduction (original TransactionID {t['TransactionID']}) "
                    f"should have been covered by Stock Issue #{issue['IssueID']}'s own remaining "
                    f"unaccounted-for capacity instead of a fresh warehouse deduction - see "
                    f"migrate_fix_direct_sale_reconciled_double_deduction.py"
                ))
                capacity = round(capacity - covered, 4)
                corrected += 1
                total_qty_restored += covered
                by_product[product_id] = by_product.get(product_id, 0) + covered

        conn.commit()
        print(f"Corrected {corrected} transaction(s), skipped {skipped} (no matching Reconciled "
              f"issue or no capacity), {already_fixed} already corrected previously.")
        print(f"Total quantity restored to the ledger: {total_qty_restored:g} units across "
              f"{len(by_product)} product(s).")
        if by_product:
            print("\nPer-product quantity restored:")
            for pid, qty in sorted(by_product.items(), key=lambda x: -x[1]):
                name_row = conn.execute("SELECT ProductName FROM Products WHERE ProductID=?", (pid,)).fetchone()
                name = name_row["ProductName"] if name_row else f"Product #{pid}"
                print(f"  {name}: +{qty:g}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
