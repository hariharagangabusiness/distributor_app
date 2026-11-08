"""Regression tests for Customer account numbers (unique "HHGnnnn", assigned
once at creation, never reused) and the flat 2-level parent/child account
grouping (ParentCustomerID): every creation path assigns a number, the
hierarchy stays exactly 2 levels deep, and the Customers list / Accounts
Receivable report / Customer form surface both.
"""
import os
import sqlite3
import tempfile
import unittest

from tests.base import DBTestCase, appmod, db


class SchemaUpgradeSafetyTests(unittest.TestCase):
    """db.init_db() re-runs the ENTIRE schema.sql (via executescript) on
    every call, including against an already-existing production database
    where `CREATE TABLE IF NOT EXISTS Customers` is a no-op - so it must
    never crash there just because a migration-introduced column
    (AccountNumber/ParentCustomerID) doesn't exist on that table yet. This
    is exactly the bug that broke the first production deploy of this
    feature: a `CREATE INDEX ... ON Customers(AccountNumber)` living in
    schema.sql's unconditional Indexes section (instead of inside
    migrate_customer_accounts.py, gated behind the ALTER TABLE that
    actually adds the column) crashed init_db() with "no such column:
    AccountNumber" on every real deploy until the column existed."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="distributor_schema_upgrade_", suffix=".db")
        os.close(fd)
        os.remove(self.path)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        # The pre-this-feature shape of Customers - no AccountNumber, no ParentCustomerID.
        conn.execute("""CREATE TABLE Customers (
            CustomerID INTEGER PRIMARY KEY AUTOINCREMENT, CustomerName TEXT NOT NULL,
            ContactPerson TEXT, Phone TEXT, Email TEXT, Address TEXT, Zone TEXT, GSTIN TEXT,
            State TEXT, StateCode TEXT, CreditLimit REAL NOT NULL DEFAULT 0,
            CreditDays INTEGER NOT NULL DEFAULT 0, Active INTEGER NOT NULL DEFAULT 1,
            IsUnassignedBucket INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("INSERT INTO Customers (CustomerName) VALUES ('Pre-existing Customer')")
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_init_db_does_not_crash_against_a_pre_existing_customers_table(self):
        original_db_path = db.DB_PATH
        db.DB_PATH = self.path
        try:
            db.init_db()  # must not raise sqlite3.OperationalError: no such column: AccountNumber
        finally:
            db.DB_PATH = original_db_path
        conn = sqlite3.connect(self.path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(Customers)")]
        self.assertNotIn("AccountNumber", cols)  # not this script's job - migrate_customer_accounts.py's is
        conn.close()

    def test_migration_then_backfills_the_column_and_index_correctly(self):
        import migrate_customer_accounts
        original_db_path = db.DB_PATH
        db.DB_PATH = self.path
        try:
            db.init_db()
            migrate_customer_accounts.DB_PATH = self.path
            migrate_customer_accounts.main()
        finally:
            db.DB_PATH = original_db_path
        conn = sqlite3.connect(self.path)
        row = conn.execute("SELECT AccountNumber FROM Customers WHERE CustomerName='Pre-existing Customer'").fetchone()
        self.assertEqual(row[0], "HHG0001")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO Customers (CustomerName, AccountNumber) VALUES ('Dup', 'HHG0001')")
        conn.close()


class NextCustomerAccountNumberTests(DBTestCase):
    def test_numbers_are_sequential_and_hhg_prefixed(self):
        with appmod.app.app_context():
            first = appmod.next_customer_account_number()
            second = appmod.next_customer_account_number()
        self.assertEqual(first, "HHG0001")
        self.assertEqual(second, "HHG0002")

    def test_numbers_never_repeat_even_after_a_customer_is_deleted(self):
        with appmod.app.app_context():
            first = appmod.next_customer_account_number()
        db.execute("INSERT INTO Customers (CustomerName, AccountNumber) VALUES ('Temp', ?)", (first,))
        db.execute("DELETE FROM Customers WHERE AccountNumber=?", (first,))
        with appmod.app.app_context():
            next_one = appmod.next_customer_account_number()
        self.assertEqual(next_one, "HHG0002")


class CustomerCreationAssignsAccountNumberTests(DBTestCase):
    def test_customer_form_assigns_an_account_number_on_create(self):
        client = self.make_admin_client()
        resp = client.post("/customers/new", data={
            "customer_name": "New Co", "contact_person": "", "phone": "", "email": "", "address": "",
            "zone": "North", "gstin": "", "state_code": "36", "credit_limit": "0", "credit_days": "0",
            "active": "on",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT AccountNumber FROM Customers WHERE CustomerName='New Co'", one=True)
        self.assertIsNotNone(row["AccountNumber"])
        self.assertTrue(row["AccountNumber"].startswith("HHG"))

    def test_editing_a_customer_does_not_change_its_account_number(self):
        client = self.make_admin_client()
        client.post("/customers/new", data={
            "customer_name": "Edit Me", "contact_person": "", "phone": "", "email": "", "address": "",
            "zone": "North", "gstin": "", "state_code": "36", "credit_limit": "0", "credit_days": "0",
            "active": "on",
        }, follow_redirects=True)
        row = db.query("SELECT CustomerID, AccountNumber FROM Customers WHERE CustomerName='Edit Me'", one=True)
        original_number = row["AccountNumber"]

        client.post(f"/customers/{row['CustomerID']}/edit", data={
            "customer_name": "Edit Me Renamed", "contact_person": "", "phone": "", "email": "", "address": "",
            "zone": "North", "gstin": "", "state_code": "36", "credit_limit": "0", "credit_days": "0",
            "active": "on",
        }, follow_redirects=True)
        after = db.query("SELECT AccountNumber FROM Customers WHERE CustomerID=?", (row["CustomerID"],), one=True)
        self.assertEqual(after["AccountNumber"], original_number)

    def test_customer_auto_created_from_the_sale_form_gets_an_account_number(self):
        with appmod.app.app_context():
            cid = appmod.resolve_sale_customer({
                "customer_id": "", "customer_name": "Walk-in Co", "customer_address": "",
                "customer_phone": "", "customer_zone": "North",
            })
        row = db.query("SELECT AccountNumber FROM Customers WHERE CustomerID=?", (cid,), one=True)
        self.assertIsNotNone(row["AccountNumber"])

    def test_the_system_unassigned_bucket_customer_gets_no_account_number(self):
        with appmod.app.app_context():
            appmod.get_unassigned_customer_id()
        row = db.query("SELECT AccountNumber FROM Customers WHERE IsUnassignedBucket=1", one=True)
        self.assertIsNone(row["AccountNumber"])


class CustomerParentGroupingTests(DBTestCase):
    def test_assigning_a_root_account_as_parent_succeeds(self):
        parent = self.make_customer("Head Office")
        child = self.make_customer("Branch A")
        client = self.make_admin_client()
        resp = client.post(f"/customers/{child}/edit", data={
            "customer_name": "Branch A", "contact_person": "", "phone": "", "email": "", "address": "",
            "zone": "North", "gstin": "", "state_code": "36", "credit_limit": "0", "credit_days": "0",
            "active": "on", "parent_customer_id": str(parent),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT ParentCustomerID FROM Customers WHERE CustomerID=?", (child,), one=True)
        self.assertEqual(row["ParentCustomerID"], parent)

    def test_a_customer_cannot_be_its_own_parent(self):
        cust = self.make_customer("Self Ref Co")
        with appmod.app.app_context():
            parent_id, error = appmod.validate_customer_parent(cust, str(cust))
        self.assertIsNone(parent_id)
        self.assertIn("own parent", error)

    def test_a_child_cannot_itself_become_a_parent(self):
        """Keeps the hierarchy flat at 2 levels: a customer that already
        has a parent can't be picked as someone else's parent."""
        grandparent = self.make_customer("Grandparent Co")
        child = self.make_customer("Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (grandparent, child))
        grandchild = self.make_customer("Grandchild Co")

        with appmod.app.app_context():
            parent_id, error = appmod.validate_customer_parent(grandchild, str(child))
        self.assertIsNone(parent_id)
        self.assertIn("itself a sub-account", error)

    def test_a_parent_with_children_cannot_be_given_a_parent_of_its_own(self):
        parent = self.make_customer("Existing Parent Co")
        child = self.make_customer("Existing Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (parent, child))
        other_root = self.make_customer("Other Root Co")

        with appmod.app.app_context():
            parent_id, error = appmod.validate_customer_parent(parent, str(other_root))
        self.assertIsNone(parent_id)
        self.assertIn("already has its own sub-accounts", error)

    def test_blank_parent_is_valid_and_means_no_parent(self):
        cust = self.make_customer("Standalone Co")
        with appmod.app.app_context():
            parent_id, error = appmod.validate_customer_parent(cust, "")
        self.assertIsNone(parent_id)
        self.assertIsNone(error)

    def test_parent_candidates_excludes_children_and_the_unassigned_bucket(self):
        root = self.make_customer("Root Co")
        child = self.make_customer("Child Co 2")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (root, child))
        with appmod.app.app_context():
            appmod.get_unassigned_customer_id()
            candidates = appmod.customer_parent_candidates()
        names = {c["CustomerName"] for c in candidates}
        self.assertIn("Root Co", names)
        self.assertNotIn("Child Co 2", names)
        self.assertNotIn("Unassigned (Van/Route Sales)", names)

    def test_customer_form_disables_parent_field_when_customer_already_has_children(self):
        parent = self.make_customer("Locked Parent Co")
        child = self.make_customer("Locked Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (parent, child))
        client = self.make_admin_client()
        resp = client.get(f"/customers/{parent}/edit")
        self.assertIn(b"disabled", resp.data)
        self.assertIn(b"already has its own sub-accounts", resp.data)


class CustomersListDisplayTests(DBTestCase):
    def test_list_shows_account_number_and_parent_account_columns(self):
        parent = self.make_customer("List Parent Co")
        db.execute("UPDATE Customers SET AccountNumber='HHG0099' WHERE CustomerID=?", (parent,))
        child = self.make_customer("List Child Co")
        db.execute("UPDATE Customers SET AccountNumber='HHG0100', ParentCustomerID=? WHERE CustomerID=?",
                   (parent, child))

        client = self.make_admin_client()
        resp = client.get("/customers")
        html = resp.data.decode()
        self.assertIn("HHG0099", html)
        self.assertIn("HHG0100", html)
        self.assertIn("sub-account", html)
        self.assertIn("List Parent Co", html)

    def test_search_matches_account_number(self):
        cust = self.make_customer("Searchable Co")
        db.execute("UPDATE Customers SET AccountNumber='HHG4242' WHERE CustomerID=?", (cust,))
        client = self.make_admin_client()
        resp = client.get("/customers?q=HHG4242")
        self.assertIn(b"Searchable Co", resp.data)


class AccountsReceivableGroupingTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, invoice):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount)
                           VALUES (?, ?, '2026-09-01', 'Completed', ?, ?, ?)""",
                           (invoice, customer_id, taxable, received, taxable))

    def test_customer_summary_includes_account_number_and_parent_account(self):
        parent = self.make_customer("AR Parent Co")
        db.execute("UPDATE Customers SET AccountNumber='HHG0010' WHERE CustomerID=?", (parent,))
        child = self.make_customer("AR Child Co")
        db.execute("UPDATE Customers SET AccountNumber='HHG0011', ParentCustomerID=? WHERE CustomerID=?",
                   (parent, child))
        self.make_sale(child, 1000, 0, invoice="INV-AR-GROUP-1")

        with appmod.app.app_context():
            rows = appmod.get_ar_customer_summary()
        row = next(r for r in rows if r["CustomerName"] == "AR Child Co")
        self.assertEqual(row["AccountNumber"], "HHG0011")
        self.assertEqual(row["ParentCustomerName"], "AR Parent Co")
        self.assertEqual(row["ParentAccountNumber"], "HHG0010")

    def test_pdf_invoice_row_includes_account_number(self):
        cust = self.make_customer("PDF Acct Co")
        db.execute("UPDATE Customers SET AccountNumber='HHG0020' WHERE CustomerID=?", (cust,))
        self.make_sale(cust, 1000, 0, invoice="INV-PDF-ACCT")
        with appmod.app.app_context():
            rows = appmod.get_ar_invoice_rows()
            row = next(r for r in rows if r["InvoiceNumber"] == "INV-PDF-ACCT")
            pdf_row = appmod._ar_invoice_pdf_row(row)
        self.assertEqual(pdf_row["Account #"], "HHG0020")


class ConsolidatedGroupLedgerTests(DBTestCase):
    """When a customer has sub-accounts grouped under it (ParentCustomerID),
    the Ledger tab can pull one consolidated statement across the parent +
    every sub-account - a single chronological running balance, each entry
    tagged with which account it belongs to - instead of only ever showing
    one account's own ledger at a time."""

    def make_sale(self, customer_id, taxable, received, sale_date="2026-09-01", invoice="INV-GROUP-LEDGER"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount)
                           VALUES (?, ?, ?, 'Completed', ?, ?, ?)""",
                           (invoice, customer_id, sale_date, taxable, received, taxable))

    def test_group_ledger_combines_parent_and_children_into_one_running_balance(self):
        parent = self.make_customer("Head Office")
        child_a = self.make_customer("Branch A")
        child_b = self.make_customer("Branch B")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID IN (?, ?)",
                   (parent, child_a, child_b))
        self.make_sale(parent, 1000, 0, sale_date="2026-09-01", invoice="INV-HO-1")
        self.make_sale(child_a, 500, 0, sale_date="2026-09-05", invoice="INV-A-1")
        self.make_sale(child_b, 300, 0, sale_date="2026-09-10", invoice="INV-B-1")

        with appmod.app.app_context():
            group_ids = appmod._customer_group_ids(parent, db.query(
                "SELECT CustomerID FROM Customers WHERE ParentCustomerID=?", (parent,)))
            ledger = appmod._build_customer_ledger(
                appmod._ledger_sales_for_group(group_ids), "", "")

        self.assertEqual({e["account"] for e in ledger["entries"]}, {"Head Office", "Branch A", "Branch B"})
        balances = [e["balance"] for e in ledger["entries"]]
        self.assertEqual(balances, [1000, 1500, 1800])
        self.assertEqual(ledger["closing_balance"], 1800)

    def test_entry_detail_is_prefixed_with_the_account_name_in_group_mode(self):
        parent = self.make_customer("Prefix Parent Co")
        child = self.make_customer("Prefix Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (parent, child))
        self.make_sale(child, 1000, 0, invoice="INV-PREFIX-1")

        with appmod.app.app_context():
            group_ids = appmod._customer_group_ids(parent, db.query(
                "SELECT CustomerID FROM Customers WHERE ParentCustomerID=?", (parent,)))
            ledger = appmod._build_customer_ledger(
                appmod._ledger_sales_for_group(group_ids), "", "")
        self.assertEqual(ledger["entries"][0]["detail"], "Prefix Child Co — Invoice INV-PREFIX-1")

    def test_single_account_ledger_is_unaffected_no_account_prefix(self):
        """A plain single-customer ledger query never selects CustomerName -
        _build_customer_ledger() must stay a no-op prefix-wise for it."""
        cust = self.make_customer("Plain Co")
        self.make_sale(cust, 1000, 0, invoice="INV-PLAIN-1")
        sales_asc = db.query("""SELECT s.SaleID, s.InvoiceNumber, s.SaleDate, s.Status, s.PaymentStatus,
                              s.TaxableAmount, s.TotalAmount, s.AmountReceived,
                              (s.TaxableAmount - s.AmountReceived) AS Due
                              FROM Sales s WHERE s.CustomerID=? AND s.Status<>'Cancelled'""", (cust,))
        with appmod.app.app_context():
            ledger = appmod._build_customer_ledger(sales_asc, "", "")
        self.assertEqual(ledger["entries"][0]["detail"], "Invoice INV-PLAIN-1")
        self.assertIsNone(ledger["entries"][0]["account"])

    def test_route_with_group_param_shows_the_consolidated_ledger(self):
        parent = self.make_customer("Route Parent Co")
        child = self.make_customer("Route Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (parent, child))
        self.make_sale(parent, 1000, 0, invoice="INV-ROUTE-PARENT")
        self.make_sale(child, 500, 0, invoice="INV-ROUTE-CHILD")

        client = self.make_admin_client()
        resp = client.get(f"/customers/{parent}/history?view=ledger&group=1")
        html = resp.data.decode()
        self.assertIn("Consolidated Group Ledger", html)
        self.assertIn("INV-ROUTE-PARENT", html)
        self.assertIn("INV-ROUTE-CHILD", html)
        self.assertIn("Route Child Co", html)  # account-name prefix on the child's entry

    def test_route_without_group_param_shows_only_this_accounts_own_ledger(self):
        parent = self.make_customer("No Group Parent Co")
        child = self.make_customer("No Group Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (parent, child))
        self.make_sale(parent, 1000, 0, invoice="INV-NG-PARENT")
        self.make_sale(child, 500, 0, invoice="INV-NG-CHILD")

        client = self.make_admin_client()
        resp = client.get(f"/customers/{parent}/history?view=ledger")
        html = resp.data.decode()
        self.assertIn("INV-NG-PARENT", html)
        self.assertNotIn("INV-NG-CHILD", html)
        self.assertNotIn("Consolidated Group Ledger", html)

    def test_group_param_is_ignored_for_an_account_with_no_sub_accounts(self):
        """A childless account (or a sub-account itself) requesting
        ?group=1 just falls back to its own ledger - group_mode requires
        sub_accounts to be non-empty."""
        cust = self.make_customer("Childless Co")
        self.make_sale(cust, 1000, 0, invoice="INV-CHILDLESS")
        client = self.make_admin_client()
        resp = client.get(f"/customers/{cust}/history?view=ledger&group=1")
        html = resp.data.decode()
        self.assertIn("INV-CHILDLESS", html)
        self.assertNotIn("Consolidated Group Ledger", html)

    def test_toggle_buttons_only_appear_when_sub_accounts_exist(self):
        parent = self.make_customer("Toggle Parent Co")
        child = self.make_customer("Toggle Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (parent, child))
        standalone = self.make_customer("Toggle Standalone Co")

        client = self.make_admin_client()
        resp_parent = client.get(f"/customers/{parent}/history?view=ledger")
        self.assertIn(b"Consolidated Group", resp_parent.data)
        resp_standalone = client.get(f"/customers/{standalone}/history?view=ledger")
        self.assertNotIn(b"Consolidated Group", resp_standalone.data)

    def test_group_ledger_pdf_download(self):
        parent = self.make_customer("PDF Group Parent Co")
        child = self.make_customer("PDF Group Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (parent, child))
        self.make_sale(parent, 1000, 0, invoice="INV-PDF-GROUP-PARENT")
        self.make_sale(child, 500, 0, invoice="INV-PDF-GROUP-CHILD")

        client = self.make_admin_client()
        resp = client.get(f"/customers/{parent}/ledger/pdf?group=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/pdf")
        self.assertTrue(resp.data.startswith(b"%PDF"))
        self.assertIn(b"Group", resp.headers["Content-Disposition"].encode())

    def test_group_ledger_date_range_still_rolls_up_an_opening_balance(self):
        parent = self.make_customer("Date Range Parent Co")
        child = self.make_customer("Date Range Child Co")
        db.execute("UPDATE Customers SET ParentCustomerID=? WHERE CustomerID=?", (parent, child))
        self.make_sale(parent, 1000, 0, sale_date="2026-08-01", invoice="INV-DR-OLD")
        self.make_sale(child, 500, 0, sale_date="2026-09-15", invoice="INV-DR-NEW")

        with appmod.app.app_context():
            group_ids = appmod._customer_group_ids(parent, db.query(
                "SELECT CustomerID FROM Customers WHERE ParentCustomerID=?", (parent,)))
            ledger = appmod._build_customer_ledger(
                appmod._ledger_sales_for_group(group_ids), "2026-09-01", "")
        self.assertEqual(ledger["opening_balance"], 1000)
        self.assertEqual(len(ledger["entries"]), 1)
        self.assertEqual(ledger["entries"][0]["balance"], 1500)
