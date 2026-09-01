"""One-time migration: adds Employee Leave & Attendance management
(configurable leave types, per-employee annual leave quotas, monthly
attendance summaries, and loss-of-pay-aware salary calculation) plus
attachment support inside Custom Fields, to an EXISTING database without
touching any data you've already entered.

Safe to run multiple times. Safe to run even on a brand-new database
(init_db.py / seed_demo.py already include these tables, so this becomes
mostly a no-op there beyond seeding the two default leave types).

Usage: python migrate_leave_attendance.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def add_column_if_missing(conn, table, column, coltype_and_default):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_and_default}")
        print(f"  Added {table}.{column}")


DEFAULT_LEAVE_TYPES = [
    ("Sick Leave", "SL", 0),
    ("Casual Leave", "CL", 1),
]


def run():
    db.init_db()  # creates LeaveTypes, EmployeeLeaveQuotas, AttendanceMonthly,
                  # AttendanceLeaveDetail if missing (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()

    print("Checking SalaryPayments...")
    add_column_if_missing(conn, "SalaryPayments", "GrossSalary", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "SalaryPayments", "LOPDays", "REAL NOT NULL DEFAULT 0")
    # Backfill existing payment rows so GrossSalary isn't left at 0 for old records.
    conn.execute("UPDATE SalaryPayments SET GrossSalary = BasicAmount WHERE GrossSalary = 0")

    print("Seeding default leave types (Sick Leave, Casual Leave) if not already present...")
    for name, code, order in DEFAULT_LEAVE_TYPES:
        exists = conn.execute("SELECT 1 FROM LeaveTypes WHERE LeaveCode=?", (code,)).fetchone()
        if not exists:
            conn.execute("INSERT INTO LeaveTypes (LeaveTypeName, LeaveCode, Active, DisplayOrder) VALUES (?,?,1,?)",
                         (name, code, order))
            print(f"  Added leave type {name} ({code})")

    conn.commit()
    conn.close()
    print("\nMigration complete. New capabilities now available:")
    print("  - Leave & Attendance tab: configure leave types, set each employee's")
    print("    annual quota per leave type (Employees > Edit), and enter each")
    print("    month's attendance summary (Leave & Attendance > pick month).")
    print("  - Generating a month's salary schedule now automatically applies a")
    print("    loss-of-pay deduction for any days beyond an employee's recorded")
    print("    attendance/leave balance for that month (months with no attendance")
    print("    entry are treated as full pay, same as before this update).")
    print("  - Custom Fields can now use an 'Attachment' field type to let you")
    print("    upload a file for that field, same as the built-in attachment tabs.")


if __name__ == "__main__":
    run()
