import sqlite3
import os
import threading
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR is where the actual .db file lives - override it with the DATA_DIR
# environment variable to point at a mounted persistent Volume (e.g. Railway),
# since a platform's regular filesystem is typically wiped on every redeploy/
# restart and this file IS the entire database. Defaults to ./db, same as
# before, for local/dev use.
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "db")
# DB_PATH itself can also be set directly (takes priority over DATA_DIR) -
# this is what's actually configured on Railway right now, left over from
# an earlier deploy setup; kept working rather than requiring a dashboard
# change to switch conventions.
DB_PATH = os.environ.get("DB_PATH") or os.path.join(DATA_DIR, "distributor.db")
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


_local = threading.local()


def _active_conn():
    return getattr(_local, "conn", None)


@contextmanager
def transaction():
    """Wraps a block of query()/execute() calls in ONE atomic SQLite
    transaction - all of it commits together, or none of it does.

    Without this, every query()/execute() call opens its own connection and
    commits immediately (see below) - fine for a single statement, but a
    multi-step business operation (create_sale() posts a dozen-plus separate
    inserts/updates) had no way to roll back if it failed partway through:
    an exception on step 10 of 16 left steps 1-9 permanently committed,
    silently corrupting the ledger. Wrap the whole operation in
    `with db.transaction():` and every query()/execute() inside that block
    (including in functions it calls) automatically joins the same
    transaction instead of opening its own - commit happens once, at the
    end, only if nothing raised; any exception rolls back everything.

    Reentrant: calling this again while already inside one (e.g.
    stock_issue_reconcile() wrapping itself, then calling create_sale(),
    which wraps itself too) just joins the existing transaction rather than
    nesting or committing early - only the OUTERMOST `with` block actually
    commits or rolls back.
    """
    existing = _active_conn()
    if existing is not None:
        yield existing
        return
    conn = get_conn()
    _local.conn = conn
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _local.conn = None
        conn.close()


def query(sql, args=(), one=False):
    conn = _active_conn()
    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    try:
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
    finally:
        # Always close a connection we opened ourselves, even if the query
        # raised (e.g. a transient "database is locked" from another
        # connection writing at the same instant) - otherwise the leaked
        # connection can go on holding a lock of its own and every write
        # after it starts failing too, for no visible reason. A connection
        # borrowed from an active transaction() is left for that block to
        # close.
        if owns_conn:
            conn.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    conn = _active_conn()
    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    try:
        cur = conn.execute(sql, args)
        if owns_conn:
            conn.commit()
        lastid = cur.lastrowid
    finally:
        if owns_conn:
            conn.close()
    return lastid
