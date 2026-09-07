"""
Preferences: standing owner settings (default payment mode, preferred
brand, shop name/GSTIN for invoices) that persist across chats. Keyed by
owner_id (the Telegram user id), NOT by chat/conversation id — this is
what makes them survive a /new chat.
"""

from db import get_connection


def set_preference(owner_id, key, value):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO preferences (owner_id, key, value, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(owner_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (owner_id, key, str(value)),
        )
        conn.commit()
        return {"ok": True, "key": key, "value": value}
    finally:
        conn.close()


def get_preference(owner_id, key):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM preferences WHERE owner_id = ? AND key = ?", (owner_id, key)
        ).fetchone()
        return {"ok": True, "value": row["value"] if row else None}
    finally:
        conn.close()


def get_all_preferences(owner_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM preferences WHERE owner_id = ?", (owner_id,)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()
