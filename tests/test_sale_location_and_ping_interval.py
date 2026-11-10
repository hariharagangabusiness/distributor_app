"""Regression tests for two Location Tracking additions:
1. CompanySettings.LocationPingIntervalMinutes makes the periodic ping
   interval (used to be a hardcoded 30 minutes) Admin-configurable.
2. Every Sale - any role, best-effort - now also captures a location point
   at the moment it's saved (log_sale_location()), tagged LocationLogs.
   RefType='Sale'/RefID=<SaleID>, shown as its own marker on the Location
   Tracking map.
"""
from tests.base import DBTestCase, appmod, db


class PingIntervalTests(DBTestCase):
    def test_default_is_30_minutes(self):
        company = db.query("SELECT LocationPingIntervalMinutes FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertEqual(company["LocationPingIntervalMinutes"], 30)

    def test_settings_form_saves_a_custom_interval(self):
        client = self.make_admin_client()
        resp = client.post("/settings", data={
            "company_name": "Test Co", "gstin": "", "pan": "", "address": "", "city": "",
            "state_code": "36", "pincode": "", "phone": "", "email": "", "bank_name": "",
            "bank_account_name": "", "bank_account_number": "", "bank_ifsc": "", "bank_branch": "",
            "invoice_prefix": "INV", "invoice_terms": "", "gst_filing_scheme": "Monthly",
            "gst_reminder_days_before": "3", "smtp_port": "587",
            "location_ping_interval_minutes": "15",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        company = db.query("SELECT LocationPingIntervalMinutes FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertEqual(company["LocationPingIntervalMinutes"], 15)

    def test_settings_form_clamps_to_a_sane_range(self):
        client = self.make_admin_client()
        client.post("/settings", data={
            "company_name": "Test Co", "gstin": "", "pan": "", "address": "", "city": "",
            "state_code": "36", "pincode": "", "phone": "", "email": "", "bank_name": "",
            "bank_account_name": "", "bank_account_number": "", "bank_ifsc": "", "bank_branch": "",
            "invoice_prefix": "INV", "invoice_terms": "", "gst_filing_scheme": "Monthly",
            "gst_reminder_days_before": "3", "smtp_port": "587",
            "location_ping_interval_minutes": "99999",
        }, follow_redirects=True)
        company = db.query("SELECT LocationPingIntervalMinutes FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertEqual(company["LocationPingIntervalMinutes"], 720)

    def test_injected_into_the_page_for_a_tracked_role(self):
        db.execute("INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES ('Staff', 'dashboard', 1)")
        client = self.make_role_client("Staff")
        db.execute("UPDATE CompanySettings SET LocationPingIntervalMinutes=45 WHERE SettingsID=1")
        resp = client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"LOCATION_PING_INTERVAL_MINUTES = 45", resp.data)


class LogSaleLocationTests(DBTestCase):
    def make_user(self, username="loc_test_admin", role="Admin"):
        from werkzeug.security import generate_password_hash
        uid = db.execute("INSERT INTO Users (Username, PasswordHash, Role, Active) VALUES (?,?,?,1)",
                          (username, generate_password_hash("x"), role))
        return db.query("SELECT * FROM Users WHERE UserID=?", (uid,), one=True)

    def test_valid_coordinates_are_stored_tagged_to_the_sale(self):
        cust = self.make_customer()
        prod = self.make_product()
        user = self.make_user()
        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-11-10", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36", lines=[(prod, 1, 100.0)])
            appmod.log_sale_location(sale_id, user, "12.9716", "77.5946", "15.5")

        row = db.query("SELECT * FROM LocationLogs WHERE RefType='Sale' AND RefID=?", (sale_id,), one=True)
        self.assertIsNotNone(row)
        self.assertEqual(row["Latitude"], 12.9716)
        self.assertEqual(row["Longitude"], 77.5946)
        self.assertEqual(row["Accuracy"], 15.5)
        self.assertEqual(row["UserID"], user["UserID"])
        self.assertEqual(row["Role"], user["Role"])

    def test_missing_coordinates_silently_does_nothing(self):
        user = self.make_user()
        with appmod.app.app_context():
            appmod.log_sale_location(1, user, "", "", "")
            appmod.log_sale_location(1, user, None, None, None)
        count = db.query("SELECT COUNT(*) c FROM LocationLogs WHERE RefType='Sale'", one=True)["c"]
        self.assertEqual(count, 0)

    def test_garbage_coordinates_silently_does_nothing_rather_than_raising(self):
        user = self.make_user()
        with appmod.app.app_context():
            appmod.log_sale_location(1, user, "not-a-number", "also-not", "x")
        count = db.query("SELECT COUNT(*) c FROM LocationLogs WHERE RefType='Sale'", one=True)["c"]
        self.assertEqual(count, 0)

    def test_no_user_silently_does_nothing(self):
        with appmod.app.app_context():
            appmod.log_sale_location(1, None, "12.0", "77.0", "5")
        count = db.query("SELECT COUNT(*) c FROM LocationLogs", one=True)["c"]
        self.assertEqual(count, 0)

    def test_bad_accuracy_falls_back_to_null_but_still_stores_the_point(self):
        user = self.make_user()
        with appmod.app.app_context():
            appmod.log_sale_location(1, user, "12.0", "77.0", "not-a-number")
        row = db.query("SELECT * FROM LocationLogs WHERE RefType='Sale'", one=True)
        self.assertIsNotNone(row)
        self.assertIsNone(row["Accuracy"])


class SaleFormCapturesLocationTests(DBTestCase):
    def test_new_sale_with_coordinates_creates_a_tagged_location_point(self):
        cust = self.make_customer()
        prod = self.make_product()
        client = self.make_admin_client()
        resp = client.post("/sales/new", data={
            "customer_id": str(cust), "customer_name": "", "customer_address": "",
            "customer_phone": "", "customer_zone": "North", "sale_date": "2026-11-10",
            "status": "Completed", "place_of_supply_code": "36", "payment_status": "Paid",
            "payment_due_date": "", "cash_amount": "100", "bank_amount": "0",
            "product_id[]": [str(prod)], "qty[]": ["1"], "unit_price[]": ["100"], "discount_amount[]": ["0"],
            "sale_lat": "12.9716", "sale_lng": "77.5946", "sale_accuracy": "20",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        sale = db.query("SELECT SaleID FROM Sales WHERE CustomerID=?", (cust,), one=True)
        self.assertIsNotNone(sale)
        loc = db.query("SELECT * FROM LocationLogs WHERE RefType='Sale' AND RefID=?", (sale["SaleID"],), one=True)
        self.assertIsNotNone(loc)
        self.assertEqual(loc["Latitude"], 12.9716)

    def test_new_sale_without_coordinates_still_saves_fine_with_no_location_row(self):
        cust = self.make_customer()
        prod = self.make_product()
        client = self.make_admin_client()
        resp = client.post("/sales/new", data={
            "customer_id": str(cust), "customer_name": "", "customer_address": "",
            "customer_phone": "", "customer_zone": "North", "sale_date": "2026-11-10",
            "status": "Completed", "place_of_supply_code": "36", "payment_status": "Paid",
            "payment_due_date": "", "cash_amount": "100", "bank_amount": "0",
            "product_id[]": [str(prod)], "qty[]": ["1"], "unit_price[]": ["100"], "discount_amount[]": ["0"],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        sale = db.query("SELECT SaleID FROM Sales WHERE CustomerID=?", (cust,), one=True)
        self.assertIsNotNone(sale)
        count = db.query("SELECT COUNT(*) c FROM LocationLogs", one=True)["c"]
        self.assertEqual(count, 0)

    def test_admin_role_sale_still_gets_a_location_point(self):
        """Unlike the periodic ping, this applies to every role - including
        Admin, which the ongoing tracking system never covers at all."""
        cust = self.make_customer()
        prod = self.make_product()
        client = self.make_admin_client()
        client.post("/sales/new", data={
            "customer_id": str(cust), "customer_name": "", "customer_address": "",
            "customer_phone": "", "customer_zone": "North", "sale_date": "2026-11-10",
            "status": "Completed", "place_of_supply_code": "36", "payment_status": "Paid",
            "payment_due_date": "", "cash_amount": "100", "bank_amount": "0",
            "product_id[]": [str(prod)], "qty[]": ["1"], "unit_price[]": ["100"], "discount_amount[]": ["0"],
            "sale_lat": "1.0", "sale_lng": "2.0", "sale_accuracy": "",
        }, follow_redirects=True)
        row = db.query("SELECT Role FROM LocationLogs WHERE RefType='Sale'", one=True)
        self.assertEqual(row["Role"], "Admin")

    def test_editing_a_sale_with_coordinates_adds_its_own_location_point(self):
        cust = self.make_customer()
        prod = self.make_product()
        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-11-10", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36", lines=[(prod, 1, 100.0)])
        client = self.make_admin_client()
        resp = client.post(f"/sales/{sale_id}/edit", data={
            "customer_id": str(cust), "customer_name": "", "customer_address": "",
            "customer_phone": "", "customer_zone": "North", "sale_date": "2026-11-10",
            "status": "Completed", "place_of_supply_code": "36", "payment_status": "Paid",
            "payment_due_date": "", "cash_amount": "100", "bank_amount": "0",
            "product_id[]": [str(prod)], "qty[]": ["1"], "unit_price[]": ["100"], "discount_amount[]": ["0"],
            "sale_lat": "9.0", "sale_lng": "10.0", "sale_accuracy": "8",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        loc = db.query("SELECT * FROM LocationLogs WHERE RefType='Sale' AND RefID=?", (sale_id,), one=True)
        self.assertIsNotNone(loc)
        self.assertEqual(loc["Latitude"], 9.0)


class LocationTrackingMapSaleMarkerTests(DBTestCase):
    def test_sale_point_is_flagged_and_enriched_in_by_user(self):
        cust = self.make_customer("Map Test Co")
        prod = self.make_product()
        client = self.make_admin_client()
        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-11-10", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36", lines=[(prod, 1, 100.0)])
            user = db.query("SELECT * FROM Users LIMIT 1", one=True)
            appmod.log_sale_location(sale_id, user, "12.0", "77.0", "5")

        recorded_date = db.query("SELECT RecordedDate FROM LocationLogs WHERE RefType='Sale' AND RefID=?",
                                  (sale_id,), one=True)["RecordedDate"]
        resp = client.get(f"/location-tracking?date={recorded_date}")
        html = resp.data.decode()
        self.assertIn("is_sale", html)
        self.assertIn("Map Test Co", html)

    def test_routine_ping_point_is_not_flagged_as_a_sale(self):
        user_row = None
        with appmod.app.app_context():
            from werkzeug.security import generate_password_hash
            uid = db.execute("INSERT INTO Users (Username, PasswordHash, Role, Active) VALUES (?,?,?,1)",
                              ("ping_user", generate_password_hash("x"), "Staff"))
            db.execute("""INSERT INTO LocationLogs (UserID, Role, Latitude, Longitude, RecordedAt, RecordedDate)
                        VALUES (?, 'Staff', 11.0, 76.0, '2026-11-10 10:00:00', '2026-11-10')""", (uid,))
        client = self.make_admin_client()
        resp = client.get("/location-tracking?date=2026-11-10")
        self.assertEqual(resp.status_code, 200)
