"""
Billing tools. A bill is a DB row from the moment start_bill runs, so it
survives across messages, and even a /new chat (agent.py can resume a
draft by id if the owner mentions it). Stock is untouched until
finalize_bill — and finalize_bill is where the atomic oversell guard lives.
"""

from db import get_connection, transaction
from tax import compute_tax
from tools.idempotency import check_idempotent, save_idempotent


def start_bill(owner_id, payment_mode=None, customer_name=None):
    conn = get_connection()
    try:
        customer_id = None
        if customer_name:
            row = conn.execute(
                "SELECT id FROM customers WHERE owner_id = ? AND name = ? COLLATE NOCASE",
                (owner_id, customer_name),
            ).fetchone()
            if row:
                customer_id = row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO customers (owner_id, name) VALUES (?, ?)",
                    (owner_id, customer_name),
                )
                customer_id = cur.lastrowid

        cur = conn.execute(
            "INSERT INTO bills (owner_id, status, payment_mode, customer_id) VALUES (?, 'draft', ?, ?)",
            (owner_id, payment_mode, customer_id),
        )
        conn.commit()
        return {"ok": True, "bill_id": cur.lastrowid}
    finally:
        conn.close()


def _resolve_product(conn, product_id, product_name):
    if product_id is not None:
        return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return conn.execute(
        "SELECT * FROM products WHERE name = ? COLLATE NOCASE", (product_name,)
    ).fetchone()


def add_bill_item(bill_id, qty, product_id=None, product_name=None):
    conn = get_connection()
    try:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill:
            return {"ok": False, "error": f"No such bill: {bill_id}"}
        if bill["status"] != "draft":
            return {"ok": False, "error": f"Bill {bill_id} is {bill['status']}, not editable."}

        product = _resolve_product(conn, product_id, product_name)
        if not product:
            return {"ok": False, "error": f"No such product: {product_id or product_name}. "
                                           f"Ask the owner to confirm which product they mean, "
                                           f"or add it first — never guess a price."}

        # Soft, real-time check. Authoritative enforcement happens atomically
        # in finalize_bill; this just gives the owner fast feedback while
        # building the bill.
        already_in_bill = conn.execute(
            "SELECT COALESCE(SUM(qty), 0) as q FROM bill_items WHERE bill_id = ? AND product_id = ?",
            (bill_id, product["id"]),
        ).fetchone()["q"]
        if product["qty"] < already_in_bill + qty:
            return {
                "ok": False,
                "error": f"Only {product['qty']} {product['unit']} of {product['name']} in stock "
                         f"(bill already has {already_in_bill}, tried to add {qty}). Refused.",
            }

        cur = conn.execute(
            """INSERT INTO bill_items (bill_id, product_id, qty, unit_price, gst_rate)
               VALUES (?, ?, ?, ?, ?)""",
            (bill_id, product["id"], qty, product["mrp"], product["gst_rate"]),
        )
        conn.commit()
        return {
            "ok": True,
            "item_id": cur.lastrowid,
            "product": product["name"],
            "qty": qty,
            "unit_price": product["mrp"],
        }
    finally:
        conn.close()


def remove_bill_item(bill_id, item_id):
    conn = get_connection()
    try:
        bill = conn.execute("SELECT status FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill or bill["status"] != "draft":
            return {"ok": False, "error": "Bill not found or not editable."}
        conn.execute("DELETE FROM bill_items WHERE id = ? AND bill_id = ?", (item_id, bill_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def update_bill_item(bill_id, item_id, new_qty):
    conn = get_connection()
    try:
        bill = conn.execute("SELECT status FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill or bill["status"] != "draft":
            return {"ok": False, "error": "Bill not found or not editable."}
        item = conn.execute(
            "SELECT * FROM bill_items WHERE id = ? AND bill_id = ?", (item_id, bill_id)
        ).fetchone()
        if not item:
            return {"ok": False, "error": "No such line item on this bill."}
        product = conn.execute("SELECT * FROM products WHERE id = ?", (item["product_id"],)).fetchone()
        if product["qty"] < new_qty:
            return {"ok": False, "error": f"Only {product['qty']} {product['unit']} in stock. Refused."}
        conn.execute("UPDATE bill_items SET qty = ? WHERE id = ?", (new_qty, item_id))
        conn.commit()
        return {"ok": True, "item_id": item_id, "new_qty": new_qty}
    finally:
        conn.close()


def get_draft_bill(bill_id):
    conn = get_connection()
    try:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill:
            return {"ok": False, "error": f"No such bill: {bill_id}"}
        items = conn.execute(
            """SELECT bi.*, p.name as product_name, p.unit
               FROM bill_items bi JOIN products p ON p.id = bi.product_id
               WHERE bi.bill_id = ?""",
            (bill_id,),
        ).fetchall()
        item_dicts = [dict(i) for i in items]
        tax = compute_tax([
            {"unit_price": i["unit_price"], "qty": i["qty"], "gst_rate": i["gst_rate"],
             "name": i["product_name"]}
            for i in item_dicts
        ]) if item_dicts else None
        receipt_text = None
        if tax:
            receipt_text = format_receipt(bill_id, bill["payment_mode"], tax, bill["payment_ref"])
        return {"ok": True, "bill": dict(bill), "items": item_dicts, "tax_preview": tax,
                "receipt_text": receipt_text}
    finally:
        conn.close()


def format_receipt(bill_id, payment_mode, tax, payment_ref=None):
    """Plain-text GST bill for Telegram — no markdown, no emoji."""
    rows = [
        "TAX INVOICE",
        f"Bill no. {bill_id}",
        "--------------------------------",
    ]
    for line in tax.get("lines") or []:
        name = line.get("name") or line.get("product_name") or "Item"
        qty = line["qty"]
        rate = line["unit_price"]
        total = line["line_total"]
        gst = line.get("gst_rate") or 0
        hsn = line.get("hsn_code") or "-"
        rows.append(name)
        rows.append(f"  {qty:g} x Rs {rate:.2f} = Rs {total:.2f}")
        rows.append(f"  GST {gst:g}%  HSN {hsn}")
    rows.append("--------------------------------")
    rows.append(f"Taxable value     Rs {tax['subtotal_taxable']:.2f}")
    rows.append(f"CGST              Rs {tax['total_cgst']:.2f}")
    rows.append(f"SGST              Rs {tax['total_sgst']:.2f}")
    if tax.get("round_off"):
        rows.append(f"Round off         Rs {tax['round_off']:.2f}")
    rows.append(f"Grand total       Rs {tax['grand_total']:.2f}")
    rows.append("--------------------------------")
    pay = (payment_mode or "-").upper()
    rows.append(f"Payment: {pay}")
    if payment_ref:
        rows.append(f"Ref: {payment_ref}")
    return "\n".join(rows)


def finalize_bill(bill_id, payment_mode, payment_ref=None, idempotency_key=None, confirmed=False):
    """
    The atomic oversell guard lives here. All stock decrements for every
    line item happen inside ONE transaction: if any single item can't be
    satisfied, the whole finalize is rolled back and refused — no partial
    bills, no partially-decremented stock.
    """
    cached = check_idempotent(idempotency_key)
    if cached is not None:
        return cached

    try:
        return _finalize_bill_inner(bill_id, payment_mode, payment_ref, idempotency_key, confirmed)
    except OversellError as e:
        result = {"ok": False, "error": str(e)}
        save_idempotent(idempotency_key, "finalize_bill", result)
        return result


def _finalize_bill_inner(bill_id, payment_mode, payment_ref, idempotency_key, confirmed):
    with transaction() as conn:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill:
            result = {"ok": False, "error": f"No such bill: {bill_id}"}
            save_idempotent(idempotency_key, "finalize_bill", result, conn=conn)
            return result
        if bill["status"] != "draft":
            result = {"ok": False, "error": f"Bill {bill_id} is already {bill['status']}."}
            save_idempotent(idempotency_key, "finalize_bill", result, conn=conn)
            return result

        items = conn.execute(
            """SELECT bi.*, p.name as product_name, p.unit, p.cost_price, p.hsn_code
               FROM bill_items bi JOIN products p ON p.id = bi.product_id
               WHERE bi.bill_id = ?""",
            (bill_id,),
        ).fetchall()
        if not items:
            result = {"ok": False, "error": "Bill has no items."}
            save_idempotent(idempotency_key, "finalize_bill", result, conn=conn)
            return result

        # Below-cost guardrail — needs explicit confirmation, not a silent sale.
        below_cost = [i for i in items if i["unit_price"] < i["cost_price"]]
        if below_cost and not confirmed:
            result = {
                "ok": False,
                "confirmation_required": True,
                "reason": f"{[i['product_name'] for i in below_cost]} would sell below cost price. "
                          f"Confirm to proceed anyway.",
            }
            return result  # not cached — this isn't a final outcome yet

        # --- Atomic oversell guard: one conditional UPDATE per line item ---
        for item in items:
            cur = conn.execute(
                "UPDATE products SET qty = qty - ? WHERE id = ? AND qty >= ?",
                (item["qty"], item["product_id"], item["qty"]),
            )
            if cur.rowcount == 0:
                current = conn.execute(
                    "SELECT qty FROM products WHERE id = ?", (item["product_id"],)
                ).fetchone()["qty"]
                # Raising here triggers the transaction() context manager's
                # ROLLBACK, undoing any earlier line items in this same loop.
                raise OversellError(
                    f"Only {current} {item['unit']} of {item['product_name']} left "
                    f"(bill wants {item['qty']}). Sale refused, nothing was billed."
                )
            conn.execute(
                "INSERT INTO stock_ledger (product_id, delta, reason, ref_bill_id) VALUES (?, ?, 'sale', ?)",
                (item["product_id"], -item["qty"], bill_id),
            )

        tax = compute_tax([
            {"unit_price": i["unit_price"], "qty": i["qty"], "gst_rate": i["gst_rate"],
             "name": i["product_name"], "hsn_code": i["hsn_code"]}
            for i in items
        ])

        for line in tax["lines"]:
            conn.execute(
                """UPDATE bill_items SET taxable_value = ?, cgst = ?, sgst = ?, line_total = ?
                   WHERE bill_id = ? AND product_id = (SELECT id FROM products WHERE name = ?)""",
                (line["taxable_value"], line["cgst"], line["sgst"], line["line_total"],
                 bill_id, line["name"]),
            )

        conn.execute(
            """UPDATE bills SET status = 'finalized', payment_mode = ?, payment_ref = ?,
               finalized_at = datetime('now') WHERE id = ?""",
            (payment_mode, payment_ref, bill_id),
        )

        if payment_mode == "credit":
            if not bill["customer_id"]:
                raise OversellError("Credit sale requires a customer name — none was set on this bill.")
            add_khata_charge_in_conn(conn, bill["customer_id"], tax["grand_total"], bill_id)

        result = {
            "ok": True,
            "bill_id": bill_id,
            "grand_total": tax["grand_total"],
            "total_cgst": tax["total_cgst"],
            "total_sgst": tax["total_sgst"],
            "lines": tax["lines"],
            "receipt_text": format_receipt(bill_id, payment_mode, tax, payment_ref),
        }
        save_idempotent(idempotency_key, "finalize_bill", result, conn=conn)
        return result


def add_khata_charge_in_conn(conn, customer_id, amount, bill_id):
    """Khata charge inside the same finalize_bill transaction (credit sales)."""
    conn.execute(
        "INSERT INTO khata_ledger (customer_id, type, amount, ref_bill_id) VALUES (?, 'charge', ?, ?)",
        (customer_id, amount, bill_id),
    )


class OversellError(Exception):
    pass
