"""Regression tests for the Guided Visit Plan feature:
- Customers.Latitude/Longitude, saved from the Customer form.
- VisitPlans/VisitPlanStops: one Visit Plan per salesperson per day, built
  by Admin/Manager, with stops resolved live from Customers (or carrying
  their own name/location for an ad-hoc stop).
- Access control: only Admin/Manager can add/remove stops; marking a stop
  visited is also open to the rep it belongs to.
"""
from tests.base import DBTestCase, appmod, db


class CustomerFormLocationTests(DBTestCase):
    def test_saves_latitude_and_longitude(self):
        client = self.make_admin_client()
        resp = client.post("/customers/new", data={
            "customer_name": "Ganesh General Store", "contact_person": "", "phone": "", "email": "",
            "address": "", "zone": "North Zone", "gstin": "", "state_code": "36",
            "credit_limit": "0", "credit_days": "0", "active": "1",
            "latitude": "16.5062", "longitude": "80.6480",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT Latitude, Longitude FROM Customers WHERE CustomerName=?",
                        ("Ganesh General Store",), one=True)
        self.assertAlmostEqual(row["Latitude"], 16.5062)
        self.assertAlmostEqual(row["Longitude"], 80.6480)

    def test_blank_location_stays_null(self):
        client = self.make_admin_client()
        resp = client.post("/customers/new", data={
            "customer_name": "No Location Store", "contact_person": "", "phone": "", "email": "",
            "address": "", "zone": "North Zone", "gstin": "", "state_code": "36",
            "credit_limit": "0", "credit_days": "0", "active": "1",
            "latitude": "", "longitude": "",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT Latitude, Longitude FROM Customers WHERE CustomerName=?",
                        ("No Location Store",), one=True)
        self.assertIsNone(row["Latitude"])
        self.assertIsNone(row["Longitude"])

    def test_garbage_location_never_crashes_and_stays_null(self):
        client = self.make_admin_client()
        resp = client.post("/customers/new", data={
            "customer_name": "Garbage Loc Store", "contact_person": "", "phone": "", "email": "",
            "address": "", "zone": "North Zone", "gstin": "", "state_code": "36",
            "credit_limit": "0", "credit_days": "0", "active": "1",
            "latitude": "not-a-number", "longitude": "also-not-a-number",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT Latitude, Longitude FROM Customers WHERE CustomerName=?",
                        ("Garbage Loc Store",), one=True)
        self.assertIsNone(row["Latitude"])
        self.assertIsNone(row["Longitude"])


class GetVisitPlanTests(DBTestCase):
    def test_returns_none_when_no_plan_exists(self):
        emp = self.make_employee()
        with appmod.app.app_context():
            self.assertIsNone(appmod.get_visit_plan(emp, "2026-11-20"))

    def test_resolves_a_customer_stop_live_from_customers(self):
        emp = self.make_employee()
        cust = self.make_customer("Ganesh General Store")
        db.execute("UPDATE Customers SET Latitude=16.5062, Longitude=80.6480 WHERE CustomerID=?", (cust,))
        plan_id = db.execute("INSERT INTO VisitPlans (EmployeeID, VisitDate) VALUES (?,?)", (emp, "2026-11-20"))
        db.execute("INSERT INTO VisitPlanStops (VisitPlanID, CustomerID, SortOrder) VALUES (?,?,1)", (plan_id, cust))

        with appmod.app.app_context():
            data = appmod.get_visit_plan(emp, "2026-11-20")
        self.assertEqual(len(data["stops"]), 1)
        stop = data["stops"][0]
        self.assertEqual(stop["Name"], "Ganesh General Store")
        self.assertAlmostEqual(stop["Latitude"], 16.5062)
        self.assertIsNone(stop["VisitedAt"])
        self.assertFalse(stop["CustomerLostLocation"])

    def test_flags_a_customer_that_lost_its_saved_location(self):
        emp = self.make_employee()
        cust = self.make_customer("Ganesh General Store")
        db.execute("UPDATE Customers SET Latitude=16.5062, Longitude=80.6480 WHERE CustomerID=?", (cust,))
        plan_id = db.execute("INSERT INTO VisitPlans (EmployeeID, VisitDate) VALUES (?,?)", (emp, "2026-11-20"))
        db.execute("INSERT INTO VisitPlanStops (VisitPlanID, CustomerID, SortOrder) VALUES (?,?,1)", (plan_id, cust))
        db.execute("UPDATE Customers SET Latitude=NULL, Longitude=NULL WHERE CustomerID=?", (cust,))

        with appmod.app.app_context():
            data = appmod.get_visit_plan(emp, "2026-11-20")
        self.assertTrue(data["stops"][0]["CustomerLostLocation"])
        self.assertIsNone(data["stops"][0]["Latitude"])

    def test_resolves_an_adhoc_stop_from_its_own_fields(self):
        emp = self.make_employee()
        plan_id = db.execute("INSERT INTO VisitPlans (EmployeeID, VisitDate) VALUES (?,?)", (emp, "2026-11-20"))
        db.execute("""INSERT INTO VisitPlanStops (VisitPlanID, StopName, Latitude, Longitude, SortOrder)
                    VALUES (?,?,?,?,1)""", (plan_id, "New Prospect — MG Road", 18.5204, 73.8567))

        with appmod.app.app_context():
            data = appmod.get_visit_plan(emp, "2026-11-20")
        stop = data["stops"][0]
        self.assertEqual(stop["Name"], "New Prospect — MG Road")
        self.assertAlmostEqual(stop["Latitude"], 18.5204)


class VisitPlanAddStopTests(DBTestCase):
    def test_admin_can_add_a_customer_with_a_saved_location(self):
        emp = self.make_employee()
        cust = self.make_customer("Ganesh General Store")
        db.execute("UPDATE Customers SET Latitude=16.5062, Longitude=80.6480 WHERE CustomerID=?", (cust,))
        client = self.make_admin_client()

        resp = client.post(f"/visit-plans/{emp}/2026-11-20/add-stop", data={
            "mode": "customer", "customer_id": str(cust),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        plan = db.query("SELECT VisitPlanID FROM VisitPlans WHERE EmployeeID=? AND VisitDate=?",
                         (emp, "2026-11-20"), one=True)
        self.assertIsNotNone(plan)
        stops = db.query("SELECT * FROM VisitPlanStops WHERE VisitPlanID=?", (plan["VisitPlanID"],))
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["CustomerID"], cust)

    def test_rejects_a_customer_with_no_saved_location(self):
        emp = self.make_employee()
        cust = self.make_customer("No Location Store")
        client = self.make_admin_client()

        resp = client.post(f"/visit-plans/{emp}/2026-11-20/add-stop", data={
            "mode": "customer", "customer_id": str(cust),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"doesn", resp.data)  # "doesn't have a saved map location yet"
        plan = db.query("SELECT VisitPlanID FROM VisitPlans WHERE EmployeeID=? AND VisitDate=?",
                         (emp, "2026-11-20"), one=True)
        self.assertIsNone(plan)

    def test_admin_can_add_an_adhoc_stop(self):
        emp = self.make_employee()
        client = self.make_admin_client()

        resp = client.post(f"/visit-plans/{emp}/2026-11-20/add-stop", data={
            "mode": "adhoc", "stop_name": "New Prospect — MG Road",
            "latitude": "18.5204", "longitude": "73.8567",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        stop = db.query("SELECT * FROM VisitPlanStops", one=True)
        self.assertEqual(stop["StopName"], "New Prospect — MG Road")
        self.assertAlmostEqual(stop["Latitude"], 18.5204)

    def test_rejects_an_adhoc_stop_without_a_captured_location(self):
        emp = self.make_employee()
        client = self.make_admin_client()

        resp = client.post(f"/visit-plans/{emp}/2026-11-20/add-stop", data={
            "mode": "adhoc", "stop_name": "New Prospect — MG Road",
            "latitude": "", "longitude": "",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.query("SELECT COUNT(*) c FROM VisitPlanStops", one=True)["c"], 0)

    def test_second_stop_same_day_reuses_the_same_plan(self):
        emp = self.make_employee()
        cust1 = self.make_customer("Store One")
        cust2 = self.make_customer("Store Two")
        db.execute("UPDATE Customers SET Latitude=1, Longitude=1 WHERE CustomerID=?", (cust1,))
        db.execute("UPDATE Customers SET Latitude=2, Longitude=2 WHERE CustomerID=?", (cust2,))
        client = self.make_admin_client()

        client.post(f"/visit-plans/{emp}/2026-11-20/add-stop", data={"mode": "customer", "customer_id": str(cust1)})
        client.post(f"/visit-plans/{emp}/2026-11-20/add-stop", data={"mode": "customer", "customer_id": str(cust2)})

        plans = db.query("SELECT VisitPlanID FROM VisitPlans WHERE EmployeeID=? AND VisitDate=?",
                          (emp, "2026-11-20"))
        self.assertEqual(len(plans), 1)
        stops = db.query("SELECT * FROM VisitPlanStops WHERE VisitPlanID=?", (plans[0]["VisitPlanID"],))
        self.assertEqual(len(stops), 2)

    def test_staff_cannot_add_a_stop(self):
        emp = self.make_employee()
        cust = self.make_customer("Ganesh General Store")
        db.execute("UPDATE Customers SET Latitude=16.5062, Longitude=80.6480 WHERE CustomerID=?", (cust,))
        db.execute("INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES ('Staff', 'dashboard', 1)")
        client = self.make_role_client("Staff")

        client.post(f"/visit-plans/{emp}/2026-11-20/add-stop", data={
            "mode": "customer", "customer_id": str(cust),
        }, follow_redirects=True)
        self.assertEqual(db.query("SELECT COUNT(*) c FROM VisitPlans", one=True)["c"], 0)


class VisitPlanStopToggleAndDeleteTests(DBTestCase):
    def make_plan_with_stop(self, employee_id, visit_date="2026-11-20"):
        plan_id = db.execute("INSERT INTO VisitPlans (EmployeeID, VisitDate) VALUES (?,?)", (employee_id, visit_date))
        return db.execute("""INSERT INTO VisitPlanStops (VisitPlanID, StopName, Latitude, Longitude, SortOrder)
                            VALUES (?,?,?,?,1)""", (plan_id, "Test Stop", 1.0, 1.0))

    def test_admin_can_toggle_visited_and_back(self):
        emp = self.make_employee()
        stop_id = self.make_plan_with_stop(emp)
        client = self.make_admin_client()

        client.post(f"/visit-plans/stops/{stop_id}/toggle-visited", follow_redirects=True)
        row = db.query("SELECT VisitedAt FROM VisitPlanStops WHERE StopID=?", (stop_id,), one=True)
        self.assertIsNotNone(row["VisitedAt"])

        client.post(f"/visit-plans/stops/{stop_id}/toggle-visited", follow_redirects=True)
        row = db.query("SELECT VisitedAt FROM VisitPlanStops WHERE StopID=?", (stop_id,), one=True)
        self.assertIsNone(row["VisitedAt"])

    def test_the_reps_own_login_can_toggle_their_own_stop(self):
        emp = self.make_employee()
        stop_id = self.make_plan_with_stop(emp)
        client = self.make_role_client("Staff", username="rep_login")
        db.execute("UPDATE Users SET EmployeeID=? WHERE Username='rep_login'", (emp,))

        resp = client.post(f"/visit-plans/stops/{stop_id}/toggle-visited", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        row = db.query("SELECT VisitedAt FROM VisitPlanStops WHERE StopID=?", (stop_id,), one=True)
        self.assertIsNotNone(row["VisitedAt"])

    def test_a_different_reps_login_cannot_toggle_someone_elses_stop(self):
        emp_a = self.make_employee("A")
        emp_b = self.make_employee("B")
        stop_id = self.make_plan_with_stop(emp_a)
        client = self.make_role_client("Staff", username="other_rep")
        db.execute("UPDATE Users SET EmployeeID=? WHERE Username='other_rep'", (emp_b,))

        client.post(f"/visit-plans/stops/{stop_id}/toggle-visited", follow_redirects=True)
        row = db.query("SELECT VisitedAt FROM VisitPlanStops WHERE StopID=?", (stop_id,), one=True)
        self.assertIsNone(row["VisitedAt"])

    def test_admin_can_delete_a_stop(self):
        emp = self.make_employee()
        stop_id = self.make_plan_with_stop(emp)
        client = self.make_admin_client()

        client.post(f"/visit-plans/stops/{stop_id}/delete", follow_redirects=True)
        self.assertEqual(db.query("SELECT COUNT(*) c FROM VisitPlanStops", one=True)["c"], 0)

    def test_staff_cannot_delete_a_stop(self):
        emp = self.make_employee()
        stop_id = self.make_plan_with_stop(emp)
        db.execute("INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES ('Staff', 'dashboard', 1)")
        client = self.make_role_client("Staff")

        client.post(f"/visit-plans/stops/{stop_id}/delete", follow_redirects=True)
        self.assertEqual(db.query("SELECT COUNT(*) c FROM VisitPlanStops", one=True)["c"], 1)


class LocationTrackingViewVisitPlanTests(DBTestCase):
    def test_admin_sees_add_stop_button_and_plan_panel(self):
        emp = self.make_employee()
        client = self.make_admin_client()
        resp = client.get(f"/location-tracking?date=2026-11-20&employee_id={emp}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Today's Visit Plan", resp.data)
        self.assertIn(b"Add Stop", resp.data)

    def test_candidates_exclude_customers_without_location_and_already_added_ones(self):
        emp = self.make_employee()
        cust_located = self.make_customer("Located Store")
        cust_unlocated = self.make_customer("Unlocated Store")
        db.execute("UPDATE Customers SET Latitude=1, Longitude=1 WHERE CustomerID=?", (cust_located,))
        cust_already_added = self.make_customer("Already Added Store")
        db.execute("UPDATE Customers SET Latitude=2, Longitude=2 WHERE CustomerID=?", (cust_already_added,))
        plan_id = db.execute("INSERT INTO VisitPlans (EmployeeID, VisitDate) VALUES (?,?)", (emp, "2026-11-20"))
        db.execute("INSERT INTO VisitPlanStops (VisitPlanID, CustomerID, SortOrder) VALUES (?,?,1)",
                   (plan_id, cust_already_added))

        client = self.make_admin_client()
        resp = client.get(f"/location-tracking?date=2026-11-20&employee_id={emp}")
        self.assertIn(b"Located Store", resp.data)
        self.assertNotIn(b"Unlocated Store", resp.data)
        # "Already Added Store" legitimately appears twice: once in the plan panel's own row,
        # and once in the embedded JS data that drives its map pin/popup - but never as a
        # second <option> in the Add Stop dropdown's candidate list.
        self.assertNotIn(f'<option value="{cust_already_added}">Already Added Store</option>'.encode(), resp.data)

    def test_no_employee_selected_shows_no_visit_plan_panel(self):
        client = self.make_admin_client()
        resp = client.get("/location-tracking?date=2026-11-20")
        self.assertNotIn(b"Today's Visit Plan", resp.data)

    def test_staff_viewing_their_own_day_has_no_add_stop_button(self):
        emp = self.make_employee()
        db.execute("INSERT INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES ('Staff', 'location_tracking', 1)")
        client = self.make_role_client("Staff", username="rep_login")
        db.execute("UPDATE Users SET EmployeeID=? WHERE Username='rep_login'", (emp,))

        resp = client.get(f"/location-tracking?date=2026-11-20&employee_id={emp}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Add Stop", resp.data)

    def test_date_picker_has_no_max_so_future_days_can_be_planned(self):
        """The date input used to carry max="{today}" (sensible when this page only showed past
        ping history) - once it also hosts the Visit Plan, that cap silently blocked planning
        for any day after today. Must stay unset."""
        client = self.make_admin_client()
        resp = client.get("/location-tracking")
        self.assertNotRegex(resp.data.decode(), r'name="date"[^>]*\bmax=')

    def test_can_build_a_visit_plan_for_a_future_date(self):
        emp = self.make_employee()
        cust = self.make_customer("Future Store")
        db.execute("UPDATE Customers SET Latitude=1, Longitude=1 WHERE CustomerID=?", (cust,))
        client = self.make_admin_client()
        future_date = "2099-01-15"

        resp = client.get(f"/location-tracking?date={future_date}&employee_id={emp}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Add Stop", resp.data)

        resp = client.post(f"/visit-plans/{emp}/{future_date}/add-stop", data={
            "mode": "customer", "customer_id": str(cust),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        plan = db.query("SELECT VisitPlanID FROM VisitPlans WHERE EmployeeID=? AND VisitDate=?",
                         (emp, future_date), one=True)
        self.assertIsNotNone(plan)
