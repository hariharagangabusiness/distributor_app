"""One-time migration for Stock Issue reconciliation enhancements: units
sold at a discounted price, units given away free under a company scheme,
and tracking of amounts still due from the salesperson plus scheme-value
claims still pending back from the company.

This does NOT delete or change any of your existing data. It only:
  - Adds DiscountAmount, QtyFree, and LineComments columns to
    StockIssueLines (all default to 0/blank, so existing reconciled lines
    are unaffected and simply show no discount/free units).
  - Adds AmountDue, PaymentStatus, SchemeAmount, ClaimStatus, ClaimedAt,
    ClaimedAmount, ReceivedAt, ReceivedAmount, ClaimNotes columns to
    StockIssues (existing reconciled issues default to PaymentStatus=
    'Paid'/AmountDue=0 and ClaimStatus='Not Claimed' - review any older
    issues that had a cash shortfall and use "Record Due Payment" there
    if that shortfall is actually still owed).
  - Creates a new table, StockIssueDuePayments, that logs each partial
    cash collection against a Stock Issue's outstanding due balance, so a
    due amount can be settled over several days with a full audit trail.

Nothing existing is dropped, renamed, or overwritten. Safe to run
multiple times.

Usage: python migrate_stock_issue_schemes.py
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
    db.init_db()  # ensures StockIssueDuePayments exists (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()
    added = 0

    line_columns = [
        ("QtyFree", "REAL NOT NULL DEFAULT 0"),
        ("DiscountAmount", "REAL NOT NULL DEFAULT 0"),
        ("LineComments", "TEXT"),
    ]
    for col, ddl in line_columns:
        if add_column_if_missing(conn, "StockIssueLines", col, ddl):
            added += 1

    issue_columns = [
        ("AmountDue", "REAL NOT NULL DEFAULT 0"),
        ("PaymentStatus", "TEXT NOT NULL DEFAULT 'Paid'"),
        ("SchemeAmount", "REAL NOT NULL DEFAULT 0"),
        ("ClaimStatus", "TEXT NOT NULL DEFAULT 'Not Claimed'"),
        ("ClaimedAt", "TEXT"),
        ("ClaimedAmount", "REAL"),
        ("ReceivedAt", "TEXT"),
        ("ReceivedAmount", "REAL"),
        ("ClaimNotes", "TEXT"),
    ]
    for col, ddl in issue_columns:
        if add_column_if_missing(conn, "StockIssues", col, ddl):
            added += 1

    conn.commit()

    # Existing Reconciled issues: derive AmountDue/PaymentStatus from the
    # Discrepancy they already have, so old shortfalls surface as "Unpaid"/
    # "Partial" rather than silently defaulting to Paid.
    existing = conn.execute(
        "SELECT IssueID, ExpectedAmount, CashCollected FROM StockIssues WHERE Status='Reconciled'"
    ).fetchall()
    backfilled = 0
    for row in existing:
        expected = row["ExpectedAmount"] or 0
        collected = row["CashCollected"] or 0
        due = round(max(expected - collected, 0), 2)
        if due <= 0:
            status = "Paid"
        elif collected <= 0:
            status = "Unpaid"
        else:
            status = "Partial"
        conn.execute("UPDATE StockIssues SET AmountDue=?, PaymentStatus=? WHERE IssueID=?",
                     (due, status, row["IssueID"]))
        backfilled += 1
    conn.commit()

    print("Migration complete.")
    if added:
        print(f"\n{added} new column(s) added.")
    else:
        print("\nNo new columns needed - already up to date.")
    if backfilled:
        print(f"Backfilled AmountDue/PaymentStatus for {backfilled} already-reconciled stock issue(s) "
              f"from their existing Expected/Collected figures.")
    print("\nWhat's new:")
    print("- Reconciling a Stock Issue now supports units sold at a discount (enter the discount")
    print("  amount per line), units given away free under a scheme (Qty Free per line), and a")
    print("  comments field per line to note why.")
    print("- A Stock Issue's cash shortfall is now tracked as an ongoing 'Amount Due' with a")
    print("  Paid/Partial/Unpaid status - use 'Record Due Payment' on the issue's page to log")
    print("  later collections against it.")
    print("- Total discount + free-scheme value per issue is tracked as a 'Scheme Amount' you can")
    print("  mark Claimed / Received as you claim it back from the company.")
    print("- A new Stock Issues report (Reports > Stock Issues Schemes & Dues) totals discounts,")
    print("  free units, pending dues, and pending company claims across any date range.")


if __name__ == "__main__":
    run()
