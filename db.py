import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("KIRANA_DB_PATH", "kirana.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,               -- kg | g | litre | ml | packet | dozen | piece
    is_loose INTEGER NOT NULL DEFAULT 0,
    cost_price REAL NOT NULL,
    mrp REAL NOT NULL,
    qty REAL NOT NULL DEFAULT 0,
    reorder_level REAL NOT NULL DEFAULT 0,
    hsn_code TEXT,
    gst_rate REAL NOT NULL DEFAULT 0, -- e.g. 0, 5, 12, 18
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stock_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    delta REAL NOT NULL,              -- +ve = received, -ve = sold
    reason TEXT NOT NULL,             -- 'receive' | 'sale' | 'adjustment'
    ref_bill_id INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT,
    UNIQUE(owner_id, name COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS khata_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    type TEXT NOT NULL,               -- 'charge' | 'payment'
    amount REAL NOT NULL,
    ref_bill_id INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | finalized | void
    payment_mode TEXT,                       -- cash | upi | card | credit
    payment_ref TEXT,
    customer_id INTEGER REFERENCES customers(id),
    created_at TEXT DEFAULT (datetime('now')),
    finalized_at TEXT
);

CREATE TABLE IF NOT EXISTS bill_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty REAL NOT NULL,
    unit_price REAL NOT NULL,
    gst_rate REAL NOT NULL,
    taxable_value REAL,
    cgst REAL,
    sgst REAL,
    line_total REAL
);

CREATE TABLE IF NOT EXISTS preferences (
    owner_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (owner_id, key)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def transaction():
    """
    Context manager giving a connection inside an explicit BEGIN IMMEDIATE
    transaction. Use this for every mutation that touches products, bills,
    or khata_ledger, so concurrent writers serialize correctly.
    """
    conn = get_connection()
    conn.isolation_level = None  # manual transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized schema at {DB_PATH}")
