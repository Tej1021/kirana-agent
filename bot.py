"""
Telegram wiring. Keeps only the per-chat conversation history in memory —
everything durable (stock, bills, khata, preferences) lives in SQLite via
agent.py's tools, so a process restart or a /new chat never loses store
state, only the current conversation's scrollback.
"""

import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

load_dotenv()  # must run before importing agent (Anthropic client reads env at import)

from db import init_db
import agent

# Keep httpx at WARNING so bot tokens aren't printed in request URLs.
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# chat_id -> list of {"role", "content"} messages for the CURRENT conversation only
CONVERSATIONS = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Kirana Agent online. Talk to me like you'd talk to your own shop assistant:\n"
        "\"50 packets of Maggi came in, cost ₹12\"\n"
        "\"make a bill: 2kg sugar, 1 Aashirvaad atta 5kg, UPI\"\n"
        "\"put ₹500 on Ramesh's credit\"\n"
        "\"today's sales?\"\n\n"
        "/new clears this chat's scrollback (your store's data and your preferences stay)."
    )


async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CONVERSATIONS.pop(update.effective_chat.id, None)
    await update.message.reply_text(
        "Started a fresh conversation. Your stock, bills, khata and preferences are unchanged."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    history = CONVERSATIONS.get(chat_id, [])

    # Idempotency seed from Telegram's own update_id: if this exact update is
    # redelivered (Telegram does this on slow acks), any mutating tool call
    # it triggers will be replayed from cache, not re-executed.
    idempotency_seed = f"tg-{update.update_id}"

    try:
        final_text, updated_history = agent.run_turn(
            owner_id=owner_id,
            user_message=update.message.text,
            history=history,
            idempotency_seed=idempotency_seed,
        )
    except Exception as e:
        logging.exception("Agent turn failed")
        await update.message.reply_text(f"Something went wrong on my end: {e}")
        return

    CONVERSATIONS[chat_id] = updated_history

    await update.message.reply_text(final_text or "(done)")

    # If a tool call this turn produced a file (PDF/PPTX), send it.
    for msg in updated_history[-4:]:
        if msg["role"] != "user" or not isinstance(msg["content"], list):
            continue
        for block in msg["content"]:
            if block.get("type") != "tool_result":
                continue
            content = block.get("content", "")
            if '"path"' in content and (".pdf" in content or ".pptx" in content):
                import json as _json
                try:
                    parsed = _json.loads(content)
                    path = parsed.get("path")
                    if path and os.path.exists(path):
                        await update.message.reply_document(document=open(path, "rb"))
                except Exception:
                    pass


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Flaky Telegram links throw NetworkError/TimedOut during long-polling — retry quietly."""
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logging.warning("Telegram network blip (will retry): %s", err)
        return
    logging.error("Unhandled bot error", exc_info=err)


def main():
    init_db()
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    # Longer timeouts help on flaky links to api.telegram.org.
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
    get_updates_request = HTTPXRequest(
        connect_timeout=30.0, read_timeout=60.0, write_timeout=30.0, pool_timeout=30.0
    )

    app = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_chat))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    print("Bot running...")
    app.run_polling(drop_pending_updates=True, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
