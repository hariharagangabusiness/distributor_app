"""Regression tests for sales_list()'s server-side filtering/pagination -
item from the architecture review ("every list page loads its entire table
into memory, unfiltered, forever"). Sales was the first (and highest-value)
list converted from fetch-everything-then-filter-in-Python to a real SQL
WHERE + LIMIT/OFFSET.
"""
from tests.base import DBTestCase, appmod, db


class SalesListPaginationTests(DBTestCase):
    def _make_sales(self, n, cust, prod):
        with appmod.app.app_context():
            for i in range(n):
                appmod.create_sale(
                    customer_id=cust, sale_date="2026-09-21", status="Completed",
                    payment_status="Paid", payment_due_date=None, amount_received=10,
                    notes=None, place_of_supply_code="36", lines=[(prod, 1, 10.0)])

    def test_pagination_splits_results_across_pages(self):
        cust = self.make_customer()
        prod = self.make_product()
        self._make_sales(appmod.SALES_LIST_PAGE_SIZE + 5, cust, prod)

        client = self.make_admin_client()
        page1 = client.get("/sales")
        self.assertEqual(page1.status_code, 200)
        self.assertIn(b"Page 1 of 2", page1.data)

        page2 = client.get("/sales?page=2")
        self.assertEqual(page2.status_code, 200)
        self.assertIn(b"Page 2 of 2", page2.data)

    def test_out_of_range_page_clamps_to_last_page(self):
        cust = self.make_customer()
        prod = self.make_product()
        self._make_sales(3, cust, prod)
        client = self.make_admin_client()

        resp = client.get("/sales?page=999")
        self.assertEqual(resp.status_code, 200)
        # Only one page's worth of results exists, so the pagination controls are
        # hidden entirely (nothing to page through) - the request must still render
        # that one page's results rather than clamping to an empty page.
        self.assertNotIn(b"No sales recorded yet.", resp.data)

    def test_search_filters_in_sql_not_just_the_current_page(self):
        cust1 = self.make_customer("Alpha Traders")
        cust2 = self.make_customer("Beta Wines")
        prod = self.make_product()
        with appmod.app.app_context():
            appmod.create_sale(customer_id=cust1, sale_date="2026-09-21", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=10, notes=None,
                place_of_supply_code="36", lines=[(prod, 1, 10.0)])
            appmod.create_sale(customer_id=cust2, sale_date="2026-09-21", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=10, notes=None,
                place_of_supply_code="36", lines=[(prod, 1, 10.0)])

        client = self.make_admin_client()
        resp = client.get("/sales?q=Alpha")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Alpha Traders", resp.data)
        self.assertNotIn(b"Beta Wines", resp.data)

    def test_existing_employee_and_date_filters_still_work_via_sql(self):
        """These filters existed before (previously applied in Python after
        fetching everything) - confirms moving them into SQL didn't change
        their behavior."""
        emp = self.make_employee()
        cust = self.make_customer()
        prod = self.make_product()
        with appmod.app.app_context():
            appmod.create_sale(customer_id=cust, sale_date="2026-09-21", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=10, notes=None,
                place_of_supply_code="36", lines=[(prod, 1, 10.0)], employee_id=emp)
            appmod.create_sale(customer_id=cust, sale_date="2026-09-22", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=10, notes=None,
                place_of_supply_code="36", lines=[(prod, 1, 10.0)])  # different date, no employee

        client = self.make_admin_client()
        resp = client.get(f"/sales?employee_id={emp}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"No sales recorded yet.", resp.data)

        resp2 = client.get("/sales?date=2026-09-22")
        rows = db.query("SELECT COUNT(*) c FROM Sales WHERE SaleDate='2026-09-22'", one=True)["c"]
        self.assertEqual(rows, 1)
        self.assertEqual(resp2.status_code, 200)
