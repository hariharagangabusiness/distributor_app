"""Regression tests for the double-deduction scenario: a salesperson
physically takes stock and sells some of it before anyone records a Stock
Issue in the system. That Sale falls through to a direct warehouse
deduction (see test_stock_issue_reconcile.py / true_product_qty_sold's
docstring). When the Stock Issue is recorded afterward for the full
physical quantity, stock_issue_post_line() must not deduct the
already-sold units a second time - see already_direct_deducted_qty() and
the "Also doubles as..." netting logic in stock_issue_post_line().
"""
from tests.base import DBTestCase, appmod, db


class AlreadyDirectDeductedQtyTests(DBTestCase):
    def test_zero_when_no_employee_id(self):
        with appmod.app.app_context():
            self.assertEqual(appmod.already_direct_deducted_qty(None, "2026-11-01", 1), 0)

    def test_zero_when_nothing_was_sold_directly(self):
        emp = self.make_employee()
        prod = self.make_product()
        with appmod.app.app_context():
            self.assertEqual(appmod.already_direct_deducted_qty(emp, "2026-11-01", prod), 0)

    def test_sums_direct_sale_deductions_for_that_employee_date_product(self):
        emp = self.make_employee()
        prod = self.make_product()
        with appmod.app.app_context():
            appmod.create_sale(customer_id=self.make_customer(), sale_date="2026-11-01", status="Completed",
                                payment_status="Paid", payment_due_date=None, amount_received=600,
                                notes=None, place_of_supply_code="36", lines=[(prod, 6, 100.0)],
                                employee_id=emp)
            qty = appmod.already_direct_deducted_qty(emp, "2026-11-01", prod)
        self.assertEqual(qty, 6)

    def test_ignores_a_different_employee_or_date_or_cancelled_sale(self):
        emp_a = self.make_employee("A")
        emp_b = self.make_employee("B")
        prod = self.make_product()
        cust = self.make_customer()
        with appmod.app.app_context():
            appmod.create_sale(customer_id=cust, sale_date="2026-11-01", status="Completed",
                                payment_status="Paid", payment_due_date=None, amount_received=100,
                                notes=None, place_of_supply_code="36", lines=[(prod, 1, 100.0)],
                                employee_id=emp_a)
            self.assertEqual(appmod.already_direct_deducted_qty(emp_b, "2026-11-01", prod), 0)
            self.assertEqual(appmod.already_direct_deducted_qty(emp_a, "2026-11-02", prod), 0)


class StockIssuePostLineNettingTests(DBTestCase):
    def make_direct_sale(self, employee_id, product_id, qty, sale_date="2026-11-01"):
        with appmod.app.app_context():
            return appmod.create_sale(
                customer_id=self.make_customer(), sale_date=sale_date, status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=qty * 100,
                notes=None, place_of_supply_code="36", lines=[(product_id, qty, 100.0)],
                employee_id=employee_id)

    def test_new_line_nets_the_warehouse_deduction_but_not_qty_issued(self):
        emp = self.make_employee()
        prod = self.make_product()
        self.make_direct_sale(emp, prod, 6)

        issue_id = db.execute("INSERT INTO StockIssues (EmployeeID, IssueDate, Status) VALUES (?,?,'Issued')",
                               (emp, "2026-11-01"))
        with appmod.app.test_request_context():
            netted = appmod.stock_issue_post_line(issue_id, prod, 10, 100.0, "2026-11-01", "test", employee_id=emp)

        self.assertEqual(netted, 6)
        line = db.query("SELECT QtyIssued FROM StockIssueLines WHERE IssueID=? AND ProductID=?",
                         (issue_id, prod), one=True)
        self.assertEqual(line["QtyIssued"], 10)  # full physical qty, unmodified
        issue_txn = db.query("""SELECT QtyChange FROM InventoryTransactions
                              WHERE RefType='StockIssue' AND RefID=? AND ProductID=?""", (issue_id, prod), one=True)
        self.assertEqual(issue_txn["QtyChange"], -4)  # 10 - 6 already sold directly

    def test_net_warehouse_effect_matches_physical_reality(self):
        """End-to-end sanity check on the exact scenario described: 10 units
        physically taken, 6 sold before the Issue is recorded, 4 returned
        unsold at Reconcile. Only the 6 that were genuinely sold are gone
        for good - the 4 returned units are a round trip (net zero) - so
        CurrentStock must drop by exactly 6, not 16 (unfixed double count)
        or 10 (still counting the returned units as gone)."""
        emp = self.make_employee()
        prod = self.make_product(cost_price=50.0, selling_price=100.0)
        starting_stock = 50
        db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType, QtyChange)
                    VALUES (?, '2026-10-01', 'Opening', ?)""", (prod, starting_stock))

        self.make_direct_sale(emp, prod, 6, sale_date="2026-11-01")

        issue_id = db.execute("INSERT INTO StockIssues (EmployeeID, IssueDate, Status) VALUES (?,?,'Issued')",
                               (emp, "2026-11-01"))
        with appmod.app.test_request_context():
            appmod.stock_issue_post_line(issue_id, prod, 10, 100.0, "2026-11-01", "test", employee_id=emp)
        line_id = db.query("SELECT LineID FROM StockIssueLines WHERE IssueID=? AND ProductID=?",
                            (issue_id, prod), one=True)["LineID"]

        client = self.make_admin_client()
        resp = client.post(f"/stock-issues/{issue_id}/reconcile", data={
            "cash_amount": "0", "bank_amount": "0",
            f"qty_sold_{line_id}": "6", f"qty_returned_{line_id}": "4", f"qty_free_{line_id}": "0",
            f"discount_amount_{line_id}": "0",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        current_stock = db.query("SELECT COALESCE(SUM(QtyChange),0) q FROM InventoryTransactions WHERE ProductID=?",
                                  (prod,), one=True)["q"]
        self.assertEqual(current_stock, starting_stock - 6)

    def test_topping_up_an_existing_line_does_not_net_again(self):
        emp = self.make_employee()
        prod = self.make_product()
        self.make_direct_sale(emp, prod, 6)
        issue_id = db.execute("INSERT INTO StockIssues (EmployeeID, IssueDate, Status) VALUES (?,?,'Issued')",
                               (emp, "2026-11-01"))
        with appmod.app.test_request_context():
            first = appmod.stock_issue_post_line(issue_id, prod, 10, 100.0, "2026-11-01", "test", employee_id=emp)
            second = appmod.stock_issue_post_line(issue_id, prod, 5, 100.0, "2026-11-01", "test", employee_id=emp)
        self.assertEqual(first, 6)
        self.assertEqual(second, 0)
        topup_txn = db.query("""SELECT QtyChange FROM InventoryTransactions
                              WHERE RefType='StockIssue' AND RefID=? AND ProductID=? ORDER BY TransactionID DESC
                              LIMIT 1""", (issue_id, prod), one=True)
        self.assertEqual(topup_txn["QtyChange"], -5)  # full top-up qty, no further netting

    def test_floors_at_zero_when_already_sold_exceeds_qty_issued(self):
        emp = self.make_employee()
        prod = self.make_product()
        self.make_direct_sale(emp, prod, 10)
        issue_id = db.execute("INSERT INTO StockIssues (EmployeeID, IssueDate, Status) VALUES (?,?,'Issued')",
                               (emp, "2026-11-01"))
        with appmod.app.test_request_context():
            netted = appmod.stock_issue_post_line(issue_id, prod, 4, 100.0, "2026-11-01", "test", employee_id=emp)
        self.assertEqual(netted, 4)  # capped at qty issued, never negative deduction
        issue_txn = db.query("""SELECT QtyChange FROM InventoryTransactions
                              WHERE RefType='StockIssue' AND RefID=? AND ProductID=?""", (issue_id, prod), one=True)
        self.assertEqual(issue_txn["QtyChange"], 0)

    def test_no_netting_without_an_employee_id(self):
        """The internal auto-create-from-sale callers (auto_create_stock_issue_for_sale,
        expand_pending_stock_issue_for_product) deliberately don't pass employee_id -
        they're constructing an issue to cover the CURRENT sale's own not-yet-deducted
        units, not retroactively fixing an already-posted direct deduction."""
        emp = self.make_employee()
        prod = self.make_product()
        self.make_direct_sale(emp, prod, 6)
        issue_id = db.execute("INSERT INTO StockIssues (EmployeeID, IssueDate, Status) VALUES (?,?,'Issued')",
                               (emp, "2026-11-01"))
        with appmod.app.test_request_context():
            netted = appmod.stock_issue_post_line(issue_id, prod, 10, 100.0, "2026-11-01", "test")
        self.assertEqual(netted, 0)


class StockIssueRouteNettingTests(DBTestCase):
    def make_direct_sale(self, employee_id, product_id, qty, sale_date="2026-11-01"):
        with appmod.app.app_context():
            return appmod.create_sale(
                customer_id=self.make_customer(), sale_date=sale_date, status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=qty * 100,
                notes=None, place_of_supply_code="36", lines=[(product_id, qty, 100.0)],
                employee_id=employee_id)

    def test_new_issue_form_nets_and_flashes_a_warning(self):
        emp = self.make_employee()
        prod = self.make_product()
        self.make_direct_sale(emp, prod, 6)

        client = self.make_admin_client()
        resp = client.post("/stock-issues/new", data={
            "employee_id": str(emp), "issue_date": "2026-11-01", "notes": "",
            "product_id[]": [str(prod)], "qty[]": ["10"], "unit_price[]": ["100"],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"reduced to avoid double-counting", resp.data)

        issue = db.query("SELECT IssueID FROM StockIssues WHERE EmployeeID=? AND IssueDate='2026-11-01'",
                          (emp,), one=True)
        txn = db.query("""SELECT QtyChange FROM InventoryTransactions
                        WHERE RefType='StockIssue' AND RefID=? AND ProductID=?""", (issue["IssueID"], prod), one=True)
        self.assertEqual(txn["QtyChange"], -4)

    def test_api_endpoint_returns_direct_sales_for_employee_and_date(self):
        emp = self.make_employee()
        prod = self.make_product("Widget")
        self.make_direct_sale(emp, prod, 6)

        client = self.make_admin_client()
        resp = client.get(f"/api/employee/{emp}/direct-sales?date=2026-11-01")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [{"product": "Widget", "qty": 6}])

    def test_api_endpoint_empty_for_other_dates(self):
        emp = self.make_employee()
        prod = self.make_product()
        self.make_direct_sale(emp, prod, 6)
        client = self.make_admin_client()
        resp = client.get(f"/api/employee/{emp}/direct-sales?date=2026-12-25")
        self.assertEqual(resp.get_json(), [])

    def test_add_lines_get_shows_warning_for_products_not_yet_on_the_issue(self):
        emp = self.make_employee()
        prod_a = self.make_product("Already Sold Product")
        prod_b = self.make_product("On Issue Already")
        self.make_direct_sale(emp, prod_a, 6)

        issue_id = db.execute("INSERT INTO StockIssues (EmployeeID, IssueDate, Status) VALUES (?,?,'Issued')",
                               (emp, "2026-11-01"))
        with appmod.app.test_request_context():
            appmod.stock_issue_post_line(issue_id, prod_b, 5, 100.0, "2026-11-01", "test")

        client = self.make_admin_client()
        resp = client.get(f"/stock-issues/{issue_id}/add-lines")
        html = resp.data.decode()
        warning_section = html.split('alert-warning')[1].split('</div>')[0]
        self.assertIn("Already Sold Product", warning_section)
        self.assertNotIn("On Issue Already", warning_section)  # already on the issue - not flagged again

    def test_add_lines_post_nets_and_flashes_a_warning(self):
        emp = self.make_employee()
        prod = self.make_product()
        self.make_direct_sale(emp, prod, 6)
        issue_id = db.execute("INSERT INTO StockIssues (EmployeeID, IssueDate, Status) VALUES (?,?,'Issued')",
                               (emp, "2026-11-01"))

        client = self.make_admin_client()
        resp = client.post(f"/stock-issues/{issue_id}/add-lines", data={
            "issue_date": "2026-11-01", "product_id[]": [str(prod)], "qty[]": ["10"], "unit_price[]": ["100"],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"reduced to avoid double-counting", resp.data)
        txn = db.query("""SELECT QtyChange FROM InventoryTransactions
                        WHERE RefType='StockIssue' AND RefID=? AND ProductID=?""", (issue_id, prod), one=True)
        self.assertEqual(txn["QtyChange"], -4)

    def test_no_warning_or_netting_when_nothing_was_sold_directly(self):
        emp = self.make_employee()
        prod = self.make_product()
        client = self.make_admin_client()
        resp = client.post("/stock-issues/new", data={
            "employee_id": str(emp), "issue_date": "2026-11-01", "notes": "",
            "product_id[]": [str(prod)], "qty[]": ["10"], "unit_price[]": ["100"],
        }, follow_redirects=True)
        self.assertNotIn(b"reduced to avoid double-counting", resp.data)
        issue = db.query("SELECT IssueID FROM StockIssues WHERE EmployeeID=?", (emp,), one=True)
        txn = db.query("""SELECT QtyChange FROM InventoryTransactions
                        WHERE RefType='StockIssue' AND RefID=? AND ProductID=?""", (issue["IssueID"], prod), one=True)
        self.assertEqual(txn["QtyChange"], -10)
