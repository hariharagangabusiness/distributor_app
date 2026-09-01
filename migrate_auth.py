"""One-time migration: adds individual staff login accounts (Users table)
to an EXISTING database without touching any data you've already entered.

The app now requires signing in before the dashboard (or any other page)
is reachable. This script creates the Users table and, if no accounts
exist yet, walks you through creating the first one - an Admin account,
since someone has to be able to create everyone else's login from the
Manage Users screen afterwards.

Safe to run multiple times: if accounts already exist, it does nothing
but confirm the table is present.

Usage: python migrate_auth.py

Non-interactive setup (e.g. scripted deployment): set the environment
variables ADMIN_USERNAME and ADMIN_PASSWORD before running this script
and it will use those instead of prompting.
"""
import os
import sys
import getpass

import db
from werkzeug.security import generate_password_hash


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def add_column_if_missing(conn, table, column, coltype_and_default):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_and_default}")
        print(f"  Added {table}.{column}")


def run():
    db.init_db()  # creates Users if missing (CREATE TABLE IF NOT EXISTS)
    conn = db.get_conn()

    # If you built your database with an even earlier version of this app
    # that already had a dormant, unused Users table (before login was
    # actually wired up), it won't have these columns yet - add them
    # without touching any accounts that happen to already be in there.
    add_column_if_missing(conn, "Users", "FullName", "TEXT")
    add_column_if_missing(conn, "Users", "CreatedAt", "TEXT")
    add_column_if_missing(conn, "Users", "LastLoginAt", "TEXT")
    conn.commit()

    existing = conn.execute("SELECT COUNT(*) AS c FROM Users").fetchone()["c"]
    if existing:
        conn.close()
        print(f"Users table already has {existing} account(s) - nothing to do.")
        print("Manage accounts from the app: log in, then go to Manage Users.")
        return

    print("No login accounts exist yet. Let's create the first one - it will")
    print("be an Admin account, able to create logins for everyone else.\n")

    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")

    if not username or not password:
        if not sys.stdin.isatty():
            # Non-interactive run with no env vars set: create a clearly
            # marked default account rather than failing outright, so
            # automated setups don't get stuck - but this MUST be changed
            # immediately.
            username = username or "admin"
            password = password or "changeme123"
            print("WARNING: running non-interactively with no ADMIN_USERNAME/")
            print("ADMIN_PASSWORD set - creating a DEFAULT account:")
            print(f"    username: {username}")
            print(f"    password: {password}")
            print("Log in and change this password immediately (Change Password,")
            print("top-right menu after logging in).\n")
        else:
            while not username:
                username = input("Choose an admin username: ").strip()
            while not password or len(password) < 6:
                password = getpass.getpass("Choose an admin password (min 6 characters): ")
                if len(password) < 6:
                    print("  Too short - please use at least 6 characters.")
                    password = ""
            confirm = getpass.getpass("Confirm password: ")
            if confirm != password:
                conn.close()
                print("\nPasswords did not match - nothing was created. Run this script again.")
                return

    full_name = username
    if sys.stdin.isatty() and not os.environ.get("ADMIN_USERNAME"):
        entered_name = input(f"Full name (optional, Enter to use '{username}'): ").strip()
        if entered_name:
            full_name = entered_name

    conn.execute(
        "INSERT INTO Users (Username, PasswordHash, FullName, Role, Active) VALUES (?,?,?,'Admin',1)",
        (username, generate_password_hash(password), full_name),
    )
    conn.commit()
    conn.close()

    print(f"\nAdmin account '{username}' created.")
    print("\nMigration complete. New capabilities now available:")
    print("  - A landing page at the app's home address, with a Login button.")
    print("  - Every other page now requires signing in.")
    print("  - Manage Users (visible to Admins, under Setup) to add a login")
    print("    for each staff member, deactivate one, or reset a password.")
    print("  - Each signed-in user can change their own password from the")
    print("    account menu in the top-right corner.")


if __name__ == "__main__":
    run()
