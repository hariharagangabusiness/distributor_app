"""Regression tests for:
1. Unique, sequential Stock Issue numbers (IssueNumber, "SI/FY/0001") -
   next_stock_issue_number(), stock_issue_form()/auto_create_stock_issue_for_sale().
2. Direct Sale Review confirmation can assign the review to a specific Stock
   Issue (DirectSaleReviews.StockIssueID), and product_qty_sold_for_issue()
   uses that assignment to split Qty Sold correctly between sibling Stock
   Issues for the same employee/date - instead of true_product_qty_sold()'s
   blanket day-total, which double-counts whenever more than one Stock Issue
   exists for the same employee/date.
3. The pre-reconcile "Expected Return" column on the Stock Issue view page.
"""
from tests.base import DBTestCase, appmod, db


class NextStockIssueNumberTests(DBTestCase):
    def test_first_number_for_the_year(self):
        with appmod.app.app_context():
            n = appmod.next_stock_issue_number()
        fy = appmod.get_financial_year_label(__import__("datetime").date.today())
        self.assertEqual(n, f"SI/{fy}/0001")

    def test_increments_sequentially(self):
        with appmod.app.app_context():
            first = appmod.next_stock_issue_number()
            second = appmod.next_stock_issue_number()
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith("0002"))

    def test_respects_a_custom_prefix(self):
        db.execute("UPDATE CompanySettings SET StockIssuePrefix='VAN' WHERE SettingsID=1")
        with appmod.app.app_context():
            n = appmod.next_stock_issue_number()
        self.assertTrue(n.startswith("VAN/"))


class StockIssueFormAssignsNumberTests(DBTestCase):
    def test_new_stock_issue_gets_a_number(self):
        emp = self.make_employee()
        prod = self.make_product()
        client = self.make_admin_client()
        resp = client.post("/stock-issues/new", data={
            "employee_id": str(emp), "issue_date": "2026-11-01", "notes": "",
            "product_id[]": [str(prod)], "qty[]": ["10"], "unit_price[]": ["50"],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        issue = db.query("SELECT IssueNumber FROM StockIssues WHERE EmployeeID=?", (emp,), one=True)
        self.assertIsNotNone(issue["IssueNumber"])
        self.assertIn("SI/", issue["IssueNumber"])

    def test_two_issues_same_day_get_different_numbers(self):
        emp = self.make_employee()
        prod = self.make_product()
        client = self.make_admin_client()
        for _ in range(2):
            client.post("/stock-issues/new", data={
                "employee_id": str(emp), "issue_date": "2026-11-01", "notes": "",
                "product_id[]": [str(prod)], "qty[]": ["5"], "unit_price[]": ["50"],
            }, follow_redirects=True)
        numbers = [r["IssueNumber"] for r in db.query(
            "SELECT IssueNumber FROM StockIssues WHERE EmployeeID=? ORDER BY IssueID", (emp,))]
        self.assertEqual(len(numbers), 2)
        self.assertNotEqual(numbers[0], numbers[1])


class DirectSaleReviewAssignmentTests(DBTestCase):
    def make_direct_sale(self, employee_id, product_id, qty, sale_date="2026-11-01"):
        with appmod.app.app_context():
            return appmod.create_sale(
                customer_id=self.make_customer(), sale_date=sale_date, status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=qty * 100,
                notes=None, place_of_supply_code="36", lines=[(product_id, qty, 100.0)],
                employee_id=employee_id)

    def test_confirm_assigns_the_chosen_stock_issue(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_id = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                               "VALUES ('SI/TEST/0001', ?, '2026-11-01', 'Issued')", (emp,))
        self.make_direct_sale(emp, prod, 5)
        review = db.query("SELECT ReviewID FROM DirectSaleReviews", one=True)
        client = self.make_admin_client()

        resp = client.post(f"/inventory/direct-sale-review/{review['ReviewID']}/mark", data={
            "status": "Confirmed", "stock_issue_id": str(issue_id), "return_status": "Pending",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT Status, StockIssueID FROM DirectSaleReviews WHERE ReviewID=?",
                        (review["ReviewID"],), one=True)
        self.assertEqual(row["Status"], "Confirmed")
        self.assertEqual(row["StockIssueID"], issue_id)

    def test_confirm_with_blank_stock_issue_id_leaves_it_unassigned(self):
        emp = self.make_employee()
        prod = self.make_product()
        self.make_direct_sale(emp, prod, 5)
        review = db.query("SELECT ReviewID FROM DirectSaleReviews", one=True)
        client = self.make_admin_client()

        resp = client.post(f"/inventory/direct-sale-review/{review['ReviewID']}/mark", data={
            "status": "Confirmed", "stock_issue_id": "", "return_status": "Pending",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT StockIssueID FROM DirectSaleReviews WHERE ReviewID=?",
                        (review["ReviewID"],), one=True)
        self.assertIsNone(row["StockIssueID"])

    def test_cannot_assign_a_stock_issue_belonging_to_a_different_employee(self):
        emp_a = self.make_employee("A")
        emp_b = self.make_employee("B")
        prod = self.make_product()
        other_issue_id = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                                     "VALUES ('SI/TEST/0002', ?, '2026-11-01', 'Issued')", (emp_b,))
        self.make_direct_sale(emp_a, prod, 5)
        review = db.query("SELECT ReviewID FROM DirectSaleReviews", one=True)
        client = self.make_admin_client()

        resp = client.post(f"/inventory/direct-sale-review/{review['ReviewID']}/mark", data={
            "status": "Confirmed", "stock_issue_id": str(other_issue_id), "return_status": "Pending",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"doesn", resp.data)  # "doesn't belong to this sale's salesperson"
        row = db.query("SELECT StockIssueID FROM DirectSaleReviews WHERE ReviewID=?",
                        (review["ReviewID"],), one=True)
        self.assertIsNone(row["StockIssueID"])

    def test_flag_and_reopen_never_touch_an_existing_assignment(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_id = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                               "VALUES ('SI/TEST/0003', ?, '2026-11-01', 'Issued')", (emp,))
        self.make_direct_sale(emp, prod, 5)
        review = db.query("SELECT ReviewID FROM DirectSaleReviews", one=True)
        client = self.make_admin_client()
        client.post(f"/inventory/direct-sale-review/{review['ReviewID']}/mark", data={
            "status": "Confirmed", "stock_issue_id": str(issue_id), "return_status": "Pending",
        }, follow_redirects=True)

        client.post(f"/inventory/direct-sale-review/{review['ReviewID']}/mark", data={
            "status": "Flagged", "return_status": "Pending",
        }, follow_redirects=True)
        row = db.query("SELECT Status, StockIssueID FROM DirectSaleReviews WHERE ReviewID=?",
                        (review["ReviewID"],), one=True)
        self.assertEqual(row["Status"], "Flagged")
        self.assertEqual(row["StockIssueID"], issue_id)

        client.post(f"/inventory/direct-sale-review/{review['ReviewID']}/mark", data={
            "status": "Pending", "return_status": "Flagged",
        }, follow_redirects=True)
        row = db.query("SELECT StockIssueID FROM DirectSaleReviews WHERE ReviewID=?",
                        (review["ReviewID"],), one=True)
        self.assertEqual(row["StockIssueID"], issue_id)


class DirectSaleReviewListCandidatesTests(DBTestCase):
    def test_lists_a_multi_candidate_assign_modal_when_two_issues_exist_same_day(self):
        emp = self.make_employee()
        prod = self.make_product()
        db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                   "VALUES ('SI/TEST/0010', ?, '2026-11-01', 'Issued')", (emp,))
        db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                   "VALUES ('SI/TEST/0011', ?, '2026-11-01', 'Issued')", (emp,))
        with appmod.app.app_context():
            appmod.create_sale(customer_id=self.make_customer(), sale_date="2026-11-01", status="Completed",
                                payment_status="Paid", payment_due_date=None, amount_received=500,
                                notes=None, place_of_supply_code="36", lines=[(prod, 5, 100.0)],
                                employee_id=emp)
        client = self.make_admin_client()
        resp = client.get("/inventory/direct-sale-review?status=Pending")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"SI/TEST/0010", resp.data)
        self.assertIn(b"SI/TEST/0011", resp.data)
        self.assertIn(b"assign-modal-", resp.data)

    def test_single_candidate_shows_plain_confirm_with_auto_assignment(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_id = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                               "VALUES ('SI/TEST/0020', ?, '2026-11-01', 'Issued')", (emp,))
        with appmod.app.app_context():
            appmod.create_sale(customer_id=self.make_customer(), sale_date="2026-11-01", status="Completed",
                                payment_status="Paid", payment_due_date=None, amount_received=500,
                                notes=None, place_of_supply_code="36", lines=[(prod, 5, 100.0)],
                                employee_id=emp)
        client = self.make_admin_client()
        resp = client.get("/inventory/direct-sale-review?status=Pending")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"assign-modal-", resp.data)
        self.assertIn(f'value="{issue_id}"'.encode(), resp.data)


class ProductQtySoldForIssueTests(DBTestCase):
    def test_matches_true_product_qty_sold_when_only_one_issue_exists(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_id = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                               "VALUES ('SI/TEST/0030', ?, '2026-11-01', 'Issued')", (emp,))
        db.execute("INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice) VALUES (?,?,10,100)",
                   (issue_id, prod))
        with appmod.app.app_context():
            appmod.create_sale(customer_id=self.make_customer(), sale_date="2026-11-01", status="Completed",
                                payment_status="Paid", payment_due_date=None, amount_received=300,
                                notes=None, place_of_supply_code="36", lines=[(prod, 3, 100.0)],
                                employee_id=emp)
            true_total = appmod.true_product_qty_sold(emp, "2026-11-01", prod)
            scoped = appmod.product_qty_sold_for_issue(issue_id, emp, "2026-11-01", prod)
        self.assertEqual(scoped, true_total)
        self.assertEqual(scoped, 3)

    def test_splits_correctly_between_two_sibling_issues(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_a = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                              "VALUES ('SI/TEST/0040', ?, '2026-11-01', 'Issued')", (emp,))
        db.execute("INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice) VALUES (?,?,5,100)",
                   (issue_a, prod))
        issue_b = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                              "VALUES ('SI/TEST/0041', ?, '2026-11-01', 'Issued')", (emp,))
        db.execute("INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice) VALUES (?,?,5,100)",
                   (issue_b, prod))

        # Sale of 8: find_open_stock_issue() picks the most-recently-created open issue (B) and
        # live-credits up to its capacity (5); the remaining 3 fall through to a direct
        # deduction, landing in DirectSaleReviews.
        with appmod.app.app_context():
            appmod.create_sale(customer_id=self.make_customer(), sale_date="2026-11-01", status="Completed",
                                payment_status="Paid", payment_due_date=None, amount_received=800,
                                notes=None, place_of_supply_code="36", lines=[(prod, 8, 100.0)],
                                employee_id=emp)
        review = db.query("SELECT ReviewID FROM DirectSaleReviews", one=True)
        self.assertIsNotNone(review, "the 3-unit overflow should have fallen through to a Direct Sale Review")

        # Confirm it, explicitly assigning the overflow to issue A (not B, which already got
        # its share via the live credit).
        client = self.make_admin_client()
        client.post(f"/inventory/direct-sale-review/{review['ReviewID']}/mark", data={
            "status": "Confirmed", "stock_issue_id": str(issue_a), "return_status": "Pending",
        }, follow_redirects=True)

        with appmod.app.app_context():
            true_total = appmod.true_product_qty_sold(emp, "2026-11-01", prod)
            qty_a = appmod.product_qty_sold_for_issue(issue_a, emp, "2026-11-01", prod)
            qty_b = appmod.product_qty_sold_for_issue(issue_b, emp, "2026-11-01", prod)

        self.assertEqual(true_total, 8, "the blanket day-total is the same ambiguous 8 either way")
        self.assertEqual(qty_a, 3, "issue A gets only the explicitly-assigned direct-sale overflow")
        self.assertEqual(qty_b, 5, "issue B gets only its own live-credited portion")
        self.assertEqual(qty_a + qty_b, true_total, "the split must still add up to the real total")

    def test_unassigned_direct_sale_counts_toward_neither_sibling(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_a = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                              "VALUES ('SI/TEST/0050', ?, '2026-11-01', 'Issued')", (emp,))
        db.execute("INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice) VALUES (?,?,5,100)",
                   (issue_a, prod))
        issue_b = db.execute("INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status) "
                              "VALUES ('SI/TEST/0051', ?, '2026-11-01', 'Issued')", (emp,))
        db.execute("INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice) VALUES (?,?,5,100)",
                   (issue_b, prod))
        with appmod.app.app_context():
            appmod.create_sale(customer_id=self.make_customer(), sale_date="2026-11-01", status="Completed",
                                payment_status="Paid", payment_due_date=None, amount_received=800,
                                notes=None, place_of_supply_code="36", lines=[(prod, 8, 100.0)],
                                employee_id=emp)
            # Left Pending - never confirmed/assigned.
            qty_a = appmod.product_qty_sold_for_issue(issue_a, emp, "2026-11-01", prod)
            qty_b = appmod.product_qty_sold_for_issue(issue_b, emp, "2026-11-01", prod)
        self.assertEqual(qty_a, 0)
        self.assertEqual(qty_b, 5)  # only B's own live credit counts


class StockIssueViewExpectedReturnTests(DBTestCase):
    def test_shows_expected_return_before_reconcile(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_id = self.make_open_stock_issue(emp, prod, "2026-11-01", qty_issued=50)
        with appmod.app.app_context():
            appmod.create_sale(customer_id=self.make_customer(), sale_date="2026-11-01", status="Completed",
                                payment_status="Paid", payment_due_date=None, amount_received=2000,
                                notes=None, place_of_supply_code="36", lines=[(prod, 20, 100.0)],
                                employee_id=emp)
        client = self.make_admin_client()
        resp = client.get(f"/stock-issues/{issue_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Expected Return", resp.data)
        self.assertIn(b"30.0", resp.data)  # 50 issued - 20 sold

    def test_reconciled_issue_does_not_show_expected_return(self):
        emp = self.make_employee()
        prod = self.make_product()
        issue_id = self.make_reconciled_stock_issue(emp, prod, "2026-11-01", qty_issued=50, qty_sold=40)
        client = self.make_admin_client()
        resp = client.get(f"/stock-issues/{issue_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Expected Return", resp.data)
