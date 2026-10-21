"""Regression tests for the Customer Ledger statement of account
(_build_customer_ledger() + customer_purchase_history.html?view=ledger):
Sales map to Debit, SalePayments/leftover receipts map to Credit, the
running balance is chronological and carries a Dr/Cr suffix, and the
6-column layout has no inline action buttons cluttering each row.
"""
from tests.base import DBTestCase, appmod, db


class CustomerLedgerHelperTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, sale_date="2026-09-01", invoice="INV-LEDGER-1"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount)
                           VALUES (?, ?, ?, 'Completed', ?, ?, ?)""",
                           (invoice, customer_id, sale_date, taxable, received, taxable))

    def get_sales_asc(self, customer_id):
        return db.query("""SELECT s.SaleID, s.InvoiceNumber, s.SaleDate, s.Status, s.PaymentStatus,
                         s.TaxableAmount, s.TotalAmount, s.AmountReceived,
                         (s.TaxableAmount - s.AmountReceived) AS Due
                         FROM Sales s WHERE s.CustomerID=? AND s.Status<>'Cancelled'
                         ORDER BY s.SaleDate, s.SaleID""", (customer_id,))

    def test_invoice_is_a_debit_entry(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, invoice="INV-DEBIT")

        with appmod.app.app_context():
            ledger = appmod._build_customer_ledger(self.get_sales_asc(cust), "", "")

        entry = ledger["entries"][0]
        self.assertEqual(entry["debit"], 1000)
        self.assertEqual(entry["credit"], 0)
        self.assertEqual(entry["ref"], "INV-DEBIT")

    def test_lump_sum_payment_on_the_sale_is_a_credit_entry(self):
        """AmountReceived entered directly on the Sale form (no SalePayments
        rows) still shows up as its own Credit line, not folded into the
        invoice row."""
        cust = self.make_customer()
        self.make_sale(cust, 1000, 400, invoice="INV-CREDIT")

        with appmod.app.app_context():
            ledger = appmod._build_customer_ledger(self.get_sales_asc(cust), "", "")

        credit_entries = [e for e in ledger["entries"] if e["credit"]]
        self.assertEqual(len(credit_entries), 1)
        self.assertEqual(credit_entries[0]["credit"], 400)
        self.assertEqual(credit_entries[0]["debit"], 0)

    def test_recorded_sale_payment_is_a_credit_entry_not_duplicated_with_leftover(self):
        cust = self.make_customer()
        sale_id = self.make_sale(cust, 1000, 400, invoice="INV-SP")
        db.execute("""INSERT INTO SalePayments (SaleID, PaymentDate, Amount, PaymentMethod)
                    VALUES (?, '2026-09-02', 400, 'Cash')""", (sale_id,))

        with appmod.app.app_context():
            ledger = appmod._build_customer_ledger(self.get_sales_asc(cust), "", "")

        credit_entries = [e for e in ledger["entries"] if e["credit"]]
        self.assertEqual(len(credit_entries), 1)
        self.assertEqual(credit_entries[0]["credit"], 400)

    def test_running_balance_is_chronological_and_reconciles_to_due(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, sale_date="2026-09-01", invoice="INV-1")
        sale2 = self.make_sale(cust, 500, 0, sale_date="2026-09-05", invoice="INV-2")
        db.execute("""INSERT INTO SalePayments (SaleID, PaymentDate, Amount, PaymentMethod)
                    VALUES (?, '2026-09-10', 300, 'Cash')""", (sale2,))

        with appmod.app.app_context():
            ledger = appmod._build_customer_ledger(self.get_sales_asc(cust), "", "")

        balances = [e["balance"] for e in ledger["entries"]]
        self.assertEqual(balances, [1000, 1500, 1200])
        self.assertEqual(ledger["closing_balance"], 1200)
        self.assertEqual(ledger["total_debit"], 1500)
        self.assertEqual(ledger["total_credit"], 300)

    def test_date_range_rolls_earlier_activity_into_opening_balance(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, sale_date="2026-08-01", invoice="INV-OLD")
        self.make_sale(cust, 500, 0, sale_date="2026-09-15", invoice="INV-NEW")

        with appmod.app.app_context():
            ledger = appmod._build_customer_ledger(self.get_sales_asc(cust), "2026-09-01", "")

        self.assertEqual(ledger["opening_balance"], 1000)
        self.assertEqual(len(ledger["entries"]), 1)
        self.assertEqual(ledger["entries"][0]["balance"], 1500)


class CustomerLedgerViewTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, sale_date="2026-09-01", invoice="INV-LEDGER-VIEW"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount)
                           VALUES (?, ?, ?, 'Completed', ?, ?, ?)""",
                           (invoice, customer_id, sale_date, taxable, received, taxable))

    def test_ledger_view_has_the_six_required_columns(self):
        cust = self.make_customer("Ledger Co")
        self.make_sale(cust, 1000, 400)

        client = self.make_admin_client()
        resp = client.get(f"/customers/{cust}/history?view=ledger")
        self.assertEqual(resp.status_code, 200)
        for header in (b"Date", b"Transaction Details", b"Voucher / Ref No.",
                       b"Debit (Dr)", b"Credit (Cr)", b"Running Balance"):
            self.assertIn(header, resp.data)

    def test_ledger_view_suffixes_balance_with_dr_or_cr(self):
        cust = self.make_customer("Ledger Co 2")
        self.make_sale(cust, 1000, 400)

        client = self.make_admin_client()
        resp = client.get(f"/customers/{cust}/history?view=ledger")
        self.assertIn(b"Dr", resp.data)

    def test_ledger_view_has_no_inline_row_buttons(self):
        """The ledger statement is read-only - unlike the Overview tab's
        Invoice History table, no row should carry an action button."""
        cust = self.make_customer("Ledger Co 3")
        self.make_sale(cust, 1000, 400)

        client = self.make_admin_client()
        resp = client.get(f"/customers/{cust}/history?view=ledger")
        html = resp.data.decode()
        ledger_section = html.split("Ledger Statement of Account", 1)[1]
        self.assertNotIn("<a ", ledger_section.split("<style", 1)[0])
        self.assertNotIn("<button", ledger_section.split("<style", 1)[0])

    def test_opening_and_closing_balance_rows_are_bold(self):
        cust = self.make_customer("Ledger Co 4")
        self.make_sale(cust, 1000, 0, sale_date="2026-09-15")

        client = self.make_admin_client()
        resp = client.get(f"/customers/{cust}/history?view=ledger&from=2026-09-01")
        html = resp.data.decode()
        self.assertIn("Opening Balance", html)
        self.assertIn("Closing Balance", html)
        # Both the opening row and the tfoot (closing) row use fw-bold.
        self.assertGreaterEqual(html.count("fw-bold"), 2)

    def test_ledger_table_uses_the_shared_mobile_card_layout(self):
        cust = self.make_customer("Ledger Co 5")
        self.make_sale(cust, 1000, 0)

        client = self.make_admin_client()
        resp = client.get(f"/customers/{cust}/history?view=ledger")
        self.assertIn(b"table-cards-mobile", resp.data)
