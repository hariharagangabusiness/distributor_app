"""Regression tests for Sales.CreatedAt: set once, automatically, in IST,
only on creation - never touched by a later edit, and never fabricated for
a Sale that predates this column (NULL, not a guessed value).
"""
from datetime import datetime

from tests.base import DBTestCase, appmod, db


class SalesCreatedAtTests(DBTestCase):
    def test_new_sale_gets_a_created_at_timestamp(self):
        cust = self.make_customer()
        prod = self.make_product()

        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-10-06", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36", lines=[(prod, 1, 100.0)])

        sale = db.query("SELECT CreatedAt FROM Sales WHERE SaleID=?", (sale_id,), one=True)
        self.assertIsNotNone(sale["CreatedAt"])
        # Stored as 'YYYY-MM-DD HH:MM:SS' - must parse cleanly as a real timestamp.
        datetime.strptime(sale["CreatedAt"], "%Y-%m-%d %H:%M:%S")

    def test_editing_a_sale_does_not_change_created_at(self):
        cust = self.make_customer()
        prod = self.make_product()

        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-10-06", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36", lines=[(prod, 1, 100.0)])
            original_created_at = db.query("SELECT CreatedAt FROM Sales WHERE SaleID=?", (sale_id,), one=True)["CreatedAt"]

            appmod.create_sale(
                customer_id=cust, sale_date="2026-10-06", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=150,
                notes="edited", place_of_supply_code="36", lines=[(prod, 1, 150.0)],
                invoice_no="INV-EDIT-TEST", sale_id=sale_id)

        after_edit = db.query("SELECT CreatedAt, Notes FROM Sales WHERE SaleID=?", (sale_id,), one=True)
        self.assertEqual(after_edit["CreatedAt"], original_created_at)
        self.assertEqual(after_edit["Notes"], "edited")

    def test_sale_view_shows_booked_time_when_present(self):
        cust = self.make_customer()
        prod = self.make_product()
        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-10-06", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36", lines=[(prod, 1, 100.0)])

        client = self.make_admin_client()
        resp = client.get(f"/sales/{sale_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"booked", resp.data)
        self.assertIn(b"IST", resp.data)

    def test_sale_view_hides_booked_time_when_absent(self):
        """A Sale that predates this column (CreatedAt NULL) must not show
        a fabricated booked-time line."""
        cust = self.make_customer()
        sale_id = db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status, TotalAmount,
                             AmountReceived, TaxableAmount) VALUES (?, ?, '2026-09-01', 'Completed', 100, 100, 100)""",
                             ("INV-NO-CREATEDAT", cust))
        client = self.make_admin_client()
        resp = client.get(f"/sales/{sale_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"booked", resp.data)
