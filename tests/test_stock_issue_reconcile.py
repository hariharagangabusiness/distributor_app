"""Regression tests for the Stock Issue reconcile guardrail: re-editing an
already-Reconciled issue must require a reason and be fully audited, and
editing a Sale entangled with one must be blocked. See the PR that
introduced these ("Guard reconciled Stock Issues against silent
double-counting") for the incident that prompted them.
"""
from tests.base import DBTestCase, appmod, db


class ReconcileGuardrailTests(DBTestCase):
    def test_fresh_reconciliation_succeeds_and_is_logged(self):
        emp = self.make_employee()
        prod = self.make_product()
        client = self.make_admin_client()
        issue_id = self.make_open_stock_issue(emp, prod, "2026-09-22", qty_issued=50)
        line = db.query("SELECT LineID FROM StockIssueLines WHERE IssueID=?", (issue_id,), one=True)

        resp = client.post(f"/stock-issues/{issue_id}/reconcile", data={
            f"qty_sold_{line['LineID']}": "30", f"qty_returned_{line['LineID']}": "20",
            f"qty_free_{line['LineID']}": "0", f"discount_amount_{line['LineID']}": "0",
            f"scheme_claim_{line['LineID']}": "0", f"comments_{line['LineID']}": "",
            "cash_amount": "3000", "bank_amount": "0", "notes": "",
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        issue = db.query("SELECT Status FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
        self.assertEqual(issue["Status"], "Reconciled")
        actions = [r["Action"] for r in db.query(
            "SELECT Action FROM StockIssueAuditLog WHERE IssueID=?", (issue_id,))]
        self.assertIn("Reconciled", actions)

    def test_reedit_without_reason_is_blocked_and_changes_nothing(self):
        emp = self.make_employee()
        prod = self.make_product()
        client = self.make_admin_client()
        issue_id = self.make_reconciled_stock_issue(emp, prod, "2026-09-21", qty_issued=140, qty_sold=40)
        line = db.query("SELECT LineID, QtySold FROM StockIssueLines WHERE IssueID=?", (issue_id,), one=True)

        resp = client.post(f"/stock-issues/{issue_id}/reconcile", data={
            f"qty_sold_{line['LineID']}": "140", f"qty_returned_{line['LineID']}": "0",
            f"qty_free_{line['LineID']}": "0", f"discount_amount_{line['LineID']}": "0",
            f"scheme_claim_{line['LineID']}": "0", f"comments_{line['LineID']}": "",
            "cash_amount": "800", "bank_amount": "0", "notes": "",
            # no reopen_reason
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"reason is required", resp.data)
        after = db.query("SELECT QtySold FROM StockIssueLines WHERE LineID=?", (line["LineID"],), one=True)
        self.assertEqual(after["QtySold"], line["QtySold"], "nothing should change without a reason")

    def test_reedit_with_reason_applies_and_logs_the_diff(self):
        emp = self.make_employee()
        prod = self.make_product()
        client = self.make_admin_client()
        issue_id = self.make_reconciled_stock_issue(emp, prod, "2026-09-21", qty_issued=140, qty_sold=40)
        line = db.query("SELECT LineID FROM StockIssueLines WHERE IssueID=?", (issue_id,), one=True)

        resp = client.post(f"/stock-issues/{issue_id}/reconcile", data={
            f"qty_sold_{line['LineID']}": "45", f"qty_returned_{line['LineID']}": "0",
            f"qty_free_{line['LineID']}": "0", f"discount_amount_{line['LineID']}": "0",
            f"scheme_claim_{line['LineID']}": "0", f"comments_{line['LineID']}": "",
            "cash_amount": "900", "bank_amount": "0", "notes": "",
            "reopen_reason": "Original count missed 5 units sold late in the day.",
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        after = db.query("SELECT QtySold FROM StockIssueLines WHERE LineID=?", (line["LineID"],), one=True)
        self.assertEqual(after["QtySold"], 45)
        actions = [r["Action"] for r in db.query(
            "SELECT Action FROM StockIssueAuditLog WHERE IssueID=? ORDER BY LogID", (issue_id,))]
        self.assertEqual(actions, ["Reopened & Re-Reconciled", "Line Corrected", "Re-Reconciled"])

    def test_editing_entangled_sale_is_blocked_and_logged(self):
        emp = self.make_employee()
        cust = self.make_customer()
        prod = self.make_product()
        client = self.make_admin_client()
        issue_id = self.make_reconciled_stock_issue(emp, prod, "2026-09-21", qty_issued=140, qty_sold=40)

        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Unpaid",
                payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                lines=[(prod, 100, 100.0)], employee_id=emp)

        resp = client.post(f"/sales/{sale_id}/edit", data={
            "product_id[]": [str(prod)], "qty[]": ["100"], "unit_price[]": ["100"],
            "discount_amount[]": ["0"], "sale_date": "2026-09-21", "status": "Completed",
            "payment_status": "Paid", "cash_amount": "500", "bank_amount": "0",
            "customer_id": str(cust), "employee_id": str(emp), "place_of_supply_code": "36",
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        after = db.query("SELECT AmountReceived FROM Sales WHERE SaleID=?", (sale_id,), one=True)
        self.assertEqual(after["AmountReceived"], 0, "the entangled edit must not have been applied")
        blocked = [r["Action"] for r in db.query(
            "SELECT Action FROM StockIssueAuditLog WHERE IssueID=?", (issue_id,))]
        self.assertIn("Blocked Edit Attempt", blocked)

    def test_editing_unrelated_sale_still_works(self):
        """Sanity check: the entanglement guard must not block ordinary
        edits to sales that have nothing to do with a reconciled issue."""
        cust = self.make_customer()
        prod = self.make_product()
        client = self.make_admin_client()

        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Unpaid",
                payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                lines=[(prod, 5, 100.0)])  # no employee_id -> never entangled

        resp = client.post(f"/sales/{sale_id}/edit", data={
            "product_id[]": [str(prod)], "qty[]": ["5"], "unit_price[]": ["100"],
            "discount_amount[]": ["0"], "sale_date": "2026-09-21", "status": "Completed",
            "payment_status": "Paid", "cash_amount": "500", "bank_amount": "0",
            "customer_id": str(cust), "employee_id": "", "place_of_supply_code": "36",
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        after = db.query("SELECT AmountReceived, PaymentStatus FROM Sales WHERE SaleID=?", (sale_id,), one=True)
        self.assertEqual(after["AmountReceived"], 500)
        self.assertEqual(after["PaymentStatus"], "Paid")
