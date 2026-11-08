"""Regression tests for Customer account numbers (unique "HHGnnnn", assigned
once at creation, never reused) and the flat 2-level parent/child account
grouping (ParentCustomerID): every creation path assigns a number, the
hierarchy stays exactly 2 levels deep, and the Customers list / Accounts
Receivable report / Customer form surface both.
"""
from tests.base import DBTestCase, appmod, db


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
