"""One-time migration: adds the Access Control feature (Staff / Supervisor /
Manager / Admin employee hierarchy, with an Admin-only "Settings > Access
Control" screen to choose which sidebar tabs each of Staff/Supervisor/Manager
can see and use) to an EXISTING database without touching any data you've
already entered.

Safe to run multiple times. Safe to run even on a brand-new database
(init_db.py / seed_demo.py already include the RoleTabPermissions table via
schema.sql, so this becomes mostly a no-op there beyond seeding defaults).

Usage: python migrate_access_control.py
"""
import db

# Mirrors ACCESS_TABS in app.py - kept as plain string keys here (rather than
# importing app.py) so this script has no dependency on the web app itself
# and stays runnable standalone, same pattern as the other migrate_*.py
# scripts in this project.
#
# Defaults are chosen to preserve exactly what Staff could already see
# before this feature existed (the tabs that were NOT wrapped in an
# "Admin only" check in base.html), assign the same starting point to the
# new Supervisor role, and give Manager everything Staff/Supervisor get
# plus the business-operations tabs that used to be Admin-only (Purchasing,
# GST Filing, Profit & Loss, Scheme Claims, Employees, Salary, Advances) -
# but NOT the system-configuration tabs (Company Settings, Custom Fields,
# Customize Dashboard, Manage Users, Access Control itself), which stay
# Admin-only by default even for Manager. An Admin can change any of this
# afterwards from Settings > Access Control.
STAFF_AND_SUPERVISOR_DEFAULT = [
    "inventory", "customers", "sales", "stock_issues", "stock_issues_report",
    "targets", "expenses", "vehicles", "maintenance", "attendance",
]
MANAGER_EXTRA_DEFAULT = [
    "suppliers", "purchases", "gst", "pnl", "scheme_claims",
    "employees", "salary", "advances",
]


def run():
    db.init_db()  # creates RoleTabPermissions if missing (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()

    existing = conn.execute("SELECT COUNT(*) c FROM RoleTabPermissions").fetchone()["c"]
    if existing:
        print(f"RoleTabPermissions already has {existing} row(s) - leaving your existing "
              f"Access Control settings as-is (delete rows manually first if you want these "
              f"defaults re-seeded).")
    else:
        print("Seeding default Access Control permissions...")
        rows = []
        for key in STAFF_AND_SUPERVISOR_DEFAULT:
            rows.append(("Staff", key, 1))
            rows.append(("Supervisor", key, 1))
            rows.append(("Manager", key, 1))
        for key in MANAGER_EXTRA_DEFAULT:
            rows.append(("Manager", key, 1))
        conn.executemany(
            "INSERT OR IGNORE INTO RoleTabPermissions (Role, TabKey, Allowed) VALUES (?,?,?)",
            rows,
        )
        conn.commit()
        print(f"  Added {len(rows)} default permission rows for Staff/Supervisor/Manager.")

    conn.close()
    print("\nMigration complete. New capabilities now available:")
    print("  - User roles are now Staff, Supervisor, Manager, or Admin (Admin > Manage Users).")
    print("  - Admin > Settings > Access Control lets you choose exactly which sidebar tabs")
    print("    each of Staff/Supervisor/Manager can see and use. Admin itself always has")
    print("    access to everything and isn't configurable away.")


if __name__ == "__main__":
    run()
