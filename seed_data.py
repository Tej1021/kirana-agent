"""
Seeds the DB with real SKUs and realistic GST slabs:
- 0%: loose staples (rice, dal, fresh atta by weight)
- 5%: packaged staples (branded atta, salt, packaged rice)
- 12-18%: FMCG (soaps, chocolates, some processed foods)
"""

from db import init_db, get_connection

PRODUCTS = [
    # sku, name, unit, is_loose, cost_price, mrp, qty, reorder_level, hsn_code, gst_rate
    ("AASH-ATTA-5KG", "Aashirvaad Atta 5kg", "packet", 0, 220, 260, 40, 10, "1101", 5),
    ("TATA-SALT-1KG", "Tata Salt 1kg", "packet", 0, 18, 24, 100, 20, "2501", 5),
    ("AMUL-BUTTER-100G", "Amul Butter 100g", "packet", 0, 48, 62, 30, 10, "0405", 12),
    ("FORT-OIL-1L", "Fortune Sunflower Oil 1L", "packet", 0, 118, 145, 25, 8, "1512", 5),
    ("MAGGI-70G", "Maggi 70g", "packet", 0, 10, 14, 80, 20, "1902", 12),
    ("PARLE-G", "Parle-G", "packet", 0, 8, 10, 100, 25, "1905", 18),
    ("SURF-EXCEL-1KG", "Surf Excel 1kg", "packet", 0, 95, 130, 20, 5, "3402", 18),
    ("SUGAR-LOOSE-KG", "Sugar (loose)", "kg", 1, 38, 45, 50, 10, "1701", 0),
    ("RICE-LOOSE-KG", "Rice (loose)", "kg", 1, 32, 40, 60, 15, "1006", 0),
    ("DAL-TOOR-LOOSE-KG", "Toor Dal (loose)", "kg", 1, 95, 115, 30, 8, "0713", 0),
]


def seed():
    init_db()
    conn = get_connection()
    try:
        for sku, name, unit, is_loose, cost, mrp, qty, reorder, hsn, gst in PRODUCTS:
            existing = conn.execute("SELECT id FROM products WHERE sku = ?", (sku,)).fetchone()
            if existing:
                print(f"skip (exists): {name}")
                continue
            conn.execute(
                """INSERT INTO products
                   (sku, name, unit, is_loose, cost_price, mrp, qty, reorder_level, hsn_code, gst_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sku, name, unit, is_loose, cost, mrp, qty, reorder, hsn, gst),
            )
            print(f"added: {name}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
