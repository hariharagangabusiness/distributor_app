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


class SaleIssueLockingTests(DBTestCase):
    """A Sale credited against (or dated within) a Reconciled Stock Issue is part of that closed book:
    it can't be rewritten, cancelled or deleted directly, only have its Notes/Due Date changed."""

    def _entangled_sale(self):
        emp, cust, prod = self.make_employee(), self.make_customer(), self.make_product()
        issue_id = self.make_open_stock_issue(emp, prod, "2026-09-21", qty_issued=100)
        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Unpaid",
                payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                lines=[(prod, 10, 100.0)], employee_id=emp)
        line = db.query("SELECT LineID FROM StockIssueLines WHERE IssueID=?", (issue_id,), one=True)
        client = self.make_admin_client()
        client.post(f"/stock-issues/{issue_id}/reconcile", data={
            f"qty_sold_{line['LineID']}": "10", f"qty_returned_{line['LineID']}": "90",
            f"qty_free_{line['LineID']}": "0", f"discount_amount_{line['LineID']}": "0",
            f"scheme_claim_{line['LineID']}": "0", f"comments_{line['LineID']}": "",
            "cash_amount": "0", "bank_amount": "0", "notes": ""})
        self.assertEqual(db.query("SELECT Status FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)["Status"],
                         "Reconciled")
        self.ctx = dict(emp=emp, cust=cust, prod=prod, issue_id=issue_id, sale_id=sale_id, line_id=line["LineID"])
        return client

    def _edit_data(self, **over):
        c = self.ctx
        data = {"customer_id": c["cust"], "sale_date": "2026-09-21", "status": "Completed", "payment_status": "Unpaid",
                "product_id[]": [c["prod"]], "qty[]": ["10"], "unit_price[]": ["100"], "discount_amount[]": ["0"],
                "employee_id": c["emp"], "cash_amount": "0", "bank_amount": "0", "notes": "", "payment_due_date": "",
                "place_of_supply_code": "36"}
        data.update(over)
        return data

    def _qty_sold(self):
        return db.query("SELECT QtySold FROM StockIssueLines WHERE LineID=?", (self.ctx["line_id"],), one=True)["QtySold"]

    def test_cancelling_a_completed_entangled_sale_raises_and_leaves_qty_sold(self):
        self._entangled_sale()
        c = self.ctx
        invoice = db.query("SELECT InvoiceNumber FROM Sales WHERE SaleID=?", (c["sale_id"],), one=True)["InvoiceNumber"]
        with appmod.app.app_context():
            with self.assertRaises(appmod.SaleStockIssueLockedError) as raised:
                appmod.create_sale(
                    customer_id=c["cust"], sale_date="2026-09-21", status="Cancelled", payment_status="Unpaid",
                    payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                    lines=[(c["prod"], 10, 100.0)], employee_id=c["emp"], sale_id=c["sale_id"], invoice_no=invoice)
        self.assertEqual(raised.exception.issue_id, c["issue_id"])
        self.assertEqual(self._qty_sold(), 10)

    def test_cancelling_via_the_edit_route_is_blocked(self):
        client = self._entangled_sale()
        resp = client.post(f"/sales/{self.ctx['sale_id']}/edit", data=self._edit_data(status="Cancelled"),
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Nothing was saved", resp.data)
        self.assertEqual(self._qty_sold(), 10)
        status = db.query("SELECT Status FROM Sales WHERE SaleID=?", (self.ctx["sale_id"],), one=True)["Status"]
        self.assertEqual(status, "Completed")

    def test_deleting_an_entangled_sale_is_blocked_with_a_flash(self):
        client = self._entangled_sale()
        sid = self.ctx["sale_id"]
        resp = client.post(f"/sales/{sid}/delete", data={"delete_reason": "oops"}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Reopen &amp; Correct", resp.data)
        self.assertEqual(db.query("SELECT COUNT(*) c FROM Sales WHERE SaleID=?", (sid,), one=True)["c"], 1)
        self.assertEqual(self._qty_sold(), 10)
        actions = [r["Action"] for r in db.query("SELECT Action FROM StockIssueAuditLog WHERE IssueID=?",
                                                  (self.ctx["issue_id"],))]
        self.assertIn("Blocked Delete Attempt", actions)

    def test_header_only_edit_succeeds_but_quantity_or_status_edits_do_not(self):
        client = self._entangled_sale()
        sid = self.ctx["sale_id"]
        resp = client.post(f"/sales/{sid}/edit", data=self._edit_data(notes="delivered to gate 2",
                           payment_due_date="2026-10-15"), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        sale = db.query("SELECT Notes, PaymentDueDate FROM Sales WHERE SaleID=?", (sid,), one=True)
        self.assertEqual((sale["Notes"], sale["PaymentDueDate"]), ("delivered to gate 2", "2026-10-15"))
        self.assertEqual(self._qty_sold(), 10)

        for override in ({"qty[]": ["12"]}, {"status": "Draft"}, {"discount_amount[]": ["5"]}, {"sale_date": "2026-09-22"}):
            resp = client.post(f"/sales/{sid}/edit", data=self._edit_data(notes="changed", **override),
                               follow_redirects=True)
            self.assertIn(b"Nothing was saved", resp.data, override)
        sale = db.query("SELECT Notes, Status FROM Sales WHERE SaleID=?", (sid,), one=True)
        self.assertEqual((sale["Notes"], sale["Status"]), ("delivered to gate 2", "Completed"))
        self.assertEqual(self._qty_sold(), 10)

    def test_deleting_a_stock_issue_with_credited_sales_is_rejected_without_partial_deletes(self):
        emp, cust, prod = self.make_employee(), self.make_customer(), self.make_product()
        issue_id = self.make_open_stock_issue(emp, prod, "2026-09-21", qty_issued=100)
        db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType, QtyChange, RefType, RefID)
                    VALUES (?,?,?,?,?,?)""", (prod, "2026-09-21", "Issue", -100, "StockIssue", issue_id))
        with appmod.app.app_context():
            appmod.create_sale(customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Unpaid",
                               payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                               lines=[(prod, 10, 100.0)], employee_id=emp)
        resp = self.make_admin_client().post(f"/stock-issues/{issue_id}/delete", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"credited against it", resp.data)
        self.assertEqual(db.query("SELECT COUNT(*) c FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)["c"], 1)
        self.assertEqual(db.query("""SELECT COUNT(*) c FROM InventoryTransactions
                                   WHERE RefType='StockIssue' AND RefID=?""", (issue_id,), one=True)["c"], 1)

    def test_lock_message_matches_the_reopen_and_correct_workflow(self):
        from core.helpers import locked_sale_message
        msg = locked_sale_message(7, "edit")
        self.assertIn("Reopen & Correct", msg)
        self.assertIn("Stock Issue #7", msg)
        self.assertNotIn("then retry", msg)  # reopening never unlocks the sale itself
        self.assertIn("Notes and Due Date", msg)
        self.assertNotIn("Notes and Due Date", locked_sale_message(7, "delete"))
