import os
import logging
import threading
from fastapi import FastAPI
import uvicorn
from dotenv import load_dotenv
from telegram import InputFile, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

load_dotenv() 

from db import init_db
import agent

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
        "\"today's sales?\"\n"
        "\"send me that bill as a PDF\"\n"
        "\"make this week's sales analysis deck\"\n\n"
        "PDF invoices and PPTX decks are sent as downloadable files in this chat.\n"
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
        final_text, updated_history, generated_files = agent.run_turn(
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

    for path in generated_files:
        if not path or not os.path.exists(path):
            continue
        filename = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                await update.message.reply_document(
                    document=InputFile(f, filename=filename),
                    caption=filename,
                )
        except Exception:
            logging.exception("Failed to send document %s", path)
            await update.message.reply_text(
                f"Generated {filename} but couldn't attach it here. Saved at: {path}"
            )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Flaky Telegram links throw NetworkError/TimedOut during long-polling — retry quietly."""
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logging.warning("Telegram network blip (will retry): %s", err)
        return
    logging.error("Unhandled bot error", exc_info=err)


health_app = FastAPI()


@health_app.get("/")
async def health():
    return {"status": "ok", "service": "Kirana Telegram Bot"}


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(
        health_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )


def main():
    init_db()

    # Start HTTP server for Render's health/port check
    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    token = os.environ["TELEGRAM_BOT_TOKEN"]

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    get_updates_request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=30.0,
        pool_timeout=30.0,
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
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    app.add_error_handler(on_error)

    print("Bot running...")
    app.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=-1
    )


if __name__ == "__main__":
    main()
