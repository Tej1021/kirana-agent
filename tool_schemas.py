MUTATING_TOOLS = {
    "receive_stock", "finalize_bill", "add_khata_charge", "add_khata_payment",
}

TOOLS = [
    # --- Inventory ---
    {
        "name": "search_product",
        "description": "Search products by name or SKU substring. Use this before quoting any price or stock level, and whenever a product reference is ambiguous.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "add_product",
        "description": "Register a new product/SKU with its price, GST slab and HSN code. Refuses if the SKU already exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "name": {"type": "string"},
                "unit": {"type": "string", "enum": ["kg", "g", "litre", "ml", "packet", "dozen", "piece"]},
                "cost_price": {"type": "number"},
                "mrp": {"type": "number"},
                "gst_rate": {"type": "number", "description": "e.g. 0, 5, 12, 18"},
                "hsn_code": {"type": "string"},
                "qty": {"type": "number", "default": 0},
                "reorder_level": {"type": "number", "default": 0},
                "is_loose": {"type": "boolean", "default": False},
            },
            "required": ["sku", "name", "unit", "cost_price", "mrp", "gst_rate", "hsn_code"],
        },
    },
    {
        "name": "receive_stock",
        "description": "Add received stock to an existing product atomically and log it. Refuses if the product doesn't exist yet — add_product must be called first for a genuinely new item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "product_name": {"type": "string"},
                "qty": {"type": "number"},
                "cost_price": {"type": "number", "description": "Optional: update cost if this batch's cost differs"},
            },
            "required": ["qty"],
        },
    },
    {
        "name": "check_stock",
        "description": "Look up current quantity, price and GST info for a product. Always use this rather than recalling a price from earlier in the conversation.",
        "input_schema": {
            "type": "object",
            "properties": {"product_id": {"type": "integer"}, "product_name": {"type": "string"}},
        },
    },
    {
        "name": "low_stock_report",
        "description": "List all products at or below their reorder level.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # --- Billing ---
    {
        "name": "start_bill",
        "description": "Begin a new draft bill. Returns a bill_id to use for all subsequent add/edit/finalize calls on this bill. If payment is on credit, pass customer_name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "payment_mode": {"type": "string", "enum": ["cash", "upi", "card", "credit"]},
                "customer_name": {"type": "string"},
            },
        },
    },
    {
        "name": "add_bill_item",
        "description": "Add a line item to a draft bill. Refuses if requested quantity exceeds current stock (soft check — finalize_bill enforces this atomically and authoritatively).",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {"type": "integer"},
                "product_id": {"type": "integer"},
                "product_name": {"type": "string"},
                "qty": {"type": "number"},
            },
            "required": ["bill_id", "qty"],
        },
    },
    {
        "name": "remove_bill_item",
        "description": "Remove a line item from a draft bill by item_id.",
        "input_schema": {
            "type": "object",
            "properties": {"bill_id": {"type": "integer"}, "item_id": {"type": "integer"}},
            "required": ["bill_id", "item_id"],
        },
    },
    {
        "name": "update_bill_item",
        "description": "Change the quantity of an existing line item on a draft bill.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {"type": "integer"}, "item_id": {"type": "integer"}, "new_qty": {"type": "number"},
            },
            "required": ["bill_id", "item_id", "new_qty"],
        },
    },
    {
        "name": "get_draft_bill",
        "description": "Fetch a draft bill's current items and a tax preview — use this to read back the bill to the owner before finalizing.",
        "input_schema": {
            "type": "object",
            "properties": {"bill_id": {"type": "integer"}},
            "required": ["bill_id"],
        },
    },
    {
        "name": "finalize_bill",
        "description": "Finalize a draft bill: atomically decrements stock for every line item (refusing the whole bill if any item is oversold), computes GST breakup, and — if payment_mode is 'credit' — adds a khata charge. May return confirmation_required=true if any item would sell below cost; in that case ask the owner and re-call with confirmed=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {"type": "integer"},
                "payment_mode": {"type": "string", "enum": ["cash", "upi", "card", "credit"]},
                "payment_ref": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            "required": ["bill_id", "payment_mode"],
        },
    },
    # --- Khata ---
    {
        "name": "add_khata_charge",
        "description": "Add a manual credit charge to a customer's khata (independent of a bill, e.g. 'put ₹500 on Ramesh's credit'). Creates the customer if new.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_name": {"type": "string"}, "amount": {"type": "number"}},
            "required": ["customer_name", "amount"],
        },
    },
    {
        "name": "add_khata_payment",
        "description": "Record a khata payment/settlement from a customer. Refuses if no khata account exists for that name — confirm the spelling with the owner rather than guessing.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_name": {"type": "string"}, "amount": {"type": "number"}},
            "required": ["customer_name", "amount"],
        },
    },
    {
        "name": "get_khata_balance",
        "description": "Get a customer's current khata balance.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_name": {"type": "string"}},
            "required": ["customer_name"],
        },
    },
    {
        "name": "list_debtors",
        "description": "List all customers with an outstanding khata balance, highest first.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # --- Analytics ---
    {
        "name": "daily_close",
        "description": "Close-of-day summary: total sales, GST collected, cash/UPI/card split, top items for a given date (defaults to today).",
        "input_schema": {"type": "object", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD, optional"}}},
    },
    {
        "name": "sales_summary",
        "description": "Total sales and bill count over a period.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string", "enum": ["today", "week", "month"]}},
        },
    },
    {
        "name": "top_items",
        "description": "Top-selling items by revenue over a period.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string", "enum": ["today", "week", "month"]}, "n": {"type": "integer", "default": 5}},
        },
    },
    {
        "name": "gst_collected_report",
        "description": "CGST/SGST collected over a period.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string", "enum": ["today", "week", "month"]}},
        },
    },
    # --- Documents ---
    {
        "name": "generate_invoice_pdf",
        "description": "Generate a clean, GST-correct PDF invoice for a finalized bill.",
        "input_schema": {
            "type": "object",
            "properties": {"bill_id": {"type": "integer"}},
            "required": ["bill_id"],
        },
    },
    {
        "name": "generate_analysis_pptx",
        "description": "Generate a PowerPoint analysis deck (sales, top items, stock health, GST collected) with real charts for a given period.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string", "enum": ["today", "week", "month"], "default": "week"}},
        },
    },
    # --- Preferences ---
    {
        "name": "set_preference",
        "description": "Save a standing owner preference (e.g. default payment mode, preferred brand, shop_name, gstin) that persists across all future chats.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
            "required": ["key", "value"],
        },
    },
    {
        "name": "get_preference",
        "description": "Read a single standing owner preference.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
]
