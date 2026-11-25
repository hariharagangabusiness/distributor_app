"""Target actuals must include Completed sales that never credited a Stock Issue line
(no open issue for that day), without double-counting ones that did."""
from core import helpers as h
from tests.base import DBTestCase, appmod, db


class TargetActualsUnlinkedSalesTests(DBTestCase):
    def sale(self, emp, prod, qty, status="Completed", date="2026-11-03", **kw):
        return h.create_sale(customer_id=self.cust, sale_date=date, status=status, payment_status="Paid",
                                  payment_due_date=None, amount_received=qty * 100, notes=None,
                                  place_of_supply_code="36", lines=[(prod, qty, 100.0)], employee_id=emp, **kw)

    def setUp(self):
        super().setUp()
        self.emp, self.prod, self.cust = self.make_employee(), self.make_product(), self.make_customer()

    def actuals(self, product_id=None):
        with appmod.app.app_context():
            return h.compute_target_actuals(self.emp, product_id, "2026-11-01", "2026-11-30")

    def test_sale_without_stock_issue_counts(self):
        with appmod.app.app_context():
            self.sale(self.emp, self.prod, 6)
        a = self.actuals()
        self.assertEqual((a["qty_sold"], a["expected"], a["collected"]), (6, 600, 600))
        self.assertEqual(self.actuals(self.prod)["qty_sold"], 6)

    def test_draft_and_other_employee_excluded(self):
        other = self.make_employee("Other")
        with appmod.app.app_context():
            self.sale(self.emp, self.prod, 3, status="Draft")
            self.sale(other, self.prod, 4)
        self.assertEqual(self.actuals()["qty_sold"], 0)

    def test_sale_credited_to_issue_is_not_double_counted(self):
        with appmod.app.test_request_context():
            h.auto_create_stock_issue_for_sale(self.emp, "2026-11-03", [(self.prod, 10, 100.0)])
            self.sale(self.emp, self.prod, 4)
        self.assertEqual(self.actuals()["qty_sold"], 4)

    def test_edit_updates_actuals(self):
        with appmod.app.app_context():
            sid = self.sale(self.emp, self.prod, 6)
            h.create_sale(customer_id=self.cust, sale_date="2026-11-03", status="Completed",
                               payment_status="Paid", payment_due_date=None, amount_received=200, notes=None,
                               place_of_supply_code="36", lines=[(self.prod, 2, 100.0)], employee_id=self.emp,
                               sale_id=sid, invoice_no="X1")
        self.assertEqual(self.actuals()["qty_sold"], 2)
