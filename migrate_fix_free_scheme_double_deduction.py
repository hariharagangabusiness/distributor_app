"""Fixes a genuine bug: every time a Stock Issue was reconciled with some quantity
marked "Given Free" under a scheme, the app posted a "Free Scheme" InventoryTransactions
row deducting that quantity from stock - ON TOP OF the original "Issue" transaction that
already deducted the FULL quantity issued (sold, returned, and free-given portions alike)
at issue time. Units given away free never come back, exactly like units sold - so, just
like sold units, they need no second deduction. This bug silently deducted every
free-given unit twice, making the app's inventory ledger understate real physical stock
by the running total of every Free Scheme quantity ever recorded.

This script does NOT delete the erroneous historical rows (that would destroy the audit
trail) - it adds one compensating "Adjustment-In" row per erroneous "Free Scheme" row,
for the same quantity, clearly labelled and cross-referenced back to it. That restores
every affected product's ledger stock to the correct figure while keeping full history.

Safe to re-run: each correction is tagged RefType='FreeSchemeFix' with RefID pointing at
the original Free Scheme TransactionID, so a second run skips anything already corrected.
"""
import sqlite3
from db import DB_PATH


def already_corrected(conn, original_txn_id):
    return conn.execute(
        "SELECT 1 FROM InventoryTransactions WHERE RefType='FreeSchemeFix' AND RefID=?",
        (original_txn_id,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        bad_rows = conn.execute(
            "SELECT * FROM InventoryTransactions WHERE TransactionType='Free Scheme'"
        ).fetchall()
        if not bad_rows:
            print("No 'Free Scheme' transactions found - nothing to correct.")
            return

        corrected = 0
        skipped = 0
        total_qty_restored = 0.0
        by_product = {}
        for row in bad_rows:
            if already_corrected(conn, row["TransactionID"]):
                skipped += 1
                continue
            qty_to_restore = abs(row["QtyChange"] or 0)
            conn.execute("""
                INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                    QtyChange, RefType, RefID, Notes)
                VALUES (?,?,?,?,?,?,?)
            """, (
                row["ProductID"], row["TransactionDate"], "Adjustment-In", qty_to_restore,
                "FreeSchemeFix", row["TransactionID"],
                f"Correction: reversing double-deducted Free Scheme units from stock issue "
                f"(original TransactionID {row['TransactionID']}) - see "
                f"migrate_fix_free_scheme_double_deduction.py"
            ))
            corrected += 1
            total_qty_restored += qty_to_restore
            by_product[row["ProductID"]] = by_product.get(row["ProductID"], 0) + qty_to_restore

        conn.commit()
        print(f"Corrected {corrected} 'Free Scheme' transaction(s), skipped {skipped} already-corrected.")
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
