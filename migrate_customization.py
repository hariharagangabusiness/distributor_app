"""One-time migration: adds attachments, custom fields, dashboard widget
customization, and list column customization to an EXISTING database
without touching any data you've already entered.

These are all brand-new tables (nothing existing changes shape), so this
is really just running the schema again plus seeding sensible dashboard
widget defaults — safe to run multiple times, and safe even on a
brand-new database (init_db.py / seed_demo.py already include this).

Usage: python migrate_customization.py
"""
import db

DEFAULT_WIDGETS = [
    ("stats", 1, 0),
    ("low_stock", 1, 1),
    ("over_stock", 1, 2),
    ("maintenance_due", 1, 3),
    ("docs_expiring", 1, 4),
    ("salary_status", 1, 5),
    ("active_advances", 1, 6),
]


def run():
    db.init_db()  # creates Attachments, CustomFieldDefinitions/Values,
                  # DashboardWidgets, ListViewColumns if missing

    conn = db.get_conn()
    for key, visible, order in DEFAULT_WIDGETS:
        conn.execute("INSERT OR IGNORE INTO DashboardWidgets (WidgetKey, Visible, DisplayOrder) VALUES (?,?,?)",
                     (key, visible, order))
    conn.commit()
    conn.close()

    print("Migration complete. New capabilities now available:")
    print("  - Attachments on Employees, Advance Payments, Vehicles, Purchases, Expenses")
    print("  - Custom fields on every tab (Settings > Custom Fields)")
    print("  - Dashboard widget show/hide & reorder (Dashboard > Customize)")
    print("  - Customizable list columns (each list page > Customize Columns)")


if __name__ == "__main__":
    run()
