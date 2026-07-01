"""
db/connection.py

Thin wrapper around psycopg2 so every job imports one consistent way to
get a database connection, rather than each job hardcoding its own
connection logic.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

from config import DATABASE_URL


def get_connection():
    """Returns a raw psycopg2 connection. Caller is responsible for
    closing it (or use get_cursor() below, which handles that for you)."""
    return psycopg2.connect(DATABASE_URL)


@contextmanager
def get_cursor(commit: bool = False):
    """
    Context manager that yields a dict-cursor (rows come back as dicts,
    not tuples - much easier to work with) and handles closing the
    connection automatically.

    Usage:
        with get_cursor(commit=True) as cur:
            cur.execute("INSERT INTO fixtures (...) VALUES (...)")

    Set commit=True for any write operation (INSERT/UPDATE/DELETE).
    Leave as False (default) for read-only SELECT queries.
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
