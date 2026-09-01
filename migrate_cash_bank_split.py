"""One-time migration (phase 26): split Stock Issue reconciliation's single
"Total Cash Collected" figure into Cash + Bank.

Adds two columns to StockIssues:
  - CashAmount (Rs) - the physical cash portion of what was collected.
  - BankAmount (Rs) - the bank/UPI/transfer portion of what was collected.

CashCollected itself is unchanged in meaning and stays the source of truth
for every downstream calculation (Expected/Discrepancy/AmountDue/PaymentStatus,
Targets actuals, Dashboard/Reports totals, etc.) - it is simply now always
computed as CashAmount + BankAmount at the point of entry (the Reconcile
form) rather than typed in directly as one number.

For any StockIssue reconciled before this migration, CashAmount is backfilled
from the existing CashCollected value and BankAmount set to 0, so the
Cash/Bank split shown on re-edit adds up to the same total as before - the
distributor can go back and adjust the split for old records if they want,
but nothing changes automatically.

Safe to run multiple times.

Usage: python migrate_cash_bank_split.py
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
    if add_column_if_missing(conn, "StockIssues", "CashAmount", "REAL NOT NULL DEFAULT 0"):
        added += 1
    if add_column_if_missing(conn, "StockIssues", "BankAmount", "REAL NOT NULL DEFAULT 0"):
        added += 1
    conn.commit()

    if added:
        backfilled = conn.execute("""UPDATE StockIssues SET CashAmount = COALESCE(CashCollected, 0)
                                   WHERE CashAmount = 0 AND BankAmount = 0
                                   AND CashCollected IS NOT NULL AND CashCollected != 0""").rowcount
        conn.commit()
        print(f"  Backfilled CashAmount from CashCollected on {backfilled} already-reconciled issue(s) "
              f"(BankAmount left at 0 - split freely on re-edit if needed).")

    print("Migration complete.")
    if added:
        print(f"\n{added} new column(s) added.")
    else:
        print("\nNo new columns needed - already up to date.")
    print("\nWhat's new:")
    print("- The Reconcile form's 'Total Cash Collected' field is now two fields, Cash and Bank.")
    print("  Total Collected (used for every downstream figure, exactly as before) = Cash + Bank.")


if __name__ == "__main__":
    run()
