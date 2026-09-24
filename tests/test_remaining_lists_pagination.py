"""Regression tests for the last four unbounded list pages -
suppliers_list(), employees_list(), vehicles_list(), advances_list() -
converted to server-side search/pagination using the same recipe already
shipped for Sales, Purchases, and Customers.
"""
from tests.base import DBTestCase, appmod, db


class SuppliersListPaginationTests(DBTestCase):
    def make_supplier(self, name="Test Supplier", **extra):
        cols = {"SupplierName": name, "Active": 1}
        cols.update(extra)
        fields = ", ".join(cols.keys())
        placeholders = ", ".join("?" for _ in cols)
        return db.execute(f"INSERT INTO Suppliers ({fields}) VALUES ({placeholders})", tuple(cols.values()))

    def test_pagination_splits_results_across_pages(self):
        for i in range(appmod.SUPPLIERS_LIST_PAGE_SIZE + 3):
            self.make_supplier(f"Supplier {i:04d}")

        client = self.make_admin_client()
        resp = client.get("/suppliers")
        self.assertIn(b"Page 1 of 2", resp.data)
        resp2 = client.get("/suppliers?page=2")
        self.assertIn(b"Page 2 of 2", resp2.data)

    def test_search_filters_by_name_or_gstin(self):
        self.make_supplier("Alpha Distributors", GSTIN="29AAAAA0000A1Z5")
        self.make_supplier("Beta Wholesale", GSTIN="27BBBBB0000B1Z5")

        client = self.make_admin_client()
        resp = client.get("/suppliers?q=Alpha")
        self.assertIn(b"Alpha Distributors", resp.data)
        self.assertNotIn(b"Beta Wholesale", resp.data)

        resp2 = client.get("/suppliers?q=27BBBBB")
        self.assertIn(b"Beta Wholesale", resp2.data)
        self.assertNotIn(b"Alpha Distributors", resp2.data)

    def test_out_of_range_page_does_not_error(self):
        self.make_supplier()
        client = self.make_admin_client()
        resp = client.get("/suppliers?page=999")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"No suppliers yet.", resp.data)


class EmployeesListPaginationTests(DBTestCase):
    def test_pagination_splits_results_across_pages(self):
        for i in range(appmod.EMPLOYEES_LIST_PAGE_SIZE + 3):
            self.make_employee(f"Employee {i:04d}")

        client = self.make_admin_client()
        resp = client.get("/employees")
        self.assertIn(b"Page 1 of 2", resp.data)
        resp2 = client.get("/employees?page=2")
        self.assertIn(b"Page 2 of 2", resp2.data)

    def test_search_filters_by_name_designation_or_status(self):
        db.execute("INSERT INTO Employees (EmployeeName, Designation, Status) VALUES ('Alpha Kumar', 'Driver', 'Active')")
        db.execute("INSERT INTO Employees (EmployeeName, Designation, Status) VALUES ('Beta Singh', 'Salesperson', 'Inactive')")

        client = self.make_admin_client()
        resp = client.get("/employees?q=Alpha")
        self.assertIn(b"Alpha Kumar", resp.data)
        self.assertNotIn(b"Beta Singh", resp.data)

        resp2 = client.get("/employees?q=Salesperson")
        self.assertIn(b"Beta Singh", resp2.data)
        self.assertNotIn(b"Alpha Kumar", resp2.data)

    def test_out_of_range_page_does_not_error(self):
        self.make_employee()
        client = self.make_admin_client()
        resp = client.get("/employees?page=999")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"No employees yet.", resp.data)


class VehiclesListPaginationTests(DBTestCase):
    def make_vehicle(self, reg, **extra):
        cols = {"RegistrationNumber": reg, "Status": "Active"}
        cols.update(extra)
        fields = ", ".join(cols.keys())
        placeholders = ", ".join("?" for _ in cols)
        return db.execute(f"INSERT INTO Vehicles ({fields}) VALUES ({placeholders})", tuple(cols.values()))

    def test_pagination_splits_results_across_pages(self):
        for i in range(appmod.VEHICLES_LIST_PAGE_SIZE + 3):
            self.make_vehicle(f"KA-01-{i:04d}")

        client = self.make_admin_client()
        resp = client.get("/vehicles")
        self.assertIn(b"Page 1 of 2", resp.data)
        resp2 = client.get("/vehicles?page=2")
        self.assertIn(b"Page 2 of 2", resp2.data)

    def test_search_filters_by_reg_number_or_make(self):
        self.make_vehicle("KA-01-AB-1234", Make="Tata")
        self.make_vehicle("KA-02-CD-5678", Make="Ashok Leyland")

        client = self.make_admin_client()
        resp = client.get("/vehicles?q=AB-1234")
        self.assertIn(b"KA-01-AB-1234", resp.data)
        self.assertNotIn(b"KA-02-CD-5678", resp.data)

        resp2 = client.get("/vehicles?q=Leyland")
        self.assertIn(b"KA-02-CD-5678", resp2.data)
        self.assertNotIn(b"KA-01-AB-1234", resp2.data)

    def test_out_of_range_page_does_not_error(self):
        self.make_vehicle("KA-01-AA-0001")
        client = self.make_admin_client()
        resp = client.get("/vehicles?page=999")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"No vehicles yet.", resp.data)


class AdvancesListPaginationTests(DBTestCase):
    def make_advance(self, employee_id, reason="Personal", status="Active", advance_date="2026-01-01"):
        return db.execute(
            """INSERT INTO AdvancePayments (EmployeeID, AdvanceDate, Amount, Reason, RepaymentMonths,
                    MonthlyDeduction, BalanceRemaining, Status)
               VALUES (?, ?, 1000, ?, 1, 1000, 1000, ?)""",
            (employee_id, advance_date, reason, status))

    def test_pagination_splits_results_across_pages(self):
        emp = self.make_employee()
        for i in range(appmod.ADVANCES_LIST_PAGE_SIZE + 3):
            self.make_advance(emp, advance_date=f"2026-01-{(i % 28) + 1:02d}")

        client = self.make_admin_client()
        resp = client.get("/advances")
        self.assertIn(b"Page 1 of 2", resp.data)
        resp2 = client.get("/advances?page=2")
        self.assertIn(b"Page 2 of 2", resp2.data)

    def test_search_filters_by_employee_name_or_reason(self):
        emp1 = self.make_employee("Alpha Kumar")
        emp2 = self.make_employee("Beta Singh")
        self.make_advance(emp1, reason="Medical emergency")
        self.make_advance(emp2, reason="Festival")

        client = self.make_admin_client()
        resp = client.get("/advances?q=Alpha")
        self.assertIn(b"Alpha Kumar", resp.data)
        self.assertNotIn(b"Beta Singh", resp.data)

        resp2 = client.get("/advances?q=Festival")
        self.assertIn(b"Beta Singh", resp2.data)
        self.assertNotIn(b"Alpha Kumar", resp2.data)

    def test_out_of_range_page_does_not_error(self):
        emp = self.make_employee()
        self.make_advance(emp)
        client = self.make_admin_client()
        resp = client.get("/advances?page=999")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"No advances recorded yet.", resp.data)
