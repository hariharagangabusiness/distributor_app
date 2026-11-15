"""One-time migration:
1. Adds CompanySettings.StockIssuePrefix / NextStockIssueSeq / StockIssueSeqFY
   and StockIssues.IssueNumber - unique, sequential Stock Issue numbers
   (e.g. "SI/2026-27/0001"), allocated at creation the same FY/seq pattern
   already used for Invoice Numbers (see next_stock_issue_number() in
   app.py). Existing Stock Issues are backfilled here, oldest first within
   each financial year (IssueDate order), so historical issues get a
   sensible-looking number too.
2. Adds DirectSaleReviews.StockIssueID - optionally set when an Admin
   confirms a direct-sale review, recording which Stock Issue (if any) that
   quantity should be reconciled against. NULL for every existing row
   (nothing to infer retroactively) and for any future row left unassigned.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_stock_issue_numbers.py
"""
import sqlite3
from datetime import date
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def financial_year_label(iso_date):
    year, month = int(iso_date[:4]), int(iso_date[5:7])
    start = year if month >= 4 else year - 1
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        did_work = False

        for column, ddl in (
            ("StockIssuePrefix", "ALTER TABLE CompanySettings ADD COLUMN StockIssuePrefix TEXT NOT NULL DEFAULT 'SI'"),
            ("NextStockIssueSeq", "ALTER TABLE CompanySettings ADD COLUMN NextStockIssueSeq INTEGER NOT NULL DEFAULT 1"),
            ("StockIssueSeqFY", "ALTER TABLE CompanySettings ADD COLUMN StockIssueSeqFY TEXT"),
        ):
            if not column_exists(conn, "CompanySettings", column):
                conn.execute(ddl)
                conn.commit()
                print(f"Added CompanySettings.{column}.")
                did_work = True
            else:
                print(f"CompanySettings.{column} already exists - skipping that part.")

        if not column_exists(conn, "StockIssues", "IssueNumber"):
            conn.execute("ALTER TABLE StockIssues ADD COLUMN IssueNumber TEXT")
            conn.commit()
            print("Added StockIssues.IssueNumber.")
            did_work = True
        else:
            print("StockIssues.IssueNumber already exists - skipping that part.")
        # Unconditional and idempotent (IF NOT EXISTS) regardless of the branch above - a
        # fresh install's schema.sql already declares IssueNumber UNIQUE inline (a no-op
        # there), but an upgraded database only gets the column via the ALTER just above,
        # and needs this index created explicitly to enforce uniqueness.
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_stockissues_number ON StockIssues(IssueNumber)")
        conn.commit()

        unnumbered = conn.execute(
            "SELECT IssueID, IssueDate FROM StockIssues WHERE IssueNumber IS NULL OR IssueNumber='' "
            "ORDER BY IssueDate, IssueID").fetchall()
        if unnumbered:
            prefix = conn.execute(
                "SELECT StockIssuePrefix FROM CompanySettings WHERE SettingsID=1").fetchone()[0] or "SI"
            fy_seq = {}
            first_numbers, last_numbers = {}, {}
            for issue_id, issue_date in unnumbered:
                fy = financial_year_label(issue_date)
                seq = fy_seq.get(fy, 1)
                issue_number = f"{prefix}/{fy}/{seq:04d}"
                conn.execute("UPDATE StockIssues SET IssueNumber=? WHERE IssueID=?", (issue_number, issue_id))
                fy_seq[fy] = seq + 1
                first_numbers.setdefault(fy, issue_number)
                last_numbers[fy] = issue_number
            conn.commit()
            # Continue the live counter from wherever the CURRENT financial year's backfill
            # left off (1 if this FY has no backfilled issues yet) - so the very next real
            # Stock Issue created after this migration gets the correct next number, not a
            # collision with one just assigned above.
            today_fy = financial_year_label(date.today().isoformat())
            conn.execute("UPDATE CompanySettings SET NextStockIssueSeq=?, StockIssueSeqFY=? WHERE SettingsID=1",
                         (fy_seq.get(today_fy, 1), today_fy))
            conn.commit()
            summary = ", ".join(f"{fy}: {first_numbers[fy]}-{last_numbers[fy]}" for fy in sorted(fy_seq))
            print(f"Backfilled {len(unnumbered)} existing Stock Issue(s) with numbers ({summary}).")
            did_work = True
        else:
            print("No Stock Issues need a number backfilled.")

        if not column_exists(conn, "DirectSaleReviews", "StockIssueID"):
            conn.execute("ALTER TABLE DirectSaleReviews ADD COLUMN StockIssueID INTEGER REFERENCES StockIssues(IssueID)")
            conn.commit()
            print("Added DirectSaleReviews.StockIssueID.")
            did_work = True
        else:
            print("DirectSaleReviews.StockIssueID already exists - skipping that part.")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_directsalereviews_stockissue ON DirectSaleReviews(StockIssueID)")
        conn.commit()

        if did_work:
            print("\nWhat's new:")
            print("- Every Stock Issue now has a unique number (SI/2026-27/0001, ...), shown on the Stock")
            print("  Issues list and the issue's own detail page.")
            print("- Confirming a Direct Sale Review can now optionally assign it to a specific Stock Issue,")
            print("  so it's reconciled against the right one when a salesperson has more than one open the")
            print("  same day - see Direct Sale Review and the Reconcile screen.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
