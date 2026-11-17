"""Regression tests for Accounts Receivable payment reminders:
- _ar_phone_e164() normalizes a saved Phone value for tel:/WhatsApp links.
- ar_remind() logs a reminder (ARReminderLog) for the Call/WhatsApp buttons'
  fire-and-forget JS.
- get_ar_reminder_flags() flags an invoice/customer "Reminder due" once
  it's overdue past the configured threshold AND hasn't had a reminder
  logged in that same window - shared by the Invoice-wise and Customer
  Summary tabs.
"""
from datetime import date, timedelta

from tests.base import DBTestCase, appmod, db


class PhoneNormalizationTests(DBTestCase):
    def test_bare_ten_digit_number_gets_india_country_code(self):
        self.assertEqual(appmod._ar_phone_e164("9876543210"), "919876543210")

    def test_number_with_spaces_and_dashes_is_cleaned(self):
        self.assertEqual(appmod._ar_phone_e164("98765-43210"), "919876543210")

    def test_leading_zero_is_dropped_before_adding_country_code(self):
        self.assertEqual(appmod._ar_phone_e164("09876543210"), "919876543210")

    def test_already_has_country_code(self):
        self.assertEqual(appmod._ar_phone_e164("+91 98765 43210"), "919876543210")

    def test_blank_or_missing_returns_none(self):
        self.assertIsNone(appmod._ar_phone_e164(""))
        self.assertIsNone(appmod._ar_phone_e164(None))

    def test_too_short_to_be_a_real_number_returns_none(self):
        self.assertIsNone(appmod._ar_phone_e164("12345"))


class ARReminderLogTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, invoice, sale_date):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount) VALUES (?, ?, ?, 'Completed', ?, ?, ?)""",
                           (invoice, customer_id, sale_date, taxable, received, taxable))

    def setUp(self):
        super().setUp()
        self.cust = self.make_customer("Reminder Test Co")
        db.execute("UPDATE Customers SET Phone='9876543210' WHERE CustomerID=?", (self.cust,))
        self.old_due_date = (date.today() - timedelta(days=20)).isoformat()
        self.sale_id = self.make_sale(self.cust, 1000, 0, "INV-REM-1", self.old_due_date)

    def test_remind_logs_an_invoice_level_reminder(self):
        client = self.make_admin_client()
        resp = client.post("/reports/accounts-receivable/remind",
                            data={"customer_id": str(self.cust), "sale_id": str(self.sale_id), "channel": "Call"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"ok": True})
        row = db.query("SELECT * FROM ARReminderLog", one=True)
        self.assertEqual(row["CustomerID"], self.cust)
        self.assertEqual(row["SaleID"], self.sale_id)
        self.assertEqual(row["Channel"], "Call")

    def test_remind_logs_an_account_level_reminder_with_no_sale_id(self):
        client = self.make_admin_client()
        resp = client.post("/reports/accounts-receivable/remind",
                            data={"customer_id": str(self.cust), "channel": "WhatsApp"})
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT * FROM ARReminderLog", one=True)
        self.assertIsNone(row["SaleID"])
        self.assertEqual(row["Channel"], "WhatsApp")

    def test_remind_rejects_an_invalid_channel(self):
        client = self.make_admin_client()
        resp = client.post("/reports/accounts-receivable/remind",
                            data={"customer_id": str(self.cust), "channel": "SMS"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(db.query("SELECT COUNT(*) c FROM ARReminderLog", one=True)["c"], 0)


class ARReminderDueFlagTests(DBTestCase):
    def make_sale(self, customer_id, taxable, received, invoice, sale_date):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount) VALUES (?, ?, ?, 'Completed', ?, ?, ?)""",
                           (invoice, customer_id, sale_date, taxable, received, taxable))

    def setUp(self):
        super().setUp()
        self.cust = self.make_customer("Overdue Co")
        db.execute("UPDATE Customers SET Phone='9876543210' WHERE CustomerID=?", (self.cust,))

    def test_invoice_past_threshold_with_no_reminder_is_flagged(self):
        old_date = (date.today() - timedelta(days=20)).isoformat()
        sale_id = self.make_sale(self.cust, 1000, 0, "INV-OLD-1", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        html = resp.data.decode()
        self.assertIn("Reminder due", html)
        self.assertIn("1", html)  # the reminders-due stat card

    def test_invoice_under_threshold_is_not_flagged(self):
        recent_date = (date.today() - timedelta(days=1)).isoformat()
        self.make_sale(self.cust, 1000, 0, "INV-NEW-1", recent_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertNotIn(b"Reminder due", resp.data)

    def test_a_recently_logged_reminder_clears_the_flag(self):
        old_date = (date.today() - timedelta(days=20)).isoformat()
        sale_id = self.make_sale(self.cust, 1000, 0, "INV-OLD-2", old_date)
        client = self.make_admin_client()
        client.post("/reports/accounts-receivable/remind",
                    data={"customer_id": str(self.cust), "sale_id": str(sale_id), "channel": "Call"})

        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertNotIn(b"Reminder due", resp.data)

    def test_customer_summary_tab_flags_the_account_once_its_oldest_due_date_is_old_enough(self):
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(self.cust, 1000, 0, "INV-OLD-3", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary")
        self.assertIn(b"Reminder due", resp.data)

    def test_call_and_whatsapp_buttons_render_when_phone_is_present(self):
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(self.cust, 1000, 0, "INV-OLD-4", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        html = resp.data.decode()
        self.assertIn('tel:+919876543210', html)
        self.assertIn('https://wa.me/919876543210', html)

    def test_no_phone_shows_no_phone_instead_of_buttons_on_summary_tab(self):
        cust_no_phone = self.make_customer("No Phone Co")
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(cust_no_phone, 500, 0, "INV-NOPHONE-1", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary")
        html = resp.data.decode()
        self.assertIn("No Phone Co", html)
        self.assertIn("No phone", html)

    def test_settings_page_can_update_the_overdue_threshold(self):
        client = self.make_admin_client()
        resp = client.get("/settings")
        self.assertIn(b"ar_reminder_overdue_days", resp.data)

        settings_data = {
            "company_name": "Test Co", "gstin": "", "pan": "", "address": "", "city": "",
            "state_code": "36", "pincode": "", "phone": "", "email": "", "bank_name": "",
            "bank_account_name": "", "bank_account_number": "", "bank_ifsc": "", "bank_branch": "",
            "invoice_prefix": "INV", "invoice_terms": "", "credit_control_mode": "Informational",
            "credit_block_days": "0", "location_ping_interval_minutes": "30",
            "visit_geofence_radius_meters": "200", "ar_reminder_overdue_days": "15",
        }
        resp = client.post("/settings", data=settings_data, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.query("SELECT ARReminderOverdueDays FROM CompanySettings WHERE SettingsID=1",
                                   one=True)["ARReminderOverdueDays"], 15)

    def test_raising_the_threshold_un_flags_an_invoice_that_was_previously_due(self):
        db.execute("UPDATE CompanySettings SET ARReminderOverdueDays=30 WHERE SettingsID=1")
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(self.cust, 1000, 0, "INV-OLD-5", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertNotIn(b"Reminder due", resp.data)
