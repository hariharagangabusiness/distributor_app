"""Schema migration runner.

Tracks which migrate_*.py scripts have already been applied to this
database (the SchemaMigrations table, created by db.init_db()) and only
runs the ones that haven't. Safe to run any time, including repeatedly -
already-applied migrations are always skipped, and the first time this ran
against an existing database it found every migrate_*.py script that
predates this tool already recorded (bootstrapped by db.init_db() without
being re-executed - see db._bootstrap_schema_migrations()).

Replaces the old "figure out which migrate_whatever.py you haven't run yet
and run it by hand" workflow (still documented in RAILWAY_DEPLOY.md's
history) - a new phase that ships a migrate_*.py script now just needs
`python run_migrations.py` run once after deploying, and it's the only
migration that actually executes.

migrate_auth.py is intentionally excluded (see db.MIGRATION_EXCLUDED) -
it's an interactive one-time account-setup script, not a schema migration,
and is still always run by hand.

Usage:
    python run_migrations.py            # apply anything new
    python run_migrations.py --list     # show status only, apply nothing
"""
import glob
import importlib
import os
import sys

from db import DB_PATH, MIGRATION_EXCLUDED
import sqlite3


def main():
    list_only = "--list" in sys.argv
    conn = sqlite3.connect(DB_PATH)
    try:
        applied = {r[0] for r in conn.execute("SELECT Name FROM SchemaMigrations").fetchall()}
        scripts = sorted(
            os.path.basename(p) for p in glob.glob("migrate_*.py")
            if os.path.basename(p) not in MIGRATION_EXCLUDED
        )
        pending = [s for s in scripts if s not in applied]

        print(f"{len(scripts)} migration script(s) known, {len(applied)} already applied, "
              f"{len(pending)} pending.\n")
        if not pending:
            print("Nothing to do.")
            return
        for name in pending:
            print(f"  pending: {name}")
        if list_only:
            return

        print()
        for name in pending:
            print(f"Running {name} ...")
            module = importlib.import_module(name[:-3])  # strip .py
            if not hasattr(module, "main"):
                print(f"  SKIPPED - {name} has no main() function; run it by hand and check RAILWAY_DEPLOY.md.")
                continue
            module.main()
            conn.execute("INSERT INTO SchemaMigrations (Name, Bootstrapped) VALUES (?, 0)", (name,))
            conn.commit()
            print(f"  done - recorded in SchemaMigrations.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
