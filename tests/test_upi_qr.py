"""Regression tests for the UPI payment QR code on invoices/receipts:
- Company / GST Settings gets a configurable UPI ID (CompanySettings.UpiId).
- upi_qr.build_upi_qr_png()/build_upi_uri() encode a standard "upi://pay"
  deep link, pre-filled with the still-outstanding balance.
- /sales/<id>/upi-qr.png serves the QR image, 404s when no UPI ID is set.
- The Tax Invoice (HTML + PDF) and 58mm thermal receipt only show the QR
  block when a UPI ID is configured.
"""
from tests.base import DBTestCase, appmod, db
from upi_qr import build_upi_uri, build_upi_qr_png


class UpiUriTests(DBTestCase):
    def test_builds_a_upi_pay_link_with_amount_and_note(self):
        uri = build_upi_uri("9959484777@axisbank", "Test Co", 150.5, "Invoice INV-1")
        self.assertTrue(uri.startswith("upi://pay?"))
        self.assertIn("pa=9959484777%40axisbank", uri)
        self.assertIn("pn=Test%20Co", uri)
        self.assertIn("am=150.50", uri)
        self.assertIn("cu=INR", uri)
        self.assertIn("tn=Invoice%20INV-1", uri)

    def test_omits_amount_when_not_positive(self):
        uri = build_upi_uri("9959484777@axisbank", "Test Co", 0, "Invoice INV-1")
        self.assertNotIn("am=", uri)
        uri2 = build_upi_uri("9959484777@axisbank", "Test Co", None, "Invoice INV-1")
        self.assertNotIn("am=", uri2)

    def test_build_qr_png_returns_none_without_a_upi_id(self):
        self.assertIsNone(build_upi_qr_png("", "Test Co", 100, "Invoice INV-1"))
        self.assertIsNone(build_upi_qr_png(None, "Test Co", 100, "Invoice INV-1"))

    def test_build_qr_png_returns_real_png_bytes(self):
        png = build_upi_qr_png("9959484777@axisbank", "Test Co", 100, "Invoice INV-1")
        self.assertIsNotNone(png)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")


class SettingsFormUpiIdTests(DBTestCase):
    SETTINGS_FORM_BASE = {
        "company_name": "Test Co", "gstin": "", "pan": "", "address": "", "city": "",
        "state_code": "36", "pincode": "", "phone": "", "email": "", "bank_name": "",
        "bank_account_name": "", "bank_account_number": "", "bank_ifsc": "", "bank_branch": "",
        "invoice_prefix": "INV", "invoice_terms": "", "gst_filing_scheme": "Monthly",
        "gst_reminder_days_before": "3", "smtp_port": "587",
    }

    def test_default_is_unset(self):
        company = db.query("SELECT UpiId FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertIsNone(company["UpiId"])

    def test_settings_form_saves_the_upi_id(self):
        client = self.make_admin_client()
        resp = client.post("/settings", data={**self.SETTINGS_FORM_BASE, "upi_id": "9959484777@axisbank"},
                            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        company = db.query("SELECT UpiId FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertEqual(company["UpiId"], "9959484777@axisbank")

    def test_settings_form_blank_upi_id_stores_null(self):
        client = self.make_admin_client()
        client.post("/settings", data={**self.SETTINGS_FORM_BASE, "upi_id": "9959484777@axisbank"},
                    follow_redirects=True)
        client.post("/settings", data={**self.SETTINGS_FORM_BASE, "upi_id": "  "}, follow_redirects=True)
        company = db.query("SELECT UpiId FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertIsNone(company["UpiId"])


class SaleUpiQrRouteAndInvoicesTests(DBTestCase):
    def make_sale(self, customer_id, total=1000.0, received=400.0, invoice="INV-QR-1"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount)
                           VALUES (?, ?, '2026-09-01', 'Completed', ?, ?, ?)""",
                           (invoice, customer_id, total, received, total))

    def set_upi_id(self, upi_id):
        db.execute("UPDATE CompanySettings SET UpiId=? WHERE SettingsID=1", (upi_id,))

    def test_qr_route_404s_without_a_configured_upi_id(self):
        client = self.make_admin_client()
        cust = self.make_customer()
        sale_id = self.make_sale(cust)
        resp = client.get(f"/sales/{sale_id}/upi-qr.png")
        self.assertEqual(resp.status_code, 404)

    def test_qr_route_returns_a_real_png_once_configured(self):
        client = self.make_admin_client()
        self.set_upi_id("9959484777@axisbank")
        cust = self.make_customer()
        sale_id = self.make_sale(cust)
        resp = client.get(f"/sales/{sale_id}/upi-qr.png")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "image/png")
        self.assertEqual(resp.data[:8], b"\x89PNG\r\n\x1a\n")

    def test_qr_route_404s_for_a_nonexistent_sale(self):
        client = self.make_admin_client()
        self.set_upi_id("9959484777@axisbank")
        resp = client.get("/sales/999999/upi-qr.png")
        self.assertEqual(resp.status_code, 404)

    def test_invoice_html_shows_qr_block_only_when_configured(self):
        client = self.make_admin_client()
        cust = self.make_customer()
        sale_id = self.make_sale(cust)

        resp = client.get(f"/sales/{sale_id}/invoice")
        self.assertNotIn(b"Scan to Pay", resp.data)

        self.set_upi_id("9959484777@axisbank")
        resp = client.get(f"/sales/{sale_id}/invoice")
        self.assertIn(b"Scan to Pay", resp.data)
        self.assertIn(f"/sales/{sale_id}/upi-qr.png".encode(), resp.data)

    def test_thermal_receipt_shows_qr_block_only_when_configured(self):
        client = self.make_admin_client()
        cust = self.make_customer()
        sale_id = self.make_sale(cust)

        resp = client.get(f"/sales/{sale_id}/invoice/thermal")
        self.assertNotIn(b"Scan to Pay", resp.data)

        self.set_upi_id("9959484777@axisbank")
        resp = client.get(f"/sales/{sale_id}/invoice/thermal")
        self.assertIn(b"Scan to Pay", resp.data)
        self.assertIn(f"/sales/{sale_id}/upi-qr.png".encode(), resp.data)

    def test_invoice_pdf_renders_without_error_with_and_without_upi_id(self):
        client = self.make_admin_client()
        cust = self.make_customer()
        sale_id = self.make_sale(cust)

        resp = client.get(f"/sales/{sale_id}/invoice.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/pdf")

        self.set_upi_id("9959484777@axisbank")
        resp = client.get(f"/sales/{sale_id}/invoice.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/pdf")
