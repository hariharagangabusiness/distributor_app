"""One-time migration for a Targets follow-up (phase 21):

Adds Incentive support to the Targets tab:
  - Targets.BaseIncentiveAmount (Rs) - a flat incentive amount per target.
  - New TargetIncentiveBuckets table - per-Target, editable over-achievement
    buckets mapping a Qty Sold achievement % threshold (actual / target x
    100) to a multiplier, e.g. 100% -> 1.0x, 110% -> 1.1x, 125% -> 1.25x.
    The highest threshold reached wins; reaching no bucket at all means no
    incentive for that target/period. Final incentive = BaseIncentiveAmount
    x the matching bucket's multiplier.

Nothing existing is dropped, renamed, or overwritten. Safe to run multiple
times. Existing targets get BaseIncentiveAmount=0 and no buckets (no
incentive computed) until you set them on the Targets tab.

Usage: python migrate_target_incentive.py
"""
import db


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def add_column_if_missing(conn, table, column, ddl):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        print(f"  Added {table}.{column}")
        return True
    return False


def run():
    db.init_db()  # creates TargetIncentiveBuckets if missing (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()
    added = 0
    if add_column_if_missing(conn, "Targets", "BaseIncentiveAmount", "REAL NOT NULL DEFAULT 0"):
        added += 1
    conn.commit()

    print("Migration complete.")
    if added:
        print(f"\n{added} new column added, plus the TargetIncentiveBuckets table.")
    else:
        print("\nNo new columns needed - already up to date.")
    print("\nWhat's new:")
    print("- Each Target on the Targets tab now has a Base Incentive (Rs) field, plus an editable")
    print("  table of over-achievement buckets (e.g. 100% of Qty Sold target -> 1.0x, 110% -> 1.1x,")
    print("  125% -> 1.25x). The Targets tab shows the resulting Incentive Rs for each target,")
    print("  computed live from the Qty Sold achievement % and whichever bucket threshold is met.")


if __name__ == "__main__":
    run()
