"""Shared test setup. Import this BEFORE anything else in a test file (even
before `import db` or `import app`) - it points DB_PATH at a throwaway file
before app.py's module-level `db.init_db()` and background reminder thread
run, so a plain `import app` during tests never touches (or creates) the
real ./db/distributor.db.

Each test gets its own fresh, empty, fully-migrated SQLite file (see
DBTestCase.setUp/tearDown below) - full isolation between tests, at the
cost of re-running db.init_db() per test, which is cheap (schema.sql on an
empty file).
"""
import os
import tempfile
import unittest

_BOOT_DB = tempfile.NamedTemporaryFile(prefix="distributor_test_boot_", suffix=".db", delete=False)
_BOOT_DB.close()
os.environ.setdefault("DB_PATH", _BOOT_DB.name)

import db  # noqa: E402 - must come after the DB_PATH env var is set
import app as appmod  # noqa: E402 - ditto; this is where app.py's import-time db.init_db() runs

# Tests post directly to routes without first fetching a page's rendered
# csrf_token() - the standard Flask-WTF testing pattern is to disable
# enforcement here rather than have every test scrape a token first. This
# only affects this test process's copy of the app config, never production.
appmod.app.config["WTF_CSRF_ENABLED"] = False


class DBTestCase(unittest.TestCase):
    """Base class for any test that needs a real (temporary) database.
    Subclasses can add fixtures in their own setUp() after calling super()."""

    def setUp(self):
        fd, path = tempfile.mkstemp(prefix="distributor_test_", suffix=".db")
        os.close(fd)
        os.remove(path)  # db.init_db() creates it fresh
        self._db_path = path
        db.DB_PATH = path
        db.init_db()

    def tearDown(self):
        db.DB_PATH = _BOOT_DB.name
        try:
            os.remove(self._db_path)
        except OSError:
            pass

    # --- small fixture helpers shared across test modules ---

    def make_employee(self, name="Test Salesperson"):
        return db.execute("INSERT INTO Employees (EmployeeName, Status) VALUES (?, 'Active')", (name,))

    def make_customer(self, name="Test Customer"):
        return db.execute("INSERT INTO Customers (CustomerName, Active) VALUES (?, 1)", (name,))

    def make_product(self, name="Test Product", selling_price=100.0, cost_price=50.0):
        return db.execute(
            """INSERT INTO Products (ProductName, Unit, CostPrice, SellingPrice, MinStock, MaxStock)
               VALUES (?, 'PCS', ?, ?, 0, 0)""",
            (name, cost_price, selling_price))

    def make_reconciled_stock_issue(self, employee_id, product_id, issue_date, qty_issued, qty_sold,
                                     unit_price=100.0, reconciled_at=None, expected=0, collected=0, discrepancy=0):
        issue_id = db.execute(
            """INSERT INTO StockIssues (EmployeeID, IssueDate, Status, ReconciledAt, ExpectedAmount,
                    CashCollected, Discrepancy)
               VALUES (?, ?, 'Reconciled', ?, ?, ?, ?)""",
            (employee_id, issue_date, reconciled_at or f"{issue_date}T12:00:00", expected, collected, discrepancy))
        db.execute(
            """INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice, QtySold, QtyReturned, QtyFree)
               VALUES (?, ?, ?, ?, ?, 0, 0)""",
            (issue_id, product_id, qty_issued, unit_price, qty_sold))
        return issue_id

    def make_open_stock_issue(self, employee_id, product_id, issue_date, qty_issued, unit_price=100.0):
        issue_id = db.execute(
            "INSERT INTO StockIssues (EmployeeID, IssueDate, Status) VALUES (?, ?, 'Issued')",
            (employee_id, issue_date))
        db.execute(
            "INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice) VALUES (?, ?, ?, ?)",
            (issue_id, product_id, qty_issued, unit_price))
        return issue_id

    def make_admin_client(self, username="test_admin", password="TestPass123!"):
        """Creates an Admin login and returns a logged-in Flask test client,
        for tests that need to drive real routes (forms, guardrails,
        redirects) rather than calling app.py functions directly."""
        from werkzeug.security import generate_password_hash
        db.execute("INSERT INTO Users (Username, PasswordHash, Role, Active) VALUES (?, ?, 'Admin', 1)",
                   (username, generate_password_hash(password)))
        client = appmod.app.test_client()
        resp = client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
        assert resp.status_code == 200, f"login failed: {resp.status_code}"
        return client
