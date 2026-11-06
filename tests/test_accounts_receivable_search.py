"""Regression tests for the ?q= search box on every Accounts Receivable tab:
Customer Summary, Aging, Invoice-wise, Zone-wise, Salesperson-wise, and
Payments Recorded each filter their rows by the same free-text query,
matched against customer name/phone/zone/invoice number/salesperson (or,
on the Payments tab, customer/invoice/notes/method), case-insensitively.
"""
from tests.base import DBTestCase, appmod, db


class AccountsReceivableSearchTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, invoice, employee_id=None, sale_date="2026-09-01"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount, EmployeeID)
                           VALUES (?, ?, ?, 'Completed', ?, ?, ?, ?)""",
                           (invoice, customer_id, sale_date, taxable, received, taxable, employee_id))

    def setUp(self):
        super().setUp()
        self.alpha = self.make_customer("Alpha Traders")
        db.execute("UPDATE Customers SET Phone='9000011111', Zone='North' WHERE CustomerID=?", (self.alpha,))
        self.beta = self.make_customer("Beta Distributors")
        db.execute("UPDATE Customers SET Phone='9000022222', Zone='South' WHERE CustomerID=?", (self.beta,))
        self.emp = self.make_employee("Ravi Kumar")
        self.make_sale(self.alpha, 1000, 0, invoice="INV-ALPHA-1", employee_id=self.emp)
        self.make_sale(self.beta, 500, 0, invoice="INV-BETA-1")

    def test_summary_tab_filters_by_customer_name_case_insensitively(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary&q=ALPHA")
        self.assertIn(b"Alpha Traders", resp.data)
        self.assertNotIn(b"Beta Distributors", resp.data)

    def test_summary_tab_filters_by_phone(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary&q=9000022222")
        self.assertIn(b"Beta Distributors", resp.data)
        self.assertNotIn(b"Alpha Traders", resp.data)

    def test_summary_tab_filters_by_zone(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary&q=North")
        self.assertIn(b"Alpha Traders", resp.data)
        self.assertNotIn(b"Beta Distributors", resp.data)

    def test_aging_tab_filters_by_customer_name(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=aging&q=beta")
        self.assertIn(b"Beta Distributors", resp.data)
        self.assertNotIn(b"Alpha Traders", resp.data)

    def test_invoices_tab_filters_by_invoice_number(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices&q=INV-ALPHA-1")
        self.assertIn(b"INV-ALPHA-1", resp.data)
        self.assertNotIn(b"INV-BETA-1", resp.data)

    def test_zone_tab_filters_by_zone_name_and_drops_non_matching_zones(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=zone&q=South")
        html = resp.data.decode()
        self.assertIn("South", html)
        self.assertNotIn("North", html)
        self.assertIn("Beta Distributors", html)
        self.assertNotIn("Alpha Traders", html)

    def test_salesperson_tab_filters_by_salesperson_name(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=salesperson&q=Ravi")
        html = resp.data.decode()
        self.assertIn("Ravi Kumar", html)
        self.assertNotIn("Counter / Unassigned", html)

    def test_no_match_shows_a_search_specific_empty_state(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary&q=Nonexistent Customer Co")
        self.assertIn(b"No matches for your search.", resp.data)

    def test_search_narrows_the_total_due_stat_card(self):
        client = self.make_admin_client()
        resp_all = client.get("/reports/accounts-receivable?view=summary")
        resp_alpha = client.get("/reports/accounts-receivable?view=summary&q=Alpha")
        self.assertIn(appmod.indian_number_format(1000, 0).encode(), resp_alpha.data)
        self.assertIn(appmod.indian_number_format(1500, 0).encode(), resp_all.data)

    def test_search_box_value_and_tab_links_preserve_the_query(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary&q=Alpha")
        html = resp.data.decode()
        self.assertIn('value="Alpha"', html)
        self.assertIn("view=aging&amp;q=Alpha", html)

    def test_payments_tab_filters_by_customer_invoice_notes_or_method(self):
        db.execute("""INSERT INTO SalePayments (SaleID, PaymentDate, Amount, PaymentMethod, Notes)
                    SELECT SaleID, '2026-09-05', 200, 'UPI', 'part payment' FROM Sales WHERE InvoiceNumber='INV-ALPHA-1'""")
        db.execute("""INSERT INTO SalePayments (SaleID, PaymentDate, Amount, PaymentMethod, Notes)
                    SELECT SaleID, '2026-09-06', 100, 'Cash', '' FROM Sales WHERE InvoiceNumber='INV-BETA-1'""")
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=payments&q=UPI")
        html = resp.data.decode()
        self.assertIn("Alpha Traders", html)
        self.assertNotIn("Beta Distributors", html)
