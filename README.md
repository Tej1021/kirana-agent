# Kirana Agent — run a store from a Telegram chat

An agent that runs a small Indian kirana store end-to-end: receiving stock,
cutting GST-correct bills, khata (credit) management, daily close, PDF
invoices, and PPTX analysis decks — all through plain-language Telegram
messages. No admin panel. The chat is the product.

## Live Bot

Telegram Bot Username: @KiranaMate_bot

## Video Link

https://drive.google.com/file/d/1IuwO5Ng6iYKNZVGjO0KwXwdfSmzbH5Bx/view?usp=sharing


## Harness

Built directly on the **Claude Messages API tool-use loop** (the `anthropic`
Python SDK), rather than the higher-level Claude Agent SDK package. This is
a deliberate choice, not a shortcut:

- The core control loop Agent SDK provides — observe → let the model choose
  tools → execute → feed results back → repeat until `end_turn` — is about
  40 lines of code on top of `messages.create(..., tools=[...])`. Writing it
  directly means zero dependency on an SDK whose surface is still moving
  fast, and full control over exactly where idempotency keys and guardrail
  confirmations get injected (see below) — which is where this task is
  actually won or lost.
- Every tool is a plain Python function with a JSON Schema `input_schema`.
  The model never sees business logic — it sees a menu of thin, named
  capabilities and decides which to call and in what order, chaining calls
  within a turn as needed. Nothing here is a regex/keyword router.

If you'd rather grade this against the actual Agent SDK, `agent.py` is a
drop-in replacement point — swap `run_turn()`'s internals for
`ClaudeSDKClient` and the tool functions/schemas are unchanged.

## Architecture

```
Telegram update ─▶ bot.py ─▶ agent.py (control loop) ─▶ tools/*.py ─▶ SQLite
                                  │
                                  └─▶ Anthropic Messages API (tool_use loop)
```

- **Persistence**: SQLite (`kirana.db`), WAL mode, explicit `BEGIN IMMEDIATE`
  transactions for every mutation. Schema is plain ANSI SQL — swapping to
  Postgres for a multi-owner production deployment means changing the
  connection string and adding `SELECT ... FOR UPDATE` where noted in
  `db.py`; no query rewrites needed.
- **Skills/tools** (`tools/`): `inventory`, `billing`, `khata`, `analytics`,
  `documents`, `preferences`. Each function does one job, validates its own
  inputs against the DB, and returns a structured result — including
  structured *refusals* (oversell, below-cost, unknown khata). The model
  relays these in plain language; it never decides whether a sale is valid.
- **Tax engine** (`tax.py`): a pure, unit-tested function — no LLM in the
  loop for GST math, ever.
- **Agent loop** (`agent.py`): owns the tool-use loop, injects the owner's
  persisted preferences into every new conversation, and — critically —
  **auto-derives idempotency keys for mutating tools itself** rather than
  trusting the model to supply them (see below).
- **Bot** (`bot.py`): Telegram wiring. Also ships `main.py`, a CLI chat mode
  for testing the whole stack without Telegram or a deployed bot.

## The hard parts

**1. Grounding.** Every tool that touches price, GST or stock reads from
SQLite live; the system prompt explicitly forbids the model from stating a
price or quantity it hasn't just gotten from a tool call in this turn.

**2. Oversell guard.** Enforced as a single atomic SQL statement, not an
app-level check:
```sql
UPDATE products SET qty = qty - ? WHERE id = ? AND qty >= ?
```
If `cursor.rowcount == 0`, the sale is refused before anything else in the
bill is touched — the whole `finalize_bill` transaction rolls back. See
`tools/billing.py::finalize_bill` and `tests/test_oversell.py`.

**3. GST correctness.** `tax.py::compute_tax` splits CGST/SGST per item by
its stored slab, rounds each line to paise, and rounds the invoice total to
the nearest rupee (standard round-half-up). Tested in isolation in
`tests/test_tax.py` against known slabs (0% loose staples, 5% packaged
staples, 12–18% FMCG).

**4. Multi-turn bills.** A bill is a `draft` row in SQLite from the moment
`start_bill` is called. Items can be added/edited/removed across any number
of messages — even across a `/new` chat, since the draft lives in the DB,
not conversation memory. Stock is untouched until `finalize_bill` runs.

**5. Idempotency.** Telegram can redeliver the same `update_id`. `bot.py`
derives a key from it; `agent.py`'s dispatcher (not the model) attaches that
key to every mutating tool call. `finalize_bill`, `receive_stock`,
`add_khata_charge` and `add_khata_payment` all check `idempotency_keys`
first and replay the cached result on a retry instead of re-executing.
Proven in `tests/test_idempotency.py` by calling `finalize_bill` twice with
the same key and asserting stock only moves once.

**6. Concurrency.** SQLite's single-writer model plus `BEGIN IMMEDIATE`
means two simultaneous finalize attempts on the same product serialize at
the DB layer — the second sees the already-decremented `qty` and its
`WHERE qty >= ?` guard fails cleanly instead of racing. On Postgres, the
equivalent is a row lock via `SELECT ... FOR UPDATE` before the same
conditional `UPDATE`.

**7. Guardrails.** Selling below cost, deleting stock, or settling a khata
account that doesn't exist all return `{"confirmation_required": true, ...}`
from the tool layer instead of executing. The agent must relay this to the
owner and re-call the tool with `confirmed=true` and the *same* idempotency
key — it can't just retry with different arguments to route around it.

**8. Real artifacts.** `tools/documents.py::generate_invoice_pdf` builds a
GST-correct invoice (line items, HSN codes, CGST/SGST breakup, rounding) via
ReportLab from the finalized bill's actual DB rows.
`generate_analysis_pptx` builds a deck via `python-pptx` + `matplotlib`,
with charts computed from `analytics.py` (sales trend, top items, stock
health, GST collected) — never a screenshot or static template.

**9. Memory across sessions.** `preferences` is a DB table keyed by
`owner_id` (the Telegram user id), not by chat/conversation id. At the start
of every conversation — including after `/new` — `agent.py` calls
`get_all_preferences(owner_id)` and injects it as a system-level fact block
before the first model call. Nothing about memory lives in context history.

## Running locally

```bash
pip install -r requirements.txt --break-system-packages
python seed_data.py              
python main.py                   
python bot.py
```





