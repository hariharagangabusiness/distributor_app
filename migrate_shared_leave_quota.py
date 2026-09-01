"""One-time migration: changes annual leave quotas (Sick Leave, Casual
Leave, etc.) from being set individually per employee to being SHARED —
one quota per leave type, applied to every employee alike.

This does NOT delete any of your existing data:
  - Adds a new LeaveTypes.AnnualQuota column (the new shared quota).
  - For each leave type, seeds that new column from the OLD per-employee
    EmployeeLeaveQuotas table by taking the HIGHEST quota any employee
    had for it, so nobody's entitlement is accidentally reduced by this
    change. Review/adjust the result on the Leave Types page afterwards.
  - The old EmployeeLeaveQuotas table and everything in it is left
    exactly as it was - nothing is deleted. It's simply no longer read
    by the app, kept only so the historical per-employee configuration
    isn't lost.
  - Already-saved attendance months (and their locked-in Paid/Unpaid
    leave splits and LOP days) are completely untouched by this change -
    it only affects the quota used for FUTURE attendance entries.

Safe to run multiple times.

Usage: python migrate_shared_leave_quota.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def run():
    db.init_db()  # ensures LeaveTypes/EmployeeLeaveQuotas exist (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()

    added_column = False
    if not column_exists(conn, "LeaveTypes", "AnnualQuota"):
        conn.execute("ALTER TABLE LeaveTypes ADD COLUMN AnnualQuota REAL NOT NULL DEFAULT 0")
        added_column = True
        print("  Added LeaveTypes.AnnualQuota")

    leave_types = conn.execute("SELECT LeaveTypeID, LeaveTypeName, LeaveCode, AnnualQuota FROM LeaveTypes").fetchall()
    seeded_any = False
    still_zero = []
    for lt in leave_types:
        ltid, name, code, current_quota = lt
        if current_quota:
            continue  # already has a shared quota set (either seeded before, or set on the Leave Types page) - leave it
        old_max = conn.execute(
            "SELECT MAX(AnnualQuota) FROM EmployeeLeaveQuotas WHERE LeaveTypeID=?", (ltid,)
        ).fetchone()[0]
        if old_max:
            conn.execute("UPDATE LeaveTypes SET AnnualQuota=? WHERE LeaveTypeID=?", (old_max, ltid))
            print(f"  {name} ({code}): shared quota set to {old_max:g} days/year "
                  f"(highest of your old per-employee quotas for it)")
            seeded_any = True
        else:
            still_zero.append(f"{name} ({code})")

    conn.commit()
    conn.close()

    print("\nMigration complete.")
    if seeded_any:
        print("Review the shared quotas seeded above on the Leave & Attendance > Leave Types")
        print("page and adjust any that don't match what you actually want everyone to get.")
    if still_zero:
        print(f"These leave type(s) have no quota configured yet (nothing to seed from either): "
              f"{', '.join(still_zero)}.")
        print("Set a real days/year amount for each on the Leave & Attendance > Leave Types page.")
    if not seeded_any and not still_zero:
        print("Nothing to do - every leave type already has a shared quota set.")
    print("\nWhat changed: every employee now draws from the SAME annual quota per leave")
    print("type (set once on Leave & Attendance > Leave Types), instead of each employee")
    print("having their own. Nothing about already-saved attendance months changes.")


if __name__ == "__main__":
    run()
