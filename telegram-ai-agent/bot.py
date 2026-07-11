"""Gold Club Telegram AI agent.

Customers message the bot; Claude drafts a reply; the support worker gets the
draft with Send / Edit / Reject buttons and every decision feeds back into a
learned style guide.
"""

import asyncio
import html
import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import ai
import db

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("goldclub")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
STYLE_UPDATE_EVERY = int(os.environ.get("STYLE_UPDATE_EVERY", "10"))
KNOWLEDGE_PATH = os.path.join(os.path.dirname(__file__), "knowledge.md")

WELCOME = (
    "Welcome to Gold Club! 🌟\n\n"
    "Send us your question — plans, pricing, setup help, anything — "
    "and we'll get right back to you."
)

_style_task_running = False


def load_knowledge() -> str:
    try:
        with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "(no business knowledge file found — knowledge.md is missing)"


def review_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Send", callback_data=f"a:{draft_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"e:{draft_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"r:{draft_id}"),
            ]
        ]
    )


def review_text(draft, status_line: str = "") -> str:
    cat = (draft["category"] or "?").upper()
    head = f"🆕 Draft #{draft['id']} · {cat}"
    body = (
        f"{head}\n"
        f"👤 {html.escape(draft['customer_name'] or 'Customer')}\n\n"
        f"💬 <b>Customer:</b>\n{html.escape(draft['customer_message'])}\n\n"
        f"🤖 <b>Suggested reply:</b>\n{html.escape(draft['ai_draft'])}"
    )
    if status_line:
        body += f"\n\n{status_line}"
    return body


# --- customer side ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id == ADMIN_CHAT_ID:
        await update.message.reply_text(
            "Admin chat registered. New customer messages will arrive here as "
            "drafts with Send / Edit / Reject buttons.\n"
            "Commands: /stats /style /relearn"
        )
    else:
        await update.message.reply_text(WELCOME)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Setup helper: shows the chat id to put in ADMIN_CHAT_ID."""
    await update.message.reply_text(f"This chat's ID: {update.effective_chat.id}")


async def handle_customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = update.effective_chat.id
    text = msg.text

    if not ADMIN_CHAT_ID:
        log.warning("Customer message received but ADMIN_CHAT_ID is not set.")
        return

    user = update.effective_user
    name = user.full_name + (f" (@{user.username})" if user.username else "")

    history = db.get_history(chat_id)
    db.add_message(chat_id, "customer", text)

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        category, reply = await ai.draft_reply(
            customer_message=text,
            history=history,
            examples=db.get_examples(),
            knowledge=load_knowledge(),
            style_guide=db.kv_get("style_guide"),
        )
    except Exception:
        log.exception("Draft generation failed")
        category, reply = "other", ""

    if not reply:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"⚠️ Could not draft a reply for {html.escape(name)}.\n\n"
                f"💬 {html.escape(text)}\n\n"
                "Please answer them manually."
            ),
            parse_mode="HTML",
        )
        return

    draft_id = db.create_draft(chat_id, name, text, category, reply)
    draft = db.get_draft(draft_id)
    review = await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=review_text(draft),
        parse_mode="HTML",
        reply_markup=review_keyboard(draft_id),
    )
    db.set_admin_msg(draft_id, review.message_id)


async def handle_customer_nontext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Photos, voice notes, payment screenshots etc. — hand straight to the worker."""
    if not ADMIN_CHAT_ID:
        return
    user = update.effective_user
    name = user.full_name + (f" (@{user.username})" if user.username else "")
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📎 Non-text message from {html.escape(name)} — forwarded below, handle manually.",
        parse_mode="HTML",
    )
    await update.message.forward(chat_id=ADMIN_CHAT_ID)


# --- worker side ---

async def _deliver(context, draft, final_text: str, status: str) -> None:
    """Send the final reply to the customer and record the decision."""
    await context.bot.send_message(chat_id=draft["chat_id"], text=final_text)
    db.add_message(draft["chat_id"], "agent", final_text)
    db.decide_draft(draft["id"], status, final_text)
    asyncio.create_task(maybe_update_style(context))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, _, raw_id = query.data.partition(":")
    draft = db.get_draft(int(raw_id))

    if draft is None or draft["status"] not in ("pending", "rejected"):
        await query.answer("Already handled.")
        return

    if action == "a":
        await _deliver(context, draft, draft["ai_draft"], "approved")
        await query.answer("Sent ✅")
        await query.edit_message_text(
            review_text(draft, "✅ <b>Sent as-is.</b>"), parse_mode="HTML"
        )

    elif action == "r":
        db.decide_draft(draft["id"], "rejected", None)
        await query.answer("Rejected")
        await query.edit_message_text(
            review_text(
                draft,
                "❌ <b>Rejected — nothing sent.</b>\n"
                "Reply to this message to send your own answer instead.",
            ),
            parse_mode="HTML",
        )

    elif action == "e":
        await query.answer()
        prompt = await query.message.reply_text(
            f"✏️ Editing draft #{draft['id']} — reply to THIS message with your "
            "version. It will be sent to the customer and the AI will learn "
            "from your changes.",
            reply_markup=ForceReply(selective=True),
        )
        db.set_edit_msg(draft["id"], prompt.message_id)


async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A worker's reply to a review message (or edit prompt) is the final answer."""
    msg = update.message
    if not msg.reply_to_message:
        return
    draft = db.find_draft_by_admin_reply(msg.reply_to_message.message_id)
    if draft is None:
        return

    final_text = msg.text.strip()
    status = "approved" if final_text == draft["ai_draft"].strip() else "edited"
    await _deliver(context, draft, final_text, status)

    label = "✏️ <b>Sent (edited)</b>" if status == "edited" else "✅ <b>Sent as-is.</b>"
    note = f"{label}\n\n<b>Final reply:</b>\n{html.escape(final_text)}" \
        if status == "edited" else label
    try:
        await context.bot.edit_message_text(
            chat_id=ADMIN_CHAT_ID,
            message_id=draft["admin_msg_id"],
            text=review_text(draft, note),
            parse_mode="HTML",
        )
    except Exception:
        pass  # review message may be too old to edit — the reply below still confirms
    await msg.reply_text("Sent to the customer 👍")


# --- learning ---

async def maybe_update_style(context: ContextTypes.DEFAULT_TYPE, force: bool = False) -> None:
    global _style_task_running
    if _style_task_running:
        return

    decided = db.count_decided()
    last = int(db.kv_get("style_decided_at_update", "0"))
    if not force and decided - last < STYLE_UPDATE_EVERY:
        return
    edit_pairs = db.get_edit_pairs(limit=20)
    if not edit_pairs:
        return  # nothing to learn from yet

    _style_task_running = True
    try:
        approved = [e for e in db.get_examples(limit=50) if e["status"] == "approved"][:10]
        guide = await ai.learn_style(edit_pairs, approved, db.kv_get("style_guide"))
        if guide:
            db.kv_set("style_guide", guide)
            db.kv_set("style_decided_at_update", str(decided))
            db.kv_set("style_updated_at", str(int(time.time())))
            log.info("Style guide updated (%d decided drafts).", decided)
            if force and ADMIN_CHAT_ID:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text="🧠 Style guide updated:\n\n" + guide,
                )
    except Exception:
        log.exception("Style guide update failed")
    finally:
        _style_task_running = False


# --- admin commands ---

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = db.stats()
    approved, edited = s.get("approved", 0), s.get("edited", 0)
    total = approved + edited
    rate = f"{100 * approved / total:.0f}%" if total else "n/a"
    await update.message.reply_text(
        f"📊 Drafts — pending: {s.get('pending', 0)} · approved: {approved} · "
        f"edited: {edited} · rejected: {s.get('rejected', 0)}\n"
        f"Approved-as-is rate: {rate}\n"
        f"Style guide: {'set' if db.kv_get('style_guide') else 'not learned yet'}"
    )


async def cmd_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    guide = db.kv_get("style_guide")
    await update.message.reply_text(
        ("🧠 Current learned style guide:\n\n" + guide)
        if guide
        else "No style guide learned yet — it builds up as you edit drafts."
    )


async def cmd_relearn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not db.get_edit_pairs(limit=1):
        await update.message.reply_text("No edited drafts yet — nothing to learn from.")
        return
    await update.message.reply_text("Re-learning style from recent edits…")
    await maybe_update_style(context, force=True)


def main() -> None:
    db.conn()  # create tables up front
    app = Application.builder().token(BOT_TOKEN).build()

    admin = filters.Chat(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else filters.Chat(-1)

    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats, filters=admin))
    app.add_handler(CommandHandler("style", cmd_style, filters=admin))
    app.add_handler(CommandHandler("relearn", cmd_relearn, filters=admin))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(admin & filters.TEXT & ~filters.COMMAND,
                                   handle_admin_message))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~admin & filters.TEXT & ~filters.COMMAND,
        handle_customer_message,
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~admin & ~filters.TEXT & ~filters.COMMAND,
        handle_customer_nontext,
    ))

    log.info("Gold Club agent starting (admin chat: %s)…", ADMIN_CHAT_ID or "NOT SET")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
