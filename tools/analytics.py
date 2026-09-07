"""
Analytics tools — everything here reads from bill_items / stock_ledger.
Nothing is hardcoded; the PPTX generator (documents.py) calls these for
its chart data.
"""

from db import get_connection


def _period_bounds(period):
    """period: 'today' | 'week' | 'month' -> SQL WHERE fragment on bills.finalized_at"""
    if period == "today":
        return "date(finalized_at) = date('now')"
    if period == "week":
        return "finalized_at >= date('now', '-7 days')"
    if period == "month":
        return "finalized_at >= date('now', '-30 days')"
    return "1=1"


def daily_close(owner_id, date=None):
    conn = get_connection()
    try:
        date_filter = "date(finalized_at) = date(?)" if date else "date(finalized_at) = date('now')"
        params = (owner_id, date) if date else (owner_id,)

        bills = conn.execute(
            f"""SELECT b.*, 
                   (SELECT COALESCE(SUM(line_total),0) FROM bill_items WHERE bill_id=b.id) as total,
                   (SELECT COALESCE(SUM(cgst),0) FROM bill_items WHERE bill_id=b.id) as cgst,
                   (SELECT COALESCE(SUM(sgst),0) FROM bill_items WHERE bill_id=b.id) as sgst
               FROM bills b
               WHERE b.owner_id = ? AND b.status = 'finalized' AND {date_filter}""",
            params,
        ).fetchall()

        total_sales = sum(b["total"] for b in bills)
        total_cgst = sum(b["cgst"] for b in bills)
        total_sgst = sum(b["sgst"] for b in bills)
        by_mode = {}
        for b in bills:
            by_mode[b["payment_mode"]] = by_mode.get(b["payment_mode"], 0) + b["total"]

        top = conn.execute(
            f"""SELECT p.name, SUM(bi.qty) as qty_sold, SUM(bi.line_total) as revenue
                FROM bill_items bi
                JOIN bills b ON b.id = bi.bill_id
                JOIN products p ON p.id = bi.product_id
                WHERE b.owner_id = ? AND b.status = 'finalized' AND {date_filter}
                GROUP BY p.id ORDER BY revenue DESC LIMIT 5""",
            params,
        ).fetchall()

        return {
            "ok": True,
            "bill_count": len(bills),
            "total_sales": round(total_sales, 2),
            "total_gst_collected": round(total_cgst + total_sgst, 2),
            "cgst": round(total_cgst, 2),
            "sgst": round(total_sgst, 2),
            "by_payment_mode": by_mode,
            "top_items": [dict(t) for t in top],
        }
    finally:
        conn.close()


def sales_summary(owner_id, period="week"):
    conn = get_connection()
    try:
        where = _period_bounds(period)
        row = conn.execute(
            f"""SELECT COUNT(*) as bill_count,
                       COALESCE(SUM((SELECT SUM(line_total) FROM bill_items WHERE bill_id=b.id)),0) as total
                FROM bills b WHERE owner_id = ? AND status='finalized' AND {where}""",
            (owner_id,),
        ).fetchone()
        return {"ok": True, "period": period, "bill_count": row["bill_count"], "total_sales": round(row["total"], 2)}
    finally:
        conn.close()


def top_items(owner_id, period="week", n=5):
    conn = get_connection()
    try:
        where = _period_bounds(period)
        rows = conn.execute(
            f"""SELECT p.name, SUM(bi.qty) as qty_sold, SUM(bi.line_total) as revenue
                FROM bill_items bi JOIN bills b ON b.id = bi.bill_id JOIN products p ON p.id = bi.product_id
                WHERE b.owner_id = ? AND b.status='finalized' AND {where}
                GROUP BY p.id ORDER BY revenue DESC LIMIT ?""",
            (owner_id, n),
        ).fetchall()
        return {"ok": True, "period": period, "items": [dict(r) for r in rows]}
    finally:
        conn.close()


def gst_collected_report(owner_id, period="week"):
    conn = get_connection()
    try:
        where = _period_bounds(period)
        row = conn.execute(
            f"""SELECT COALESCE(SUM(bi.cgst),0) as cgst, COALESCE(SUM(bi.sgst),0) as sgst
                FROM bill_items bi JOIN bills b ON b.id = bi.bill_id
                WHERE b.owner_id = ? AND b.status='finalized' AND {where}""",
            (owner_id,),
        ).fetchone()
        return {"ok": True, "period": period, "cgst": round(row["cgst"], 2), "sgst": round(row["sgst"], 2),
                "total_gst": round(row["cgst"] + row["sgst"], 2)}
    finally:
        conn.close()
