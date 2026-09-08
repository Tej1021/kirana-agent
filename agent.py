import os
import json

from tool_schemas import TOOLS, MUTATING_TOOLS
from tools import inventory, billing, khata, analytics, documents, preferences as prefs_module

# Which LLM backend this process talks to: "claude" (Anthropic) or "gpt" (OpenAI).
# Set LLM_PROVIDER in .env. One conversation must stay on one provider — the two
# use different message formats, so history is not portable between them.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "claude").strip().lower()

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
GPT_MODEL = os.environ.get("GPT_MODEL", "gpt-5")

MODEL = CLAUDE_MODEL  # backwards-compatible alias

_claude_client = None
_gpt_client_instance = None


def claude_client():
    """Anthropic client, built on first use so a GPT-only setup needs no ANTHROPIC_API_KEY."""
    global _claude_client
    if _claude_client is None:
        import anthropic

        _claude_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _claude_client


def gpt_client():
    """OpenAI client, built on first use so a Claude-only setup needs no OPENAI_API_KEY."""
    global _gpt_client_instance
    if _gpt_client_instance is None:
        import openai

        _gpt_client_instance = openai.OpenAI()  # reads OPENAI_API_KEY from env
    return _gpt_client_instance

SYSTEM_PROMPT_TEMPLATE = """You run a small Indian kirana (grocery) store on behalf of its owner, \
entirely through this chat. Prices are in INR (₹).

Rules you must always follow:
- Never state a price, stock quantity, GST rate, or khata balance from memory or by guessing — \
always call the relevant tool first, even if you think you already know the answer from earlier \
in this conversation.
- If a request is genuinely ambiguous (e.g. "add atta" when multiple atta products or a loose/packaged \
distinction could apply), ask a short clarifying question instead of guessing.
- Bills are built over multiple messages: start_bill once, then add/remove/update items as the owner \
talks, and only call finalize_bill when the owner clearly wants to complete the sale. Read the bill \
back (via get_draft_bill) before finalizing if it has more than one or two items, so the owner can \
catch mistakes.
- If a tool returns confirmation_required, explain why in plain language and only proceed (re-calling \
with confirmed=true) if the owner explicitly agrees.
- If a tool returns an error/refusal (oversell, unknown khata account, etc.), relay it plainly — do not \
retry with different values to work around it.
- Keep replies short and shopkeeper-practical, like a text message, not a report.
- Never use markdown (no asterisks, no bold), never use emoji or icons. Plain text only.
- When finalize_bill or get_draft_bill returns receipt_text, send that receipt to the owner \
verbatim as the bill. Do not decorate it. You may add one plain line after it asking if they \
want the invoice PDF.
- When the owner asks for an invoice PDF or analysis deck, call generate_invoice_pdf / \
generate_analysis_pptx. The bot will attach the file in Telegram — just confirm it is attached.
- Reply in the language of the owner's latest message (Hindi, English, Tamil, mixed, etc.). \
If they switch languages mid-chat, switch with them — do not keep using an earlier language \
just because previous turns were in it. Do not save a language preference unless they \
explicitly ask you to always use one.

The owner's standing preferences (persisted across all chats):
{preferences_block}
"""


def build_system_prompt(owner_id):
    prefs = prefs_module.get_all_preferences(owner_id)
    if prefs:
        block = "\n".join(f"- {k}: {v}" for k, v in prefs.items())
    else:
        block = "(none set yet)"
    return SYSTEM_PROMPT_TEMPLATE.format(preferences_block=block)


TOOL_DISPATCH = {
    "search_product": inventory.search_product,
    "add_product": inventory.add_product,
    "receive_stock": inventory.receive_stock,
    "check_stock": inventory.check_stock,
    "low_stock_report": lambda **kw: inventory.low_stock_report(),
    "start_bill": billing.start_bill,
    "add_bill_item": billing.add_bill_item,
    "remove_bill_item": billing.remove_bill_item,
    "update_bill_item": billing.update_bill_item,
    "get_draft_bill": billing.get_draft_bill,
    "finalize_bill": billing.finalize_bill,
    "add_khata_charge": khata.add_khata_charge,
    "add_khata_payment": khata.add_khata_payment,
    "get_khata_balance": khata.get_khata_balance,
    "list_debtors": lambda **kw: khata.list_debtors(kw["owner_id"]),
    "daily_close": lambda **kw: analytics.daily_close(kw["owner_id"], kw.get("date")),
    "sales_summary": lambda **kw: analytics.sales_summary(kw["owner_id"], kw.get("period", "week")),
    "top_items": lambda **kw: analytics.top_items(kw["owner_id"], kw.get("period", "week"), kw.get("n", 5)),
    "gst_collected_report": lambda **kw: analytics.gst_collected_report(kw["owner_id"], kw.get("period", "week")),
    "generate_invoice_pdf": lambda **kw: documents.generate_invoice_pdf(kw["bill_id"], owner_id=kw.get("owner_id")),
    "generate_analysis_pptx": lambda **kw: documents.generate_analysis_pptx(kw["owner_id"], kw.get("period", "week")),
    "set_preference": lambda **kw: prefs_module.set_preference(kw["owner_id"], kw["key"], kw["value"]),
    "get_preference": lambda **kw: prefs_module.get_preference(kw["owner_id"], kw["key"]),
}

# Tools that need owner_id injected automatically (the model doesn't supply it)
NEEDS_OWNER_ID = {
    "start_bill", "add_khata_charge", "add_khata_payment", "get_khata_balance",
    "list_debtors", "daily_close", "sales_summary", "top_items", "gst_collected_report",
    "generate_invoice_pdf", "generate_analysis_pptx", "set_preference", "get_preference",
}


def dispatch_tool(tool_name, tool_input, owner_id, idempotency_seed):
    fn = TOOL_DISPATCH.get(tool_name)
    if fn is None:
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}

    kwargs = dict(tool_input)
    if tool_name in NEEDS_OWNER_ID:
        kwargs["owner_id"] = owner_id
    if tool_name in MUTATING_TOOLS:
        # Idempotency key is derived by the control loop, never trusted from the model.
        kwargs["idempotency_key"] = f"{idempotency_seed}:{tool_name}"

    try:
        return fn(**kwargs)
    except TypeError as e:
        return {"ok": False, "error": f"Bad arguments for {tool_name}: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Internal error in {tool_name}: {e}"}


def with_language_hint(user_message):
    """Pin reply language to this turn so history in another language cannot stick."""
    return (
        f"{user_message}\n\n"
        "[Reply in the same language as my message above. "
        "If I wrote in English, reply in English — do not continue in Hindi "
        "just because earlier messages were Hindi.]"
    )


def run_turn_claude(owner_id, user_message, history, idempotency_seed):
    """Anthropic (Claude) implementation of run_turn. History is in Anthropic format."""
    system_prompt = build_system_prompt(owner_id)
    messages = history + [{"role": "user", "content": with_language_hint(user_message)}]
    generated_files = []

    while True:
        response = claude_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            final_text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            return final_text, messages, generated_files

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = dispatch_tool(block.name, block.input, owner_id, idempotency_seed)
            if isinstance(result, dict) and result.get("ok") and result.get("path"):
                path = result["path"]
                if os.path.exists(path):
                    generated_files.append(path)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, default=str),
            })

        messages.append({"role": "user", "content": tool_results})


def _openai_tools():
    """TOOLS is written in Anthropic shape; OpenAI wants each one wrapped as a function."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        }
        for tool in TOOLS
    ]


OPENAI_TOOLS = _openai_tools()


def run_turn_gpt(owner_id, user_message, history, idempotency_seed):
    """OpenAI (GPT) implementation of run_turn. History is in OpenAI chat format."""
    system_prompt = build_system_prompt(owner_id)
    messages = history + [{"role": "user", "content": with_language_hint(user_message)}]
    generated_files = []

    while True:
        response = gpt_client().chat.completions.create(
            model=GPT_MODEL,
            max_completion_tokens=1024,
            tools=OPENAI_TOOLS,
            messages=[{"role": "system", "content": system_prompt}] + messages,
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return (message.content or ""), messages, generated_files

        for call in message.tool_calls:
            try:
                tool_input = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as e:
                result = {"ok": False, "error": f"Bad arguments for {call.function.name}: {e}"}
            else:
                result = dispatch_tool(call.function.name, tool_input, owner_id, idempotency_seed)
            if isinstance(result, dict) and result.get("ok") and result.get("path"):
                path = result["path"]
                if os.path.exists(path):
                    generated_files.append(path)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, default=str),
            })


def run_turn(owner_id, user_message, history, idempotency_seed):
    """
    owner_id: stable identity for the shop owner (Telegram user id)
    user_message: plain text from the owner
    history: list of prior messages for THIS conversation, in the format of the
             active provider (cleared on /new — persistence of facts happens via
             the DB, not this list)
    idempotency_seed: unique per incoming update (e.g. f"tg-{update_id}"), used to
             derive idempotency keys for any mutating tool calls this turn triggers
    Returns (final_text, updated_history, generated_files)

    Routes to Claude or GPT based on LLM_PROVIDER.
    """
    if LLM_PROVIDER == "claude":
        return run_turn_claude(owner_id, user_message, history, idempotency_seed)
    if LLM_PROVIDER == "gpt":
        return run_turn_gpt(owner_id, user_message, history, idempotency_seed)
    raise ValueError(
        f"Unknown LLM_PROVIDER {LLM_PROVIDER!r} — expected 'claude' or 'gpt'."
    )
