from decimal import Decimal, ROUND_HALF_UP


def round2(value):
    """Round to paise (2 decimals), half-up, returned as float."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round_rupee(value):
    """Round an invoice grand total to the nearest whole rupee, half-up."""
    return float(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_line_tax(unit_price: float, qty: float, gst_rate: float):
    """
    Compute the tax breakup for a single bill line.

    GST-inclusive pricing convention: `unit_price` is the MRP/selling price
    already inclusive of GST (standard for Indian retail shelf pricing).
    We back out the taxable value, then split GST equally into CGST + SGST.

    Returns a dict with taxable_value, cgst, sgst, line_total (all rounded
    to paise) plus the raw gst_rate for display on the invoice.
    """
    gross = unit_price * qty
    if gst_rate and gst_rate > 0:
        taxable_value = gross / (1 + gst_rate / 100)
        total_tax = gross - taxable_value
        cgst = total_tax / 2
        sgst = total_tax / 2
    else:
        taxable_value = gross
        cgst = 0.0
        sgst = 0.0

    return {
        "gst_rate": gst_rate,
        "taxable_value": round2(taxable_value),
        "cgst": round2(cgst),
        "sgst": round2(sgst),
        "line_total": round2(gross),
    }


def compute_tax(items):
    """
    items: list of dicts, each with unit_price, qty, gst_rate (and any
    caller metadata like name/hsn_code, which is passed through unchanged).

    Returns a dict:
      lines: each input item enriched with its tax breakup
      subtotal_taxable: sum of taxable values across lines
      total_cgst / total_sgst
      grand_total_exact: sum of line_totals, unrounded to rupee
      grand_total: grand_total_exact rounded to the nearest rupee
      round_off: the rupee-rounding adjustment applied (can be +/-)
    """
    lines = []
    subtotal_taxable = 0.0
    total_cgst = 0.0
    total_sgst = 0.0
    grand_total_exact = 0.0

    for item in items:
        breakup = compute_line_tax(item["unit_price"], item["qty"], item["gst_rate"])
        line = {**item, **breakup}
        lines.append(line)
        subtotal_taxable += breakup["taxable_value"]
        total_cgst += breakup["cgst"]
        total_sgst += breakup["sgst"]
        grand_total_exact += breakup["line_total"]

    grand_total = round_rupee(grand_total_exact)
    round_off = round2(grand_total - grand_total_exact)

    return {
        "lines": lines,
        "subtotal_taxable": round2(subtotal_taxable),
        "total_cgst": round2(total_cgst),
        "total_sgst": round2(total_sgst),
        "grand_total_exact": round2(grand_total_exact),
        "round_off": round_off,
        "grand_total": grand_total,
    }
