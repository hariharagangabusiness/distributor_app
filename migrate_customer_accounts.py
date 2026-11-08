"""One-time migration: adds Customers.AccountNumber, Customers.ParentCustomerID,
and CompanySettings.NextCustomerAccountSeq.

AccountNumber is a unique "HHGnnnn" identifier allocated once, automatically,
when a customer is created (see next_customer_account_number() in app.py) -
never reassigned or reused. Existing customers are backfilled here, oldest
first (CustomerID order), skipping the single system "Unassigned" bucket
customer (IsUnassignedBucket=1), which isn't a real account.

ParentCustomerID optionally groups a customer under a parent "head account"
(e.g. several shop locations under one head office) - left NULL for every
existing customer; nothing to backfill, since this is a brand-new concept
with no prior data to infer it from.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times.

Usage: python migrate_customer_accounts.py
"""
import sqlite3
from db import DB_PATH


def column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        did_work = False

        if not column_exists(conn, "Customers", "AccountNumber"):
            conn.execute("ALTER TABLE Customers ADD COLUMN AccountNumber TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_account_number ON Customers(AccountNumber)")
            conn.commit()
            print("Added Customers.AccountNumber.")
            did_work = True
        else:
            print("Customers.AccountNumber already exists - skipping that part.")

        if not column_exists(conn, "Customers", "ParentCustomerID"):
            conn.execute("ALTER TABLE Customers ADD COLUMN ParentCustomerID INTEGER REFERENCES Customers(CustomerID)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_parent ON Customers(ParentCustomerID)")
            conn.commit()
            print("Added Customers.ParentCustomerID.")
            did_work = True
        else:
            print("Customers.ParentCustomerID already exists - skipping that part.")

        if not column_exists(conn, "CompanySettings", "NextCustomerAccountSeq"):
            conn.execute(
                "ALTER TABLE CompanySettings ADD COLUMN NextCustomerAccountSeq INTEGER NOT NULL DEFAULT 1")
            conn.commit()
            print("Added CompanySettings.NextCustomerAccountSeq.")
            did_work = True
        else:
            print("CompanySettings.NextCustomerAccountSeq already exists - skipping that part.")

        unnumbered = conn.execute(
            "SELECT CustomerID FROM Customers WHERE IsUnassignedBucket=0 AND (AccountNumber IS NULL OR AccountNumber='') "
            "ORDER BY CustomerID").fetchall()
        if unnumbered:
            start_seq = conn.execute(
                "SELECT NextCustomerAccountSeq FROM CompanySettings WHERE SettingsID=1").fetchone()[0]
            seq = start_seq
            for (customer_id,) in unnumbered:
                account_number = f"HHG{seq:04d}"
                conn.execute("UPDATE Customers SET AccountNumber=? WHERE CustomerID=?", (account_number, customer_id))
                seq += 1
            conn.execute("UPDATE CompanySettings SET NextCustomerAccountSeq=? WHERE SettingsID=1", (seq,))
            conn.commit()
            print(f"Backfilled {len(unnumbered)} existing customer(s) with account numbers "
                  f"HHG{start_seq:04d} through HHG{seq - 1:04d}.")
            did_work = True
        else:
            print("No customers need an account number backfilled.")

        if did_work:
            print("\nWhat's new:")
            print("- Every customer now has a unique Account Number (HHG0001, HHG0002, ...), shown on the")
            print("  Customers list, Customer form, Accounts Receivable report, the customer ledger/statement,")
            print("  and printed invoices. New customers get the next number automatically.")
            print("- A customer can optionally be assigned a Parent Account on the Customer form, to group")
            print("  several accounts (e.g. multiple shop locations) under one head account. A parent's own")
            print("  sub-accounts are listed on its Purchase History page, and shown as a 'Parent Account'")
            print("  column on the Customers list and Accounts Receivable's Customer Summary tab.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
