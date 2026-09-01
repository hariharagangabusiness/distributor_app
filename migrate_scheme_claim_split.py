"""One-time migration for a phase-19 follow-up:

1. Stock Issue "Delete" no longer refuses to delete an issue that already
   has a due payment or scheme claim recorded against it - it now performs
   a full reversal instead (Admin-only): the due-payment history, claim
   status, linked Sale, and stock impact are all undone together. No
   schema change for this part.

2. "Scheme Amount" (what's claimable back from the company, tracked
   through Claimed/Received) used to be computed automatically as
   Discount Rs + (Qty Free x Unit Price) at reconciliation. That mixed in
   Discount Rs, which is a real margin-reducing discount - never
   claimable back from the company - not a company scheme. Scheme Amount
   is now built from a new, directly-entered "Scheme Claim Rs" field per
   line (still starts pre-filled from Qty Free x Unit Price as a
   starting point, but is a plain editable Rs figure, since a company's
   actual scheme reimbursement rate need not match the line's retail
   price). Discount Rs is unchanged otherwise - it already reduces
   Expected Amount and the linked Sale's revenue, exactly as before.
   Adds: StockIssueLines.SchemeClaimAmount.

Nothing existing is dropped, renamed, or overwritten. Safe to run
multiple times. Existing reconciled issues keep their old SchemeAmount
figure until re-reconciled (Edit Reconciliation) - this migration does
not retroactively recompute past issues.

Usage: python migrate_scheme_claim_split.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def add_column_if_missing(conn, table, column, ddl):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        print(f"  Added {table}.{column}")
        return True
    return False


def run():
    db.init_db()
    conn = db.get_conn()
    added = 0
    if add_column_if_missing(conn, "StockIssueLines", "SchemeClaimAmount", "REAL NOT NULL DEFAULT 0"):
        added += 1
    conn.commit()

    print("Migration complete.")
    if added:
        print(f"\n{added} new column added.")
    else:
        print("\nNo new columns needed - already up to date.")
    print("\nWhat's new:")
    print("- Deleting a Stock Issue (Admin only) is no longer blocked when a due payment or scheme")
    print("  claim has already been recorded - it now fully reverses everything (due payments, claim")
    print("  status, linked Sale, stock impact) in one step.")
    print("- Reconciling a Stock Issue now has a 'Scheme Claim Rs' field per line - the amount")
    print("  actually claimable from the company for that line's scheme. Discount Rs is still tracked")
    print("  separately and is no longer folded into the claimable Scheme Amount (it's a real margin")
    print("  reduction, not something the company reimburses).")


if __name__ == "__main__":
    run()
