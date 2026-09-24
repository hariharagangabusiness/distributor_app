"""Regression tests for purchases_list() and customers_list()'s server-side
search/pagination - same recipe applied to sales_list() in the previous PR,
now extended to these two lists.
"""
from tests.base import DBTestCase, appmod, db


class PurchasesListPaginationTests(DBTestCase):
    def make_supplier(self, name="Test Supplier"):
        return db.execute("INSERT INTO Suppliers (SupplierName, Active) VALUES (?, 1)", (name,))

    def make_purchase(self, supplier_id, po_number):
        db.execute("""INSERT INTO Purchases (SupplierID, PONumber, PurchaseDate, Status, PaymentStatus, TotalAmount)
                    VALUES (?, ?, '2026-09-21', 'Completed', 'Unpaid', 100)""", (supplier_id, po_number))

    def test_pagination_splits_results_across_pages(self):
        sup = self.make_supplier()
        for i in range(appmod.PURCHASES_LIST_PAGE_SIZE + 3):
            self.make_purchase(sup, f"PO-{i:04d}")

        client = self.make_admin_client()
        resp = client.get("/purchases")
        self.assertIn(b"Page 1 of 2", resp.data)
        resp2 = client.get("/purchases?page=2")
        self.assertIn(b"Page 2 of 2", resp2.data)

    def test_search_filters_by_supplier_name(self):
        sup1 = self.make_supplier("Alpha Distributors")
        sup2 = self.make_supplier("Beta Wholesale")
        self.make_purchase(sup1, "PO-A1")
        self.make_purchase(sup2, "PO-B1")

        client = self.make_admin_client()
        resp = client.get("/purchases?q=Alpha")
        self.assertIn(b"Alpha Distributors", resp.data)
        self.assertNotIn(b"Beta Wholesale", resp.data)

    def test_out_of_range_page_does_not_error(self):
        sup = self.make_supplier()
        self.make_purchase(sup, "PO-0001")
        client = self.make_admin_client()
        resp = client.get("/purchases?page=999")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"No purchases recorded yet.", resp.data)


class CustomersListPaginationTests(DBTestCase):
    def test_pagination_splits_results_across_pages(self):
        for i in range(appmod.CUSTOMERS_LIST_PAGE_SIZE + 3):
            db.execute("INSERT INTO Customers (CustomerName, Active) VALUES (?, 1)", (f"Customer {i:04d}",))

        client = self.make_admin_client()
        resp = client.get("/customers")
        self.assertIn(b"Page 1 of 2", resp.data)
        resp2 = client.get("/customers?page=2")
        self.assertIn(b"Page 2 of 2", resp2.data)

    def test_search_filters_by_name_phone_or_zone(self):
        self.make_customer("Alpha Traders")
        db.execute("INSERT INTO Customers (CustomerName, Phone, Zone, Active) VALUES ('Beta Wines', '9999999999', 'North', 1)")

        client = self.make_admin_client()
        resp = client.get("/customers?q=Alpha")
        self.assertIn(b"Alpha Traders", resp.data)
        self.assertNotIn(b"Beta Wines", resp.data)

        resp2 = client.get("/customers?q=North")
        self.assertIn(b"Beta Wines", resp2.data)
        self.assertNotIn(b"Alpha Traders", resp2.data)

    def test_serial_number_continues_across_pages_not_reset(self):
        for i in range(appmod.CUSTOMERS_LIST_PAGE_SIZE + 3):
            db.execute("INSERT INTO Customers (CustomerName, Active) VALUES (?, 1)", (f"Customer {i:04d}",))
        client = self.make_admin_client()
        resp = client.get("/customers?page=2")
        # First row on page 2 should be numbered PAGE_SIZE+1, not 1.
        expected_first_row_number = str(appmod.CUSTOMERS_LIST_PAGE_SIZE + 1).encode()
        self.assertIn(expected_first_row_number, resp.data)
