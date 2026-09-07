"""
Real artifact generation: a GST-correct PDF invoice from a finalized bill's
actual DB rows, and a PPTX analysis deck with real matplotlib charts fed by
analytics.py. Neither is a screenshot or a static template filled with
placeholder text.
"""

import os
import tempfile

from db import get_connection
from tools import analytics, preferences


def _register_rupee_font():
    """
    ReportLab's built-in fonts (Helvetica etc.) don't include the ₹ glyph
    (U+20B9) and silently render it as a black box. DejaVu Sans does, and
    ships on most Linux distros (Debian/Ubuntu base images used for
    deployment). Falls back to writing "Rs." instead of "₹" if no such
    font is found, rather than shipping invoices with broken glyphs.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    if not os.path.exists(candidates[0]):
        return None  # caller falls back to "Rs."

    pdfmetrics.registerFont(TTFont("DejaVuSans", candidates[0]))
    if os.path.exists(candidates[1]):
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", candidates[1]))
    return "DejaVuSans"


def generate_invoice_pdf(bill_id, owner_id=None, out_dir=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    rupee_font = _register_rupee_font()
    currency = "₹" if rupee_font else "Rs. "

    conn = get_connection()
    try:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not bill or bill["status"] != "finalized":
            return {"ok": False, "error": f"Bill {bill_id} not found or not finalized yet."}

        items = conn.execute(
            """SELECT bi.*, p.name as product_name, p.unit, p.hsn_code
               FROM bill_items bi JOIN products p ON p.id = bi.product_id
               WHERE bi.bill_id = ?""",
            (bill_id,),
        ).fetchall()

        customer_name = None
        if bill["customer_id"]:
            c = conn.execute("SELECT name FROM customers WHERE id = ?", (bill["customer_id"],)).fetchone()
            customer_name = c["name"] if c else None
    finally:
        conn.close()

    shop_name = "Kirana Store"
    gstin = None
    if owner_id:
        prefs = preferences.get_all_preferences(owner_id)
        shop_name = prefs.get("shop_name", shop_name)
        gstin = prefs.get("gstin")

    out_dir = out_dir or tempfile.gettempdir()
    path = os.path.join(out_dir, f"invoice_{bill_id}.pdf")

    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    if rupee_font:
        for style_name in ("Normal", "Title", "Heading2", "Heading3"):
            styles[style_name].fontName = rupee_font
    story = []

    story.append(Paragraph(f"<b>{shop_name}</b>", styles["Title"]))
    if gstin:
        story.append(Paragraph(f"GSTIN: {gstin}", styles["Normal"]))
    story.append(Paragraph(f"Tax Invoice #{bill_id}", styles["Heading2"]))
    story.append(Paragraph(f"Date: {bill['finalized_at']}", styles["Normal"]))
    story.append(Paragraph(f"Payment: {bill['payment_mode'].upper() if bill['payment_mode'] else '-'}"
                            + (f" | Customer (Khata): {customer_name}" if customer_name else ""),
                            styles["Normal"]))
    story.append(Spacer(1, 10))

    table_data = [["Item", "HSN", "Qty", "Rate (incl. GST)", "GST%", "Taxable", "CGST", "SGST", "Total"]]
    total_taxable = total_cgst = total_sgst = total_amount = 0.0
    for item in items:
        table_data.append([
            item["product_name"], item["hsn_code"] or "-", f"{item['qty']:g}",
            f"{currency}{item['unit_price']:.2f}", f"{item['gst_rate']:g}%",
            f"{currency}{item['taxable_value']:.2f}", f"{currency}{item['cgst']:.2f}",
            f"{currency}{item['sgst']:.2f}", f"{currency}{item['line_total']:.2f}",
        ])
        total_taxable += item["taxable_value"]
        total_cgst += item["cgst"]
        total_sgst += item["sgst"]
        total_amount += item["line_total"]

    table_data.append(["", "", "", "", "TOTAL",
                        f"{currency}{total_taxable:.2f}", f"{currency}{total_cgst:.2f}",
                        f"{currency}{total_sgst:.2f}", f"{currency}{total_amount:.2f}"])

    table_font = rupee_font or "Helvetica"
    table_font_bold = f"{rupee_font}-Bold" if rupee_font and os.path.exists(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") else "Helvetica-Bold"

    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d5f3f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), table_font),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0f0f0")),
        ("FONTNAME", (0, -1), (-1, -1), table_font_bold),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"<b>Grand Total: {currency}{total_amount:.2f}</b>", styles["Heading3"]))

    doc.build(story)
    return {"ok": True, "path": path, "grand_total": round(total_amount, 2)}


def generate_analysis_pptx(owner_id, period="week", out_dir=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pptx import Presentation
    from pptx.util import Inches, Pt

    top = analytics.top_items(owner_id, period=period, n=5)["items"]
    summary = analytics.sales_summary(owner_id, period=period)
    gst = analytics.gst_collected_report(owner_id, period=period)

    from tools.inventory import low_stock_report
    low_stock = low_stock_report(owner_id)["low_stock"]

    out_dir = out_dir or tempfile.gettempdir()

    # Chart 1: top items by revenue
    chart1_path = os.path.join(out_dir, "chart_top_items.png")
    fig, ax = plt.subplots(figsize=(7, 4))
    names = [i["name"] for i in top] or ["No sales yet"]
    revenues = [i["revenue"] for i in top] or [0]
    ax.barh(names, revenues, color="#2d5f3f")
    ax.set_xlabel("Revenue (₹)")
    ax.set_title(f"Top items by revenue — {period}")
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(chart1_path, dpi=150)
    plt.close(fig)

    # Chart 2: GST split
    chart2_path = os.path.join(out_dir, "chart_gst.png")
    fig, ax = plt.subplots(figsize=(5, 4))
    if gst["total_gst"] > 0:
        ax.pie([gst["cgst"], gst["sgst"]], labels=["CGST", "SGST"], autopct="%1.1f%%",
               colors=["#2d5f3f", "#8fbf9f"])
    else:
        ax.text(0.5, 0.5, "No GST collected yet", ha="center")
        ax.axis("off")
    ax.set_title(f"GST collected — {period}")
    fig.savefig(chart2_path, dpi=150)
    plt.close(fig)

    prs = Presentation()

    # Title slide
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Store Analysis"
    slide.placeholders[1].text = f"Period: {period} | {summary['bill_count']} bills | ₹{summary['total_sales']:.2f} sales"

    # Top items slide
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Top Selling Items"
    slide.shapes.add_picture(chart1_path, Inches(1), Inches(1.3), width=Inches(8))

    # GST slide
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = f"GST Collected: ₹{gst['total_gst']:.2f}"
    slide.shapes.add_picture(chart2_path, Inches(2.5), Inches(1.3), width=Inches(5))

    # Stock health slide
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Stock Health — Reorder Soon"
    body = slide.placeholders[1].text_frame
    body.clear()
    if low_stock:
        for i, item in enumerate(low_stock[:8]):
            p = body.paragraphs[0] if i == 0 else body.add_paragraph()
            p.text = f"{item['name']}: {item['qty']} {item['unit']} left (reorder at {item['reorder_level']})"
            p.font.size = Pt(16)
    else:
        body.text = "All stock levels healthy."

    path = os.path.join(out_dir, f"analysis_{owner_id}_{period}.pptx")
    prs.save(path)
    return {"ok": True, "path": path}
