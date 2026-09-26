"""Regression test for the Stock Issue view page: "Qty Sold" used to be
hidden entirely for an open (not-yet-reconciled) issue, even though
StockIssueLines.QtySold is already updated live as sales are credited
against it (see create_sale()'s open-issue crediting path) - so the column
existed and was accurate, it just wasn't shown until Status='Reconciled'.
"""
from tests.base import DBTestCase, appmod, db


class StockIssueViewQtySoldTests(DBTestCase):
    def test_open_issue_shows_qty_sold_so_far(self):
        emp = self.make_employee()
        cust = self.make_customer()
        prod = self.make_product()
        issue_id = self.make_open_stock_issue(emp, prod, "2026-09-21", qty_issued=50)

        with appmod.app.app_context():
            appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36",
                lines=[(prod, 20, 100.0)], employee_id=emp)

        client = self.make_admin_client()
        resp = client.get(f"/stock-issues/{issue_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Qty Sold", resp.data)
        self.assertIn(b"(so far)", resp.data)
        self.assertIn(b"20", resp.data)
        # Reconciliation-only columns must stay hidden on an open issue.
        # ("Scheme Claim ₹" is the table header - the sidebar nav has an
        # unrelated "Scheme Claims" link present on every page.)
        self.assertNotIn(b"Qty Returned", resp.data)
        self.assertNotIn("Scheme Claim ₹".encode(), resp.data)

    def test_open_issue_with_no_sales_shows_zero_not_blank(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_id = self.make_open_stock_issue(emp, prod, "2026-09-21", qty_issued=50)

        client = self.make_admin_client()
        resp = client.get(f"/stock-issues/{issue_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"None", resp.data)

    def test_reconciled_issue_still_shows_all_columns(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_id = self.make_reconciled_stock_issue(emp, prod, "2026-09-21", qty_issued=50, qty_sold=40)

        client = self.make_admin_client()
        resp = client.get(f"/stock-issues/{issue_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Qty Sold", resp.data)
        self.assertNotIn(b"(so far)", resp.data)
        self.assertIn(b"Qty Returned", resp.data)
        self.assertIn(b"Scheme Claim", resp.data)
        self.assertIn(b"40", resp.data)
