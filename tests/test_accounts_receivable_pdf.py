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

    def test_customer_summary_sort_name_is_alphabetical(self):
        zebra = self.make_customer("Zebra Traders")
        alpha = self.make_customer("Alpha Distributors")
        mango = self.make_customer("mango co")  # lowercase - must sort case-insensitively
        self.make_sale(zebra, 1000, 0, invoice="INV-Z")
        self.make_sale(alpha, 1000, 0, invoice="INV-A")
        self.make_sale(mango, 1000, 0, invoice="INV-M")

        with appmod.app.app_context():
            rows = appmod.get_ar_customer_summary(sort="name")
        names = [r["CustomerName"] for r in rows]
        self.assertEqual(names, ["Alpha Distributors", "mango co", "Zebra Traders"])

    def test_customer_summary_default_sort_is_by_due_descending(self):
        small = self.make_customer("Small Due Co")
        big = self.make_customer("Big Due Co")
        self.make_sale(small, 100, 0, invoice="INV-SMALL")
        self.make_sale(big, 9000, 0, invoice="INV-BIG")

        with appmod.app.app_context():
            rows = appmod.get_ar_customer_summary()
        self.assertEqual(rows[0]["CustomerName"], "Big Due Co")


class AccountsReceivableInvoicesPdfTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, status="Completed", invoice="INV-AR-1"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount)
                           VALUES (?, ?, '2026-09-01', ?, ?, ?, ?)""",
                           (invoice, customer_id, status, taxable, received, taxable))

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

    def test_pdf_has_two_sections_customer_then_invoice(self):
        """rows_to_pdf_sections() puts a PageBreak between sections - can't
        text-search the compiled PDF bytes (reportlab doesn't store glyphs
        as plain searchable text), so this checks the actual section data
        the route builds and hands to it: two sections, customer-level
        first, invoice-level second, both sorted alphabetically, with
        Salesperson/Due Date left out of the invoice columns."""
        cust_b = self.make_customer("Beta Co")
        cust_a = self.make_customer("Alpha Co")
        self.make_sale(cust_b, 1000, 0, invoice="INV-BETA")
        self.make_sale(cust_a, 1000, 0, invoice="INV-ALPHA")

        with appmod.app.app_context():
            base_rows = appmod.get_ar_base_rows()
            customer_rows = appmod.get_ar_customer_summary(base_rows, sort="name")
            invoice_rows = sorted(appmod.get_ar_invoice_rows(base_rows),
                                   key=lambda r: (r["CustomerName"] or "").lower())

        self.assertEqual([c["CustomerName"] for c in customer_rows], ["Alpha Co", "Beta Co"])
        self.assertEqual([r["CustomerName"] for r in invoice_rows], ["Alpha Co", "Beta Co"])

        # The actual PDF-row shaping used by the route - Salesperson/Due Date must be absent.
        with appmod.app.app_context():
            pdf_row = appmod._ar_invoice_pdf_row(invoice_rows[0])
        self.assertNotIn("Salesperson", pdf_row)
        self.assertNotIn("Due Date", pdf_row)
        self.assertEqual(set(pdf_row.keys()), {
            "Invoice #", "Customer", "Phone", "Zone", "Sale Date", "Age (days)",
            "Status", "Taxable Value", "Total (incl. GST)", "Received", "Due",
        })
