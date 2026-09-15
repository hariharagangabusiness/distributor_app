"""Adds the DirectSaleReviews table (an Admin review queue for every "Sale"-type
InventoryTransactions row - stock deducted straight from the warehouse, beyond what was
issued to a salesperson) and backfills a 'Pending' row for every such transaction that
already exists, so the review queue starts with full history rather than only new ones
going forward. Safe to re-run - only inserts a backfill row for a TransactionID that
doesn't already have one.

This does NOT change any stock figures - it's purely a review/audit queue layered on top
of transactions that have already happened, so an Admin can confirm each one isn't a
duplicate deduction (see the two `migrate_fix_*_double_deduction.py` scripts for the two
known bug patterns this queue exists to help catch anything similar to, going forward)."""
import sqlite3
from db import DB_PATH


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if not table_exists(conn, "DirectSaleReviews"):
            conn.execute("""
                CREATE TABLE DirectSaleReviews (
                    ReviewID        INTEGER PRIMARY KEY AUTOINCREMENT,
                    TransactionID   INTEGER NOT NULL UNIQUE,
                    ProductID       INTEGER NOT NULL,
                    Status          TEXT NOT NULL DEFAULT 'Pending',
                    ReviewedByUserID INTEGER,
                    ReviewedByUsername TEXT,
                    ReviewedAt      TEXT,
                    Notes           TEXT,
                    CreatedAt       TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (TransactionID) REFERENCES InventoryTransactions(TransactionID),
                    FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_directsalereviews_status ON DirectSaleReviews(Status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_directsalereviews_product ON DirectSaleReviews(ProductID)")
            conn.commit()
            print("Created DirectSaleReviews table.")
        else:
            print("DirectSaleReviews table already exists.")

        existing_ids = {r["TransactionID"] for r in conn.execute(
            "SELECT TransactionID FROM DirectSaleReviews").fetchall()}
        sale_txns = conn.execute("""
            SELECT TransactionID, ProductID, TransactionDate FROM InventoryTransactions
            WHERE TransactionType='Sale'
            ORDER BY TransactionID
        """).fetchall()

        backfilled = 0
        for t in sale_txns:
            if t["TransactionID"] in existing_ids:
                continue
            conn.execute("""
                INSERT INTO DirectSaleReviews (TransactionID, ProductID, Status, CreatedAt)
                VALUES (?,?, 'Pending', ?)
            """, (t["TransactionID"], t["ProductID"], t["TransactionDate"] or None))
            backfilled += 1

        conn.commit()
        print(f"Backfilled {backfilled} historical Direct Sale transaction(s) into the review queue "
              f"(skipped {len(sale_txns) - backfilled} already present).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
