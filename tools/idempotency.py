import json
from db import get_connection


def check_idempotent(key, conn=None):
    """
    conn: pass an existing connection when called from inside a transaction
    (e.g. finalize_bill) to avoid opening a second connection that would
    deadlock against SQLite's single-writer lock. Opens its own short-lived
    connection otherwise.
    """
    if not key:
        return None
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        row = conn.execute(
            "SELECT result_json FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        if row:
            return json.loads(row["result_json"])
        return None
    finally:
        if owns_conn:
            conn.close()


def save_idempotent(key, tool_name, result, conn=None):
    """See check_idempotent's docstring re: the conn parameter."""
    if not key:
        return
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys (key, tool_name, result_json) VALUES (?, ?, ?)",
            (key, tool_name, json.dumps(result)),
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()
