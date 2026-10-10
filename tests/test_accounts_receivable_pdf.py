"""Regression tests for the Accounts Receivable Invoice-wise PDF export:
get_ar_base_rows()/get_ar_invoice_rows() must stay the single source of
truth shared with the on-screen report, and the PDF route must actually
produce a real PDF with the same access control as the report itself.
"""
from tests.base import DBTestCase, appmod, db


class AccountsReceivableHelpersTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, status="Completed", invoice="INV-AR-1"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount)
                           VALUES (?, ?, '2026-09-01', ?, ?, ?, ?)""",
                           (invoice, customer_id, status, taxable, received, taxable))

    def test_base_rows_only_includes_invoices_with_a_positive_due(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 400, invoice="INV-DUE")
        self.make_sale(cust, 500, 500, invoice="INV-PAID")
        self.make_sale(cust, 300, 0, status="Cancelled", invoice="INV-CANCELLED")

        with appmod.app.app_context():
            rows = appmod.get_ar_base_rows()
        invoices = {r["InvoiceNumber"] for r in rows}
        self.assertIn("INV-DUE", invoices)
        self.assertNotIn("INV-PAID", invoices)
        self.assertNotIn("INV-CANCELLED", invoices)

    def test_invoice_rows_adds_age_days_and_rounds_due(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000.005, 400, invoice="INV-ROUND")

        with appmod.app.app_context():
            rows = appmod.get_ar_invoice_rows()
        row = next(r for r in rows if r["InvoiceNumber"] == "INV-ROUND")
        self.assertIn("AgeDays", row)
        self.assertEqual(row["Due"], round(1000.005 - 400, 2))


class AccountsReceivableInvoicesPdfTests(DBTestCase):
    def test_pdf_route_returns_a_real_pdf(self):
        cust = self.make_customer("PDF Export Co")
        db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                    TotalAmount, AmountReceived, TaxableAmount)
                    VALUES ('INV-PDF-1', ?, '2026-09-01', 'Completed', 1000, 400, 1000)""", (cust,))

        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable/invoices/pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/pdf")
        self.assertIn("Accounts_Receivable_Invoice_Wise", resp.headers["Content-Disposition"])
        self.assertTrue(resp.data.startswith(b"%PDF"))

    def test_pdf_route_handles_zero_rows_without_erroring(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable/invoices/pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.startswith(b"%PDF"))

    def test_pdf_route_shares_access_control_with_the_report(self):
        """The PDF path starts with /reports/accounts-receivable, so it must
        be gated by the same 'accounts_receivable' tab permission - a Staff
        user with no tabs granted gets redirected away from both."""
        client = self.make_role_client("Staff")
        resp = client.get("/reports/accounts-receivable/invoices/pdf", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

        db.execute("INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES ('Staff', 'accounts_receivable', 1)")
        resp2 = client.get("/reports/accounts-receivable/invoices/pdf")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.mimetype, "application/pdf")

    def test_invoices_tab_has_a_generate_pdf_link(self):
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Generate PDF", resp.data)
        self.assertIn(b"/reports/accounts-receivable/invoices/pdf", resp.data)
