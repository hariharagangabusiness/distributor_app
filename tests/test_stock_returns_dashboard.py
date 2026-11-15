"""Regression tests for the Dashboard's Stock Return Variance widget: per-
salesperson, per-month units/value variance (the existing Unaccounted figure
from Reconciled Stock Issues: Qty Issued - Qty Sold - Qty Returned - Qty
Free), valued at Products.CostPrice, plus its drill-down API endpoint.
"""
from tests.base import DBTestCase, appmod, db


class GetStockReturnsSummaryTests(DBTestCase):
    def test_empty_when_no_reconciled_issues(self):
        with appmod.app.app_context():
            summary = appmod.get_stock_returns_summary(2026, 11)
        self.assertEqual(summary["rows"], [])
        self.assertEqual(summary["totals"]["units"], 0)
        self.assertEqual(summary["totals"]["value"], 0)

    def test_positive_variance_is_a_shortage_valued_at_cost_price(self):
        emp = self.make_employee()
        prod = self.make_product(cost_price=40.0, selling_price=100.0)
        # Issued 50, sold 30, returned 10, free 0 -> 10 short.
        self.make_reconciled_stock_issue(emp, prod, "2026-11-05", qty_issued=50, qty_sold=30)
        db.execute("UPDATE StockIssueLines SET QtyReturned=10 WHERE ProductID=?", (prod,))

        with appmod.app.app_context():
            summary = appmod.get_stock_returns_summary(2026, 11)
        self.assertEqual(len(summary["rows"]), 1)
        row = summary["rows"][0]
        self.assertEqual(row["EmployeeID"], emp)
        self.assertEqual(row["UnitsVariance"], 10)
        self.assertEqual(row["ValueVariance"], 400.0)  # 10 * cost price 40
        self.assertEqual(summary["totals"]["units"], 10)
        self.assertEqual(summary["totals"]["value"], 400.0)
        self.assertEqual(summary["totals"]["short_count"], 1)

    def test_fully_accounted_for_line_contributes_zero(self):
        emp = self.make_employee()
        prod = self.make_product(cost_price=40.0)
        self.make_reconciled_stock_issue(emp, prod, "2026-11-05", qty_issued=50, qty_sold=50)
        with appmod.app.app_context():
            summary = appmod.get_stock_returns_summary(2026, 11)
        self.assertEqual(summary["rows"][0]["UnitsVariance"], 0)
        self.assertEqual(summary["totals"]["short_count"], 0)

    def test_an_issued_but_not_yet_reconciled_issue_is_excluded(self):
        emp = self.make_employee()
        prod = self.make_product(cost_price=40.0)
        self.make_open_stock_issue(emp, prod, "2026-11-05", qty_issued=50)
        with appmod.app.app_context():
            summary = appmod.get_stock_returns_summary(2026, 11)
        self.assertEqual(summary["rows"], [])

    def test_a_different_months_issue_is_excluded(self):
        emp = self.make_employee()
        prod = self.make_product(cost_price=40.0)
        self.make_reconciled_stock_issue(emp, prod, "2026-10-05", qty_issued=50, qty_sold=30)
        with appmod.app.app_context():
            summary = appmod.get_stock_returns_summary(2026, 11)
        self.assertEqual(summary["rows"], [])
        with appmod.app.app_context():
            summary_oct = appmod.get_stock_returns_summary(2026, 10)
        self.assertEqual(len(summary_oct["rows"]), 1)


class GetStockReturnsDetailTests(DBTestCase):
    def test_returns_only_nonzero_variance_lines(self):
        emp = self.make_employee()
        prod_short = self.make_product(name="Short Product", cost_price=40.0)
        prod_balanced = self.make_product(name="Balanced Product", cost_price=20.0)
        issue_id = self.make_reconciled_stock_issue(emp, prod_short, "2026-11-05", qty_issued=50, qty_sold=30)
        db.execute("INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice, QtySold, QtyReturned, QtyFree) "
                   "VALUES (?,?,20,50,20,0,0)", (issue_id, prod_balanced))

        with appmod.app.app_context():
            detail = appmod.get_stock_returns_detail(emp, 2026, 11)
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]["product"], "Short Product")
        self.assertEqual(detail[0]["variance"], 20)
        self.assertEqual(detail[0]["value"], 800.0)  # 20 * 40 cost price


class ApiStockReturnsDetailRouteTests(DBTestCase):
    def test_admin_gets_json_detail(self):
        emp = self.make_employee("Route Rep")
        prod = self.make_product(cost_price=40.0)
        self.make_reconciled_stock_issue(emp, prod, "2026-11-05", qty_issued=50, qty_sold=30)
        client = self.make_admin_client()
        resp = client.get(f"/api/stock-returns/{emp}?year=2026&month=11")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["employee_name"], "Route Rep")
        self.assertEqual(len(data["lines"]), 1)
        self.assertEqual(data["lines"][0]["variance"], 20)

    def test_404_for_unknown_employee(self):
        client = self.make_admin_client()
        resp = client.get("/api/stock-returns/999999?year=2026&month=11")
        self.assertEqual(resp.status_code, 404)

    def test_non_admin_is_blocked(self):
        db.execute("INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES ('Staff', 'dashboard', 1)")
        emp = self.make_employee()
        client = self.make_role_client("Staff")
        resp = client.get(f"/api/stock-returns/{emp}?year=2026&month=11")
        self.assertNotEqual(resp.status_code, 200)


class DashboardStockReturnsWidgetTests(DBTestCase):
    def test_admin_sees_the_widget(self):
        emp = self.make_employee("Route Rep")
        prod = self.make_product(cost_price=40.0)
        self.make_reconciled_stock_issue(emp, prod, "2026-11-05", qty_issued=50, qty_sold=30)
        client = self.make_admin_client()
        resp = client.get("/dashboard?year=2026&month=11")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Stock Return Variance", resp.data)
        self.assertIn(b"Route Rep", resp.data)

    def test_non_admin_does_not_see_the_widget(self):
        db.execute("INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES ('Staff', 'dashboard', 1)")
        client = self.make_role_client("Staff")
        resp = client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        # The widget card itself (with its "Net this month" summary line) must not render for a
        # non-Admin - the inert, data-less modal skeleton and its JS (shared, only ever
        # populated by a click on an actual rendered row) staying in the page is fine, so this
        # checks the card's own content rather than generic strings also present in the
        # modal's static title/script, which always render regardless of role.
        self.assertNotIn(b"Net this month", resp.data)
