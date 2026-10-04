"""Regression tests for the customer-dues warning + credit block on the
New/Edit Sale screens: get_customer_due_info() must match the Accounts
Receivable report's own definition, check_credit_block() must respect the
3-way CreditControlMode (Informational / BlockAfterDays / BlockImmediate)
and only ever block a non-Admin, and the /customers/<id>/due endpoint + the
real /sales/new route must wire all of that together correctly.
"""
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from tests.base import DBTestCase, appmod, db


def iso_days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


class CustomerCreditBlockTests(DBTestCase):
    def make_staff_client(self, username="test_staff", password="TestPass123!"):
        db.execute("INSERT INTO Users (Username, PasswordHash, Role, Active) VALUES (?, ?, 'Staff', 1)",
                   (username, generate_password_hash(password)))
        # Staff has no tab access by default (see ACCESS_TABS/RoleTabPermissions in app.py) -
        # grant the Sales tab so the test can actually reach /sales/new.
        db.execute("INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES ('Staff', 'sales', 1)")
        client = appmod.app.test_client()
        resp = client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
        assert resp.status_code == 200, f"login failed: {resp.status_code}"
        return client

    def make_sale(self, customer_id, taxable, received, status="Completed", days_ago=0, invoice="INV-TEST-1"):
        return db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status,
                           TotalAmount, AmountReceived, TaxableAmount)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                           (invoice, customer_id, iso_days_ago(days_ago), status, taxable, received, taxable))

    # --- get_customer_due_info() ---

    def test_due_info_sums_only_non_cancelled_sales(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 400, status="Completed", days_ago=10, invoice="INV-A")
        self.make_sale(cust, 500, 0, status="Cancelled", days_ago=5, invoice="INV-B")

        with appmod.app.app_context():
            info = appmod.get_customer_due_info(cust)
        self.assertEqual(info["due"], 600)
        self.assertEqual(info["days_overdue"], 10)

    def test_due_info_zero_when_fully_paid(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 1000, days_ago=10, invoice="INV-PAID")

        with appmod.app.app_context():
            info = appmod.get_customer_due_info(cust)
        self.assertEqual(info["due"], 0)
        self.assertIsNone(info["oldest_due_date"])

    def test_due_info_matches_accounts_receivable_report(self):
        """Not a style preference - the same customer must show the same due
        figure on both the AR report and the Sale form's warning."""
        cust = self.make_customer("AR Match Co")
        self.make_sale(cust, 2000, 500, days_ago=20, invoice="INV-AR-1")
        self.make_sale(cust, 1000, 1000, days_ago=5, invoice="INV-AR-2")

        with appmod.app.app_context():
            info = appmod.get_customer_due_info(cust)

        client = self.make_admin_client()
        resp = client.get("/reports/accounts-receivable?view=summary")
        self.assertEqual(resp.status_code, 200)
        # The report formats currency Indian-style (comma grouping) - match that, not a plain .2f.
        self.assertIn(appmod.indian_number_format(info["due"]).encode(), resp.data)

    # --- /customers/<id>/due endpoint ---

    def test_due_endpoint_returns_expected_json(self):
        cust = self.make_customer()
        self.make_sale(cust, 1200, 200, days_ago=3, invoice="INV-EP-1")
        client = self.make_admin_client()
        resp = client.get(f"/customers/{cust}/due")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["due"], 1000)
        self.assertEqual(data["days_overdue"], 3)

    def test_due_endpoint_unknown_customer_is_404_not_a_crash(self):
        client = self.make_admin_client()
        resp = client.get("/customers/999999/due")
        self.assertEqual(resp.status_code, 404)

    # --- check_credit_block() ---

    def test_block_off_by_default(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, days_ago=999, invoice="INV-OLD")
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        self.assertEqual(company["CreditControlMode"], "Informational")
        with appmod.app.app_context():
            appmod.check_credit_block(cust, company, {"Role": "Staff"})  # must not raise

    def test_informational_mode_never_blocks_even_with_days_configured(self):
        """Mode is the real gate - a leftover/positive CreditBlockDays value
        must not matter at all while the mode is still Informational."""
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, days_ago=999, invoice="INV-INFO")
        db.execute("UPDATE CompanySettings SET CreditControlMode='Informational', CreditBlockDays=1 WHERE SettingsID=1")
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        with appmod.app.app_context():
            appmod.check_credit_block(cust, company, {"Role": "Staff"})  # must not raise

    def test_non_admin_blocked_past_the_configured_days(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, days_ago=45, invoice="INV-OLD2")
        db.execute("UPDATE CompanySettings SET CreditControlMode='BlockAfterDays', CreditBlockDays=30 WHERE SettingsID=1")
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        with appmod.app.app_context():
            with self.assertRaises(ValueError):
                appmod.check_credit_block(cust, company, {"Role": "Staff"})

    def test_admin_bypasses_the_block(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, days_ago=45, invoice="INV-OLD3")
        db.execute("UPDATE CompanySettings SET CreditControlMode='BlockAfterDays', CreditBlockDays=30 WHERE SettingsID=1")
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        with appmod.app.app_context():
            appmod.check_credit_block(cust, company, {"Role": "Admin"})  # must not raise

    def test_not_blocked_when_under_the_day_threshold(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, days_ago=10, invoice="INV-RECENT")
        db.execute("UPDATE CompanySettings SET CreditControlMode='BlockAfterDays', CreditBlockDays=30 WHERE SettingsID=1")
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        with appmod.app.app_context():
            appmod.check_credit_block(cust, company, {"Role": "Staff"})  # must not raise

    def test_block_immediate_mode_blocks_with_no_grace_period(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 0, days_ago=1, invoice="INV-IMM")  # barely overdue
        db.execute("UPDATE CompanySettings SET CreditControlMode='BlockImmediate' WHERE SettingsID=1")
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        with appmod.app.app_context():
            with self.assertRaises(ValueError):
                appmod.check_credit_block(cust, company, {"Role": "Staff"})

    def test_block_immediate_mode_does_not_block_a_fully_paid_customer(self):
        cust = self.make_customer()
        self.make_sale(cust, 1000, 1000, days_ago=100, invoice="INV-IMM-PAID")
        db.execute("UPDATE CompanySettings SET CreditControlMode='BlockImmediate' WHERE SettingsID=1")
        company = db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)
        with appmod.app.app_context():
            appmod.check_credit_block(cust, company, {"Role": "Staff"})  # must not raise

    # --- end-to-end through the real /sales/new route ---

    def test_staff_cannot_save_new_sale_for_overdue_customer_past_the_limit(self):
        prod = self.make_product()
        cust = self.make_customer("Overdue Customer")
        self.make_sale(cust, 1000, 0, days_ago=45, invoice="INV-E2E-1")
        db.execute("UPDATE CompanySettings SET CreditControlMode='BlockAfterDays', CreditBlockDays=30 WHERE SettingsID=1")

        client = self.make_staff_client()
        resp = client.post("/sales/new", data={
            "customer_id": str(cust), "customer_name": "Overdue Customer", "customer_zone": "",
            "sale_date": date.today().isoformat(), "status": "Completed", "payment_status": "Unpaid",
            "place_of_supply_code": "36", "cash_amount": "100", "bank_amount": "0",
            "product_id[]": [str(prod)], "qty[]": ["1"], "unit_price[]": ["100"],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"outstanding balance", resp.data)
        # The blocked attempt must not have created a second sale for this customer.
        total_sales = db.query("SELECT COUNT(*) c FROM Sales WHERE CustomerID=?", (cust,), one=True)["c"]
        self.assertEqual(total_sales, 1)

    def test_admin_can_save_new_sale_for_overdue_customer_past_the_limit(self):
        prod = self.make_product()
        cust = self.make_customer("Overdue Customer Admin")
        self.make_sale(cust, 1000, 0, days_ago=45, invoice="INV-E2E-2")
        db.execute("UPDATE CompanySettings SET CreditControlMode='BlockAfterDays', CreditBlockDays=30 WHERE SettingsID=1")

        client = self.make_admin_client()
        resp = client.post("/sales/new", data={
            "customer_id": str(cust), "customer_name": "Overdue Customer Admin", "customer_zone": "",
            "sale_date": date.today().isoformat(), "status": "Completed", "payment_status": "Unpaid",
            "place_of_supply_code": "36", "cash_amount": "100", "bank_amount": "0",
            "product_id[]": [str(prod)], "qty[]": ["1"], "unit_price[]": ["100"],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        total_sales = db.query("SELECT COUNT(*) c FROM Sales WHERE CustomerID=?", (cust,), one=True)["c"]
        self.assertEqual(total_sales, 2)
