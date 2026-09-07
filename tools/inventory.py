"""
Inventory tools. Every price/stock fact the agent ever states must come
from here — never invented.
"""

from db import get_connection, transaction
from tools.idempotency import check_idempotent, save_idempotent


def search_product(query: str):
    """Fuzzy-ish search by name or SKU substring. Returns list of matches."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM products WHERE name LIKE ? OR sku LIKE ? ORDER BY name",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
        return {"ok": True, "matches": [dict(r) for r in rows]}
    finally:
        conn.close()


def add_product(sku, name, unit, cost_price, mrp, gst_rate, hsn_code,
                 qty=0, reorder_level=0, is_loose=False):
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM products WHERE sku = ?", (sku,)
        ).fetchone()
        if existing:
            return {"ok": False, "error": f"SKU '{sku}' already exists (product_id={existing['id']})."}

        cur = conn.execute(
            """INSERT INTO products
               (sku, name, unit, is_loose, cost_price, mrp, qty, reorder_level, hsn_code, gst_rate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sku, name, unit, int(is_loose), cost_price, mrp, qty, reorder_level, hsn_code, gst_rate),
        )
        conn.commit()
        return {"ok": True, "product_id": cur.lastrowid, "name": name, "sku": sku}
    finally:
        conn.close()


def receive_stock(product_id=None, product_name=None, qty=None, cost_price=None,
                   idempotency_key=None):
    """
    Adds stock atomically and logs it to stock_ledger. If cost_price is
    given and differs from the current cost, updates the product's cost
    (simple moving-cost-is-latest-cost model — adequate for a single-shop
    kirana; a weighted-average cost model would be a stretch improvement).
    """
    if qty is None or qty <= 0:
        return {"ok": False, "error": "qty must be a positive number."}

    with transaction() as conn:
        cached = check_idempotent(idempotency_key, conn=conn)
        if cached is not None:
            return cached

        if product_id is not None:
            product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        else:
            product = conn.execute(
                "SELECT * FROM products WHERE name = ? COLLATE NOCASE", (product_name,)
            ).fetchone()

        if not product:
            result = {"ok": False, "error": f"No such product: {product_id or product_name}. "
                                             f"Use add_product first if this is new."}
            save_idempotent(idempotency_key, "receive_stock", result, conn=conn)
            return result

        conn.execute(
            "UPDATE products SET qty = qty + ?, cost_price = COALESCE(?, cost_price) WHERE id = ?",
            (qty, cost_price, product["id"]),
        )
        conn.execute(
            "INSERT INTO stock_ledger (product_id, delta, reason) VALUES (?, ?, 'receive')",
            (product["id"], qty),
        )
        new_qty = conn.execute(
            "SELECT qty FROM products WHERE id = ?", (product["id"],)
        ).fetchone()["qty"]

        result = {
            "ok": True,
            "product_id": product["id"],
            "name": product["name"],
            "received": qty,
            "new_qty": new_qty,
        }
        save_idempotent(idempotency_key, "receive_stock", result, conn=conn)
        return result


def check_stock(product_id=None, product_name=None):
    conn = get_connection()
    try:
        if product_id is not None:
            row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM products WHERE name = ? COLLATE NOCASE", (product_name,)
            ).fetchone()
        if not row:
            return {"ok": False, "error": f"No such product: {product_id or product_name}"}
        return {"ok": True, "product": dict(row)}
    finally:
        conn.close()


def low_stock_report(owner_id=None):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM products WHERE qty <= reorder_level ORDER BY qty ASC"
        ).fetchall()
        return {"ok": True, "low_stock": [dict(r) for r in rows]}
    finally:
        conn.close()
