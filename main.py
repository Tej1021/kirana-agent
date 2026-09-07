"""
CLI chat mode — exercises the exact same agent.py + tools/ + db.py stack
as bot.py, without needing Telegram. Useful for local testing and for
verifying the demo script before recording it.
"""

import os
import sys
import uuid
from dotenv import load_dotenv

load_dotenv()  # must run before importing agent (Anthropic client reads env at import)

from db import init_db
import agent

OWNER_ID = "cli-owner-1"  # stand-in for a Telegram user id


def main():
    init_db()
    history = []
    print("Kirana Agent CLI. Type 'exit' to quit, '/new' to clear conversation scrollback.")
    while True:
        try:
            text = input("owner> ").strip()
        except EOFError:
            break
        if not text:
            continue
        if text.lower() == "exit":
            break
        if text == "/new":
            history = []
            print("(conversation cleared — store data and preferences unchanged)")
            continue

        idempotency_seed = f"cli-{uuid.uuid4()}"
        final_text, history = agent.run_turn(
            owner_id=OWNER_ID, user_message=text, history=history, idempotency_seed=idempotency_seed
        )
        print(f"agent> {final_text}\n")


if __name__ == "__main__":
    main()
