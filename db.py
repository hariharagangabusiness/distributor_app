import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Override via env var to point at a mounted persistent volume (e.g. on
# Railway, where anything outside the volume's mount path is wiped on
# every redeploy). Defaults to the in-repo path for local/LAN/VPS use.
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "db", "distributor.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "db", "schema.sql")


def get_conn():
    # timeout/busy_timeout: if another connection briefly holds a write
    # lock (a few staff on the same office network saving at once), wait
    # up to 10s instead of failing immediately with "database is locked".
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def query(sql, args=(), one=False):
    conn = get_conn()
    try:
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
    finally:
        # Always close, even if the query raised (e.g. a transient
        # "database is locked" from another connection writing at the
        # same instant) - otherwise the leaked connection can go on
        # holding a lock of its own and every write after it starts
        # failing too, for no visible reason.
        conn.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    conn = get_conn()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        lastid = cur.lastrowid
    finally:
        conn.close()
    return lastid
