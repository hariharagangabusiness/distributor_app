"""Regression tests for Accounts Receivable payment reminders:
- _ar_phone_e164() normalizes a saved Phone value for tel:/WhatsApp links.
- ar_remind() logs a reminder (ARReminderLog) for the Call/WhatsApp buttons'
  fire-and-forget JS - an account-level reminder (no sale_id) logs one row
  per invoice currently contributing to that customer's due balance.
- get_ar_reminder_flags() flags an invoice "Reminder due" once it's overdue
  past the configured threshold AND hasn't had a reminder logged in that
  same window - except past ARReminderDailyAfterDays, where it's re-flagged
  every day instead. A customer (Customer Summary tab) is flagged whenever
  any of their own invoices is, so both tabs always agree.
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

    def test_remind_logs_an_account_level_reminder_against_every_outstanding_invoice(self):
        """A whole-account reminder's one message covers every invoice contributing to the
        balance it quotes, so it's logged against each of them (not a single account-wide
        entry) - that's what lets it also clear each invoice's own Invoice-wise flag."""
        second_sale_id = self.make_sale(self.cust, 500, 0, "INV-REM-2", self.old_due_date)
        client = self.make_admin_client()
        resp = client.post("/reports/accounts-receivable/remind",
                            data={"customer_id": str(self.cust), "channel": "WhatsApp"})
        self.assertEqual(resp.status_code, 200)
        rows = db.query("SELECT * FROM ARReminderLog ORDER BY SaleID")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["SaleID"] for r in rows}, {self.sale_id, second_sale_id})
        self.assertTrue(all(r["Channel"] == "WhatsApp" for r in rows))

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

    def test_invoice_level_row_has_a_separate_pdf_button_next_to_whatsapp(self):
        """The WhatsApp button itself must stay a plain wa.me link with nothing else attached to
        its click (so it always navigates straight to the chat, not fight a second popup for the
        browser's one-popup-per-click allowance) - the invoice PDF gets its own separate button/
        link instead."""
        old_date = (date.today() - timedelta(days=20)).isoformat()
        sale_id = self.make_sale(self.cust, 1000, 0, "INV-OLD-6", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        html = resp.data.decode()
        self.assertRegex(html, r'<a[^>]*\bjs-ar-remind-wa-invoice\b')
        self.assertNotRegex(html, r'<a[^>]*\bjs-ar-remind-wa-invoice\b[^>]*\bdata-pdf-url\b')
        self.assertIn(f"/sales/{sale_id}/invoice.pdf", html)

    def test_account_level_whatsapp_button_has_no_pdf_attachment(self):
        """Attaching one specific invoice makes no sense for a whole-account reminder (it could
        cover several invoices) - only the Invoice-wise tab's row gets the separate PDF button.
        (The class name itself still appears elsewhere on the page, in the always-present JS that
        wires up that other tab's button - so this checks for an actual <a> tag carrying it,
        not just the substring.)"""
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(self.cust, 1000, 0, "INV-OLD-7", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary")
        html = resp.data.decode()
        self.assertIn("https://wa.me/919876543210", html)
        self.assertNotRegex(html, r'<a[^>]*\bjs-ar-remind-wa-invoice\b')
        self.assertNotIn("invoice.pdf", html)

    def test_settings_page_can_update_the_overdue_threshold(self):
        client = self.make_admin_client()
        resp = client.get("/settings")
        self.assertIn(b"ar_reminder_overdue_days", resp.data)
        self.assertIn(b"ar_reminder_daily_after_days", resp.data)

        settings_data = {
            "company_name": "Test Co", "gstin": "", "pan": "", "address": "", "city": "",
            "state_code": "36", "pincode": "", "phone": "", "email": "", "bank_name": "",
            "bank_account_name": "", "bank_account_number": "", "bank_ifsc": "", "bank_branch": "",
            "invoice_prefix": "INV", "invoice_terms": "", "credit_control_mode": "Informational",
            "credit_block_days": "0", "location_ping_interval_minutes": "30",
            "visit_geofence_radius_meters": "200", "ar_reminder_overdue_days": "15",
            "ar_reminder_daily_after_days": "21",
        }
        resp = client.post("/settings", data=settings_data, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT ARReminderOverdueDays, ARReminderDailyAfterDays FROM CompanySettings WHERE SettingsID=1",
                        one=True)
        self.assertEqual(row["ARReminderOverdueDays"], 15)
        self.assertEqual(row["ARReminderDailyAfterDays"], 21)

    def test_raising_the_threshold_un_flags_an_invoice_that_was_previously_due(self):
        db.execute("UPDATE CompanySettings SET ARReminderOverdueDays=30 WHERE SettingsID=1")
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(self.cust, 1000, 0, "INV-OLD-5", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertNotIn(b"Reminder due", resp.data)

    def test_badly_overdue_invoice_is_due_again_even_within_the_normal_cooldown_window(self):
        """Past ARReminderDailyAfterDays (14 by default), the normal 'once every threshold days'
        cooldown is ignored - a reminder logged a few days ago (well within the default 7-day
        threshold window) still leaves it flagged, since daily cadence only cares whether today's
        reminder has gone out yet."""
        old_date = (date.today() - timedelta(days=25)).isoformat()  # AgeDays=25 > 14
        sale_id = self.make_sale(self.cust, 1000, 0, "INV-DAILY-1", old_date)
        recent_sent_at = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d 10:00:00")
        db.execute("""INSERT INTO ARReminderLog (CustomerID, SaleID, Channel, SentAt)
                    VALUES (?,?,?,?)""", (self.cust, sale_id, "Call", recent_sent_at))
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertIn(b"Reminder due", resp.data)

    def test_badly_overdue_invoice_already_reminded_today_is_not_due_again(self):
        old_date = (date.today() - timedelta(days=25)).isoformat()
        sale_id = self.make_sale(self.cust, 1000, 0, "INV-DAILY-2", old_date)
        today_sent_at = date.today().strftime("%Y-%m-%d 09:00:00")
        db.execute("""INSERT INTO ARReminderLog (CustomerID, SaleID, Channel, SentAt)
                    VALUES (?,?,?,?)""", (self.cust, sale_id, "Call", today_sent_at))
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertNotIn(b"Reminder due", resp.data)

    def test_moderately_overdue_invoice_still_follows_the_normal_cooldown_not_daily(self):
        """Between the threshold and ARReminderDailyAfterDays, a reminder logged a few days ago
        still clears the flag for the rest of that threshold-day window - only past
        ARReminderDailyAfterDays does it switch to daily."""
        old_date = (date.today() - timedelta(days=10)).isoformat()  # threshold(7) < AgeDays(10) <= 14
        sale_id = self.make_sale(self.cust, 1000, 0, "INV-MID-1", old_date)
        recent_sent_at = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d 10:00:00")
        db.execute("""INSERT INTO ARReminderLog (CustomerID, SaleID, Channel, SentAt)
                    VALUES (?,?,?,?)""", (self.cust, sale_id, "Call", recent_sent_at))
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertNotIn(b"Reminder due", resp.data)

    def test_customer_summary_clears_once_its_only_overdue_invoice_is_reminded(self):
        old_date = (date.today() - timedelta(days=20)).isoformat()
        sale_id = self.make_sale(self.cust, 1000, 0, "INV-SYNC-1", old_date)
        client = self.make_admin_client()
        client.post("/reports/accounts-receivable/remind",
                    data={"customer_id": str(self.cust), "sale_id": str(sale_id), "channel": "WhatsApp"})

        resp = client.get("/reports/accounts-receivable?view=summary")
        self.assertNotIn(b"Reminder due", resp.data)

    def test_customer_summary_stays_flagged_while_a_second_invoice_is_unreminded(self):
        old_date = (date.today() - timedelta(days=20)).isoformat()
        sale_id = self.make_sale(self.cust, 1000, 0, "INV-SYNC-2", old_date)
        self.make_sale(self.cust, 500, 0, "INV-SYNC-3", old_date)  # left unreminded
        client = self.make_admin_client()
        client.post("/reports/accounts-receivable/remind",
                    data={"customer_id": str(self.cust), "sale_id": str(sale_id), "channel": "WhatsApp"})

        resp = client.get("/reports/accounts-receivable?view=summary")
        self.assertIn(b"Reminder due", resp.data)

    def test_account_level_remind_clears_invoice_wise_flags_too(self):
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(self.cust, 1000, 0, "INV-SYNC-4", old_date)
        self.make_sale(self.cust, 500, 0, "INV-SYNC-5", old_date)
        client = self.make_admin_client()
        client.post("/reports/accounts-receivable/remind",
                    data={"customer_id": str(self.cust), "channel": "WhatsApp"})

        resp = client.get("/reports/accounts-receivable?view=invoices")
        self.assertNotIn(b"Reminder due", resp.data)

    def test_invoice_rows_with_a_phone_get_a_bulk_select_checkbox(self):
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(self.cust, 1000, 0, "INV-OLD-8", old_date)
        no_phone_cust = self.make_customer("No Phone Co")
        self.make_sale(no_phone_cust, 500, 0, "INV-NOPHONE-2", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        html = resp.data.decode()
        self.assertEqual(html.count('class="js-ar-bulk-select"'), 1)
        self.assertIn('id="ar-select-all"', html)
        self.assertIn('id="ar-bulk-modal"', html)

    def test_no_phones_at_all_skips_the_select_all_checkbox(self):
        no_phone_cust = self.make_customer("No Phone Co 2")
        old_date = (date.today() - timedelta(days=20)).isoformat()
        self.make_sale(no_phone_cust, 500, 0, "INV-NOPHONE-3", old_date)
        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=invoices")
        html = resp.data.decode()
        self.assertNotIn('id="ar-select-all"', html)
        self.assertNotIn('class="js-ar-bulk-select"', html)
