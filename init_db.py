"""Run this once to (re)create the database structure.
Usage: python init_db.py
Safe to re-run: it only creates tables/indexes that don't already exist,
it never deletes data.
"""
import db

if __name__ == "__main__":
    db.init_db()
    print(f"Database ready at: {db.DB_PATH}")
    print("\nNext: run 'python migrate_auth.py' to create your first login "
          "(the app requires signing in before use).")
