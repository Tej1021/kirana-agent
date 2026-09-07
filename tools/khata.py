from db import get_connection, transaction
from tools.idempotency import check_idempotent, save_idempotent


def get_or_create_customer(owner_id, name, phone=None):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM customers WHERE owner_id = ? AND name = ? COLLATE NOCASE",
            (owner_id, name),
        ).fetchone()
        if row:
            return {"ok": True, "customer_id": row["id"], "name": row["name"], "created": False}
        cur = conn.execute(
            "INSERT INTO customers (owner_id, name, phone) VALUES (?, ?, ?)", (owner_id, name, phone)
        )
        conn.commit()
        return {"ok": True, "customer_id": cur.lastrowid, "name": name, "created": True}
    finally:
        conn.close()


def _balance(conn, customer_id):
    row = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN type='charge' THEN amount ELSE -amount END), 0) as bal
           FROM khata_ledger WHERE customer_id = ?""",
        (customer_id,),
    ).fetchone()
    return row["bal"]


def add_khata_charge(owner_id, customer_name, amount, ref_bill_id=None, idempotency_key=None):
    if amount <= 0:
        return {"ok": False, "error": "Amount must be positive."}

    with transaction() as conn:
        cached = check_idempotent(idempotency_key, conn=conn)
        if cached is not None:
            return cached

        customer = conn.execute(
            "SELECT * FROM customers WHERE owner_id = ? AND name = ? COLLATE NOCASE",
            (owner_id, customer_name),
        ).fetchone()
        if not customer:
            cur = conn.execute(
                "INSERT INTO customers (owner_id, name) VALUES (?, ?)", (owner_id, customer_name)
            )
            customer_id = cur.lastrowid
        else:
            customer_id = customer["id"]

        conn.execute(
            "INSERT INTO khata_ledger (customer_id, type, amount, ref_bill_id) VALUES (?, 'charge', ?, ?)",
            (customer_id, amount, ref_bill_id),
        )
        new_balance = _balance(conn, customer_id)
        result = {"ok": True, "customer": customer_name, "charged": amount, "new_balance": new_balance}
        save_idempotent(idempotency_key, "add_khata_charge", result, conn=conn)
        return result


def add_khata_payment(owner_id, customer_name, amount, idempotency_key=None):
    """
    Guardrail: refuses to record a payment against a customer who doesn't
    exist yet — unlike a charge, a payment can never legitimately create a
    new khata account, so silently auto-creating here would mask a typo'd
    customer name.
    """
    if amount <= 0:
        return {"ok": False, "error": "Amount must be positive."}

    with transaction() as conn:
        cached = check_idempotent(idempotency_key, conn=conn)
        if cached is not None:
            return cached

        customer = conn.execute(
            "SELECT * FROM customers WHERE owner_id = ? AND name = ? COLLATE NOCASE",
            (owner_id, customer_name),
        ).fetchone()
        if not customer:
            result = {
                "ok": False,
                "error": f"No khata account for '{customer_name}'. Confirm the name, "
                         f"or charge them first if this is their first transaction.",
            }
            save_idempotent(idempotency_key, "add_khata_payment", result, conn=conn)
            return result

        conn.execute(
            "INSERT INTO khata_ledger (customer_id, type, amount) VALUES (?, 'payment', ?)",
            (customer["id"], amount),
        )
        new_balance = _balance(conn, customer["id"])
        result = {"ok": True, "customer": customer_name, "paid": amount, "new_balance": new_balance}
        save_idempotent(idempotency_key, "add_khata_payment", result, conn=conn)
        return result


def get_khata_balance(owner_id, customer_name):
    conn = get_connection()
    try:
        customer = conn.execute(
            "SELECT * FROM customers WHERE owner_id = ? AND name = ? COLLATE NOCASE",
            (owner_id, customer_name),
        ).fetchone()
        if not customer:
            return {"ok": False, "error": f"No khata account for '{customer_name}'."}
        balance = _balance(conn, customer["id"])
        return {"ok": True, "customer": customer_name, "balance": balance}
    finally:
        conn.close()


def list_debtors(owner_id):
    conn = get_connection()
    try:
        customers = conn.execute("SELECT * FROM customers WHERE owner_id = ?", (owner_id,)).fetchall()
        debtors = []
        for c in customers:
            bal = _balance(conn, c["id"])
            if bal > 0:
                debtors.append({"name": c["name"], "balance": bal})
        debtors.sort(key=lambda d: -d["balance"])
        return {"ok": True, "debtors": debtors}
    finally:
        conn.close()
