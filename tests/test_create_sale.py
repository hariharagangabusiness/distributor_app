"""Regression tests for create_sale()'s stock-posting logic - the credited/
covered/direct-deduction split that a whole investigation this repo's history
was spent tracing by hand (see the diagnose_invoice_stock_issue_trace.py
commits). These scenarios are locked in here so a future change can't
reintroduce the same class of bug silently.
"""
from tests.base import DBTestCase, appmod, db


class CreateSaleCreditLogicTests(DBTestCase):
    def test_open_issue_is_credited_via_sale_stock_issue_links(self):
        """A sale within an OPEN Stock Issue's remaining capacity should be
        fully credited (SaleStockIssueLinks), with nothing deducted directly."""
        emp = self.make_employee()
        cust = self.make_customer()
        prod = self.make_product()
        self.make_open_stock_issue(emp, prod, "2026-09-21", qty_issued=50)

        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36",
                lines=[(prod, 20, 100.0)], employee_id=emp)

        credited = db.query("SELECT COALESCE(SUM(QtyApplied),0) q FROM SaleStockIssueLinks WHERE SaleID=?",
                             (sale_id,), one=True)["q"]
        direct = db.query("""SELECT COALESCE(SUM(-QtyChange),0) q FROM InventoryTransactions
                           WHERE RefType='Sale' AND RefID=?""", (sale_id,), one=True)["q"]
        self.assertEqual(credited, 20)
        self.assertEqual(direct, 0)

    def test_oversell_beyond_open_issue_capacity_falls_to_direct_deduction(self):
        """Selling more than an open issue's remaining capacity: the covered
        portion is credited, the rest is a direct warehouse deduction flagged
        in DirectSaleReviews."""
        emp = self.make_employee()
        cust = self.make_customer()
        prod = self.make_product()
        self.make_open_stock_issue(emp, prod, "2026-09-21", qty_issued=10)

        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=0,
                notes=None, place_of_supply_code="36",
                lines=[(prod, 15, 100.0)], employee_id=emp)

        credited = db.query("SELECT COALESCE(SUM(QtyApplied),0) q FROM SaleStockIssueLinks WHERE SaleID=?",
                             (sale_id,), one=True)["q"]
        direct = db.query("""SELECT COALESCE(-it.QtyChange,0) q, dsr.Status FROM InventoryTransactions it
                           JOIN DirectSaleReviews dsr ON dsr.TransactionID = it.TransactionID
                           WHERE it.RefType='Sale' AND it.RefID=?""", (sale_id,), one=True)
        self.assertEqual(credited, 10)
        self.assertEqual(direct["q"], 5)
        self.assertEqual(direct["Status"], "Pending")

    def test_no_employee_always_deducts_directly_regardless_of_open_issues(self):
        """A sale with no salesperson can NEVER credit a Stock Issue, even if
        one happens to be open for someone else that day (find_open_stock_issue
        requires employee_id) - the single most common reason a plain
        counter/customer sale looks 'excluded' from Stock Issues."""
        cust = self.make_customer()
        prod = self.make_product()

        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed",
                payment_status="Paid", payment_due_date=None, amount_received=100,
                notes=None, place_of_supply_code="36",
                lines=[(prod, 3, 100.0)])  # no employee_id

        credited = db.query("SELECT COUNT(*) c FROM SaleStockIssueLinks WHERE SaleID=?", (sale_id,), one=True)["c"]
        direct = db.query("""SELECT COALESCE(SUM(-QtyChange),0) q FROM InventoryTransactions
                           WHERE RefType='Sale' AND RefID=?""", (sale_id,), one=True)["q"]
        self.assertEqual(credited, 0)
        self.assertEqual(direct, 3)

    def test_late_sale_against_reconciled_issue_splits_covered_and_direct(self):
        """THE core scenario from this repo's reconciliation investigation: a
        sale entered for a salesperson/date whose Stock Issue is ALREADY
        Reconciled can't get a SaleStockIssueLinks row (that only exists for
        an open issue) - instead, whatever of the reconciled issue's own
        capacity is still unaccounted-for silently covers part of it (no
        ledger entry - the stock already left the warehouse once), and only
        the genuine excess is deducted directly. Numbers mirror the real
        invoice this was traced from: Qty Issued 140, already-reconciled Qty
        Sold 40, one other same-day sale of 40 units, then this 100-unit sale
        -> 60 silently covered, 40 posted as a direct deduction."""
        emp = self.make_employee()
        cust = self.make_customer()
        prod = self.make_product()
        self.make_reconciled_stock_issue(emp, prod, "2026-09-21", qty_issued=140, qty_sold=40)

        with appmod.app.app_context():
            # An earlier same-day sale for the same employee/product, also
            # falling into the reconciled-capacity path - consumes some of
            # the remaining room before the sale under test arrives.
            appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Paid",
                payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                lines=[(prod, 40, 100.0)], employee_id=emp)

            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Unpaid",
                payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                lines=[(prod, 100, 100.0)], employee_id=emp)

        credited = db.query("SELECT COUNT(*) c FROM SaleStockIssueLinks WHERE SaleID=?", (sale_id,), one=True)["c"]
        direct = db.query("""SELECT COALESCE(SUM(-QtyChange),0) q FROM InventoryTransactions
                           WHERE RefType='Sale' AND RefID=?""", (sale_id,), one=True)["q"]
        self.assertEqual(credited, 0, "a Reconciled issue never gets a live SaleStockIssueLinks credit")
        self.assertEqual(direct, 40, "60 of the 100 units should be silently covered, only 40 posted directly")

    def test_editing_a_sale_entangled_with_a_reconciled_issue_is_blocked(self):
        """Guardrail: editing a Sale whose salesperson has an already-
        Reconciled Stock Issue for that date must raise SaleStockIssueLockedError
        rather than silently recomputing the credit/covered/direct split
        against the issue's CURRENT state."""
        emp = self.make_employee()
        cust = self.make_customer()
        prod = self.make_product()
        issue_id = self.make_reconciled_stock_issue(emp, prod, "2026-09-21", qty_issued=140, qty_sold=40)

        with appmod.app.app_context():
            sale_id = appmod.create_sale(
                customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Unpaid",
                payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                lines=[(prod, 10, 100.0)], employee_id=emp)

            with self.assertRaises(appmod.SaleStockIssueLockedError) as ctx:
                appmod.create_sale(
                    customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Paid",
                    payment_due_date=None, amount_received=1000, notes=None, place_of_supply_code="36",
                    lines=[(prod, 10, 100.0)], employee_id=emp, sale_id=sale_id,
                    invoice_no=db.query("SELECT InvoiceNumber FROM Sales WHERE SaleID=?", (sale_id,), one=True)["InvoiceNumber"])
        self.assertEqual(ctx.exception.issue_id, issue_id)
        # Nothing should have changed - the guard fires before any write in the edit branch.
        unchanged = db.query("SELECT AmountReceived FROM Sales WHERE SaleID=?", (sale_id,), one=True)
        self.assertEqual(unchanged["AmountReceived"], 0)

    def test_multi_line_sale_is_atomic_a_bad_line_rolls_back_everything(self):
        """A failure partway through a multi-line sale (e.g. a bad ProductID
        on the second line) must roll back the ENTIRE sale, not leave a
        half-written Sales/SalesLines row behind - see db.transaction()."""
        cust = self.make_customer()
        prod = self.make_product()
        before_sales = db.query("SELECT COUNT(*) c FROM Sales", one=True)["c"]
        before_lines = db.query("SELECT COUNT(*) c FROM SalesLines", one=True)["c"]

        with appmod.app.app_context():
            with self.assertRaises(Exception):
                appmod.create_sale(
                    customer_id=cust, sale_date="2026-09-21", status="Completed", payment_status="Paid",
                    payment_due_date=None, amount_received=0, notes=None, place_of_supply_code="36",
                    lines=[(prod, 2, 100.0), (999999, 1, 10.0)])  # bad ProductID -> FK violation

        self.assertEqual(db.query("SELECT COUNT(*) c FROM Sales", one=True)["c"], before_sales)
        self.assertEqual(db.query("SELECT COUNT(*) c FROM SalesLines", one=True)["c"], before_lines)
