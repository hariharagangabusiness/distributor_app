import sqlite3

import db
from tests.base import DBTestCase


class PnlReportTests(DBTestCase):
    def test_pnl_renders_on_fresh_database(self):
        resp = self.make_admin_client().get("/reports/pnl")
        self.assertEqual(resp.status_code, 200)

    def test_init_db_adds_purchase_price_to_older_vehicles_table(self):
        client = self.make_admin_client()
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("DROP TABLE Vehicles")  # recreate without PurchasePrice, as on a pre-migration database
        conn.execute("CREATE TABLE Vehicles (VehicleID INTEGER PRIMARY KEY, RegistrationNumber TEXT, PurchaseDate TEXT)")
        conn.commit()
        conn.close()
        db.init_db()
        cols = [r["name"] for r in db.query("PRAGMA table_info(Vehicles)")]
        self.assertIn("PurchasePrice", cols)
        self.assertEqual(client.get("/reports/pnl").status_code, 200)
