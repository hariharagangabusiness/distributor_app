"""Regression test for a bug where a Draft sale (saved but not yet billed -
see the "Status" field on the Sale form) leaked into Accounts Receivable as
money owed. get_ar_base_rows()'s own docstring, and accounts_receivable()'s,
both say "Only Completed sales count (Draft isn't billed yet...)" but the
query only excluded Cancelled, not Draft - so any Draft sale with an unpaid
balance showed up as a phantom invoice across every AR view (Total Due,
Customer Summary, Invoice-wise, Aging, Zone-wise, Salesperson-wise, the PDF
export) and could even trigger a Call/WhatsApp payment reminder for an
invoice that was never actually sent to the customer.
"""
from tests.base import DBTestCase, appmod, db


class DraftSaleExcludedFromARTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, invoice, status="Completed"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount) VALUES (?, ?, '2026-10-01', ?, ?, ?, ?)""",
                           (invoice, customer_id, status, taxable * 1.18, received, taxable))

    def setUp(self):
        super().setUp()
        self.cust = self.make_customer("Draft Test Co")
        self.make_sale(self.cust, 1000, 0, "INV-DRAFT-1", status="Draft")

    def test_draft_sale_does_not_appear_in_base_rows(self):
        rows = appmod.get_ar_base_rows()
        self.assertFalse(any(r["InvoiceNumber"] == "INV-DRAFT-1" for r in rows))

    def test_draft_sale_excluded_from_total_due_stat(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary")
        html = resp.data.decode()
        self.assertNotIn("Draft Test Co", html)

    def test_draft_sale_excluded_from_invoice_wise_tab(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertNotIn(b"INV-DRAFT-1", resp.data)

    def test_completed_sale_still_included(self):
        self.make_sale(self.cust, 500, 0, "INV-COMPLETED-1", status="Completed")
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertIn(b"INV-COMPLETED-1", resp.data)

    def test_cancelled_sale_still_excluded(self):
        self.make_sale(self.cust, 500, 0, "INV-CANCELLED-1", status="Cancelled")
        rows = appmod.get_ar_base_rows()
        self.assertFalse(any(r["InvoiceNumber"] == "INV-CANCELLED-1" for r in rows))
