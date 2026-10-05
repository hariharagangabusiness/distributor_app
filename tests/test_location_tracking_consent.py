"""Regression tests for the two Location Tracking changes:
1. Working days are now Admin-configurable (CompanySettings.LocationTrackingWorkingDays)
   instead of the old hardcoded "every day except Monday".
2. A tracked role (Staff/Supervisor/Manager) must accept a consent screen at every
   login before reaching anything else - enforced in require_login(), reset by
   session.clear() at every fresh login.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.base import DBTestCase, appmod, db

IST = ZoneInfo("Asia/Kolkata")
A_MONDAY_NOON = datetime(2026, 10, 5, 12, 0, tzinfo=IST)
A_TUESDAY_NOON = datetime(2026, 10, 6, 12, 0, tzinfo=IST)
A_SATURDAY_NOON = datetime(2026, 10, 10, 12, 0, tzinfo=IST)
A_TUESDAY_8AM = datetime(2026, 10, 6, 8, 0, tzinfo=IST)   # before the 9 AM start
A_TUESDAY_8PM = datetime(2026, 10, 6, 20, 0, tzinfo=IST)  # exactly the (exclusive) end hour


class LocationWorkingDaysTests(DBTestCase):
    def test_default_matches_the_original_hardcoded_rule(self):
        with appmod.app.app_context():
            self.assertFalse(appmod._within_location_tracking_hours(A_MONDAY_NOON))
            self.assertTrue(appmod._within_location_tracking_hours(A_TUESDAY_NOON))
            self.assertTrue(appmod._within_location_tracking_hours(A_SATURDAY_NOON))

    def test_hours_boundary_still_enforced(self):
        with appmod.app.app_context():
            self.assertFalse(appmod._within_location_tracking_hours(A_TUESDAY_8AM))
            self.assertFalse(appmod._within_location_tracking_hours(A_TUESDAY_8PM))

    def test_admin_configured_working_days_change_the_result(self):
        db.execute("UPDATE CompanySettings SET LocationTrackingWorkingDays='Mon,Tue,Wed,Thu,Fri' WHERE SettingsID=1")
        with appmod.app.app_context():
            self.assertTrue(appmod._within_location_tracking_hours(A_MONDAY_NOON))   # now a working day
            self.assertFalse(appmod._within_location_tracking_hours(A_SATURDAY_NOON))  # no longer one


class LocationConsentGateTests(DBTestCase):
    def test_unconsented_staff_is_redirected_to_consent_from_any_page(self):
        client = self.make_role_client("Staff", accept_location_consent=False)
        resp = client.get("/account/change-password", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/location-consent", resp.headers["Location"])

    def test_accepting_consent_unblocks_normal_navigation_and_is_logged(self):
        client = self.make_role_client("Staff", accept_location_consent=False)
        resp = client.post("/location-consent", data={}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        resp2 = client.get("/account/change-password")
        self.assertEqual(resp2.status_code, 200)

        user = db.query("SELECT UserID FROM Users WHERE Username='test_staff'", one=True)
        count = db.query("SELECT COUNT(*) c FROM LocationConsentLog WHERE UserID=?",
                          (user["UserID"],), one=True)["c"]
        self.assertEqual(count, 1)

    def test_admin_is_never_gated(self):
        client = self.make_admin_client()
        resp = client.get("/account/change-password", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)

    def test_consent_required_again_after_logout_and_relogin(self):
        client = self.make_role_client("Staff", accept_location_consent=True)
        resp = client.get("/account/change-password")
        self.assertEqual(resp.status_code, 200)

        client.post("/logout", follow_redirects=True)
        client.post("/login", data={"username": "test_staff", "password": "TestPass123!"}, follow_redirects=True)

        resp2 = client.get("/account/change-password", follow_redirects=False)
        self.assertEqual(resp2.status_code, 302)
        self.assertIn("/location-consent", resp2.headers["Location"])

    def test_consent_page_shows_the_configured_or_default_text(self):
        client = self.make_role_client("Staff", accept_location_consent=False)
        resp = client.get("/location-consent")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"I Accept", resp.data)

        db.execute("UPDATE CompanySettings SET LocationConsentText='Custom legal notice text.' WHERE SettingsID=1")
        resp2 = client.get("/location-consent")
        self.assertIn(b"Custom legal notice text.", resp2.data)


class LocationSettingsFormTests(DBTestCase):
    def test_settings_page_renders_location_tracking_section(self):
        client = self.make_admin_client()
        resp = client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Location Tracking", resp.data)
        self.assertIn(b"working_day", resp.data)

    def test_saving_settings_updates_working_days_and_consent_text(self):
        client = self.make_admin_client()
        resp = client.get("/settings")
        form_data = {
            "company_name": "X", "gstin": "", "pan": "", "address": "", "city": "",
            "state_code": "36", "pincode": "", "phone": "", "email": "",
            "bank_name": "", "bank_account_name": "", "bank_account_number": "",
            "bank_ifsc": "", "bank_branch": "", "invoice_prefix": "INV", "invoice_terms": "",
            "credit_control_mode": "Informational", "credit_block_days": "30",
            "working_day": ["Mon", "Wed", "Fri"],
            "location_consent_text": "Approved legal text.",
        }
        resp = client.post("/settings", data=form_data, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertEqual(company["LocationTrackingWorkingDays"], "Mon,Wed,Fri")
        self.assertEqual(company["LocationConsentText"], "Approved legal text.")

    def test_unchecking_all_working_days_falls_back_to_the_default_rather_than_empty(self):
        client = self.make_admin_client()
        form_data = {
            "company_name": "X", "gstin": "", "pan": "", "address": "", "city": "",
            "state_code": "36", "pincode": "", "phone": "", "email": "",
            "bank_name": "", "bank_account_name": "", "bank_account_number": "",
            "bank_ifsc": "", "bank_branch": "", "invoice_prefix": "INV", "invoice_terms": "",
            "credit_control_mode": "Informational", "credit_block_days": "30",
            "location_consent_text": "",
        }
        client.post("/settings", data=form_data, follow_redirects=True)
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertEqual(company["LocationTrackingWorkingDays"], appmod.DEFAULT_LOCATION_WORKING_DAYS)
