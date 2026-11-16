"""Regression tests for the "Export to Excel" feature (export_module()):
- Purchases/Sales/Stock Issues get a "Detail" sheet with one row per line
  item (not just the header-level "Summary" sheet).
- That Detail sheet - being the actual drill-down a user wants - opens as
  the active sheet by default, while modules whose "Detail" is just a
  wider column dump of the same rows (no line-item join) keep Summary
  as the default.
"""
import io

import openpyxl

from tests.base import DBTestCase, db


class ExportActiveSheetTests(DBTestCase):
    def _make_purchase_with_two_lines(self):
        supplier_id = db.execute("INSERT INTO Suppliers (SupplierName, Active) VALUES ('Test Supplier', 1)")
        product_id = self.make_product("Test Product")
        purchase_id = db.execute(
            """INSERT INTO Purchases (PONumber, SupplierID, PurchaseDate, InvoiceNumber, TotalAmount)
               VALUES ('PO-1', ?, '2026-11-01', 'INV-1', 1180)""", (supplier_id,))
        for qty in (10, 20):
            db.execute(
                """INSERT INTO PurchaseLines (PurchaseID, ProductID, Qty, UnitCost, LineTotal,
                        HSNCode, GSTRate, TaxableValue, CGSTAmount, SGSTAmount)
                   VALUES (?, ?, ?, 50, 590, '1234', 18, 500, 45, 45)""",
                (purchase_id, product_id, qty))
        return purchase_id

    def test_purchases_export_has_a_detail_sheet_with_line_items(self):
        self._make_purchase_with_two_lines()
        client = self.make_admin_client()
        resp = client.get("/export/purchases")
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.data))
        self.assertEqual(wb.sheetnames, ["Summary", "Detail"])
        detail = wb["Detail"]
        self.assertEqual(detail.max_row, 3)  # header + 2 line items
        self.assertIn("Product", next(detail.iter_rows(min_row=1, max_row=1, values_only=True)))

    def test_purchases_export_opens_on_the_detail_sheet(self):
        self._make_purchase_with_two_lines()
        client = self.make_admin_client()
        resp = client.get("/export/purchases")
        wb = openpyxl.load_workbook(io.BytesIO(resp.data))
        self.assertEqual(wb.active.title, "Detail")

    def test_sales_and_stock_issues_exports_also_open_on_detail(self):
        client = self.make_admin_client()
        for module in ("sales", "stock_issues"):
            resp = client.get(f"/export/{module}")
            self.assertEqual(resp.status_code, 200)
            wb = openpyxl.load_workbook(io.BytesIO(resp.data))
            self.assertEqual(wb.active.title, "Detail", f"{module} export should open on Detail")

    def test_a_plain_column_dump_export_still_opens_on_summary(self):
        self.make_product("Some Product")
        client = self.make_admin_client()
        resp = client.get("/export/products")
        self.assertEqual(resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(resp.data))
        self.assertEqual(wb.sheetnames, ["Summary", "Detail"])
        self.assertEqual(wb.active.title, "Summary")
