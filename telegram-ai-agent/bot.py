"""Gold Club Telegram AI agent — personal-account edition.

A Telethon client logged in as YOUR personal Telegram account receives
customer DMs. Claude drafts a reply, a companion approval bot shows it to
your support worker with Send / Edit / Reject buttons, and the approved text
is sent from your personal account — so customers just see you answering.

Manual replies sent straight from your phone are picked up too and feed the
same style-learning loop.
"""

import asyncio
import html
import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()

# When no API key is configured, authenticate via a Claude CLI login
# (`ant auth login`), whose stored profile the Anthropic SDK reads
# automatically. A blank ANTHROPIC_API_KEY would otherwise take precedence and
# fail, so drop an empty one and let the SDK fall back to that profile.
if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ.pop("ANTHROPIC_API_KEY", None)

from telethon import TelegramClient, events
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
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("goldclub")

# Personal account (get these at https://my.telegram.org → API development tools)
TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
SESSION_NAME = os.environ.get("SESSION_NAME", "goldclub_user")

# Approval bot (the worker's control panel)
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))

STYLE_UPDATE_EVERY = int(os.environ.get("STYLE_UPDATE_EVERY", "10"))
KNOWLEDGE_PATH = os.path.join(os.path.dirname(__file__), "knowledge.md")

TG: TelegramClient | None = None   # personal account client
BOT = None                         # approval bot (telegram.Bot)
ME_ID: int | None = None           # personal account user id
AGENT_SENT: set[int] = set()       # message ids we sent, so the outgoing
                                   # handler doesn't treat them as manual replies
_style_task_running = False


def load_knowledge() -> str:
    try:
        with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "(no business knowledge file found — knowledge.md is missing)"


def peer_name(user) -> str:
    name = " ".join(filter(None, [getattr(user, "first_name", None),
                                  getattr(user, "last_name", None)])) or "Customer"
    if getattr(user, "username", None):
        name += f" (@{user.username})"
    return name


def review_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Send", callback_data=f"a:{draft_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"e:{draft_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"r:{draft_id}"),
            ],
            [InlineKeyboardButton("🔕 Ignore this chat", callback_data=f"i:{draft_id}")],
        ]
    )


def review_text(draft, status_line: str = "") -> str:
    cat = (draft["category"] or "?").upper()
    body = (
        f"🆕 Draft #{draft['id']} · {cat}\n"
        f"👤 {html.escape(draft['customer_name'] or 'Customer')}\n\n"
        f"💬 <b>Customer:</b>\n{html.escape(draft['customer_message'])}\n\n"
        f"🤖 <b>Suggested reply:</b>\n{html.escape(draft['ai_draft'])}"
    )
    if status_line:
        body += f"\n\n{status_line}"
    return body


async def update_card(draft, status_line: str) -> None:
    if not draft["admin_msg_id"]:
        return
    try:
        await BOT.edit_message_text(
            chat_id=ADMIN_CHAT_ID,
            message_id=draft["admin_msg_id"],
            text=review_text(draft, status_line),
            parse_mode="HTML",
        )
    except Exception:
        pass  # card may be too old to edit — not critical


# --- personal account: incoming customer messages ---

async def on_incoming(event) -> None:
    if not event.is_private:
        return
    peer = await event.get_chat()
    if peer is None or getattr(peer, "bot", False) or event.chat_id == ME_ID:
        return
    if db.is_ignored(event.chat_id):
        return
    if not ADMIN_CHAT_ID:
        log.warning("Customer message received but ADMIN_CHAT_ID is not set.")
        return

    name = peer_name(peer)
    text = (event.raw_text or "").strip()

    if not text:
        await BOT.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(f"📎 Media/voice message from <b>{html.escape(name)}</b> — "
                  "open Telegram to handle it manually."),
            parse_mode="HTML",
        )
        return

    history = db.get_history(event.chat_id)
    db.add_message(event.chat_id, "customer", text)

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
        await BOT.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(f"⚠️ Could not draft a reply for <b>{html.escape(name)}</b>.\n\n"
                  f"💬 {html.escape(text)}\n\nPlease answer them manually."),
            parse_mode="HTML",
        )
        return

    draft_id = db.create_draft(event.chat_id, name, text, category, reply)
    draft = db.get_draft(draft_id)
    review = await BOT.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=review_text(draft),
        parse_mode="HTML",
        reply_markup=review_keyboard(draft_id),
    )
    db.set_admin_msg(draft_id, review.message_id)


# --- personal account: manual replies typed on the phone/desktop ---

async def on_outgoing(event) -> None:
    if not event.is_private:
        return
    if event.message.id in AGENT_SENT:
        AGENT_SENT.discard(event.message.id)
        return  # this one was sent by the agent itself
    if event.chat_id == ME_ID:
        return
    peer = await event.get_chat()
    if peer is None or getattr(peer, "bot", False):
        return
    text = (event.raw_text or "").strip()
    if not text:
        return

    db.add_message(event.chat_id, "agent", text)

    # If a draft was waiting for this chat, the manual reply supersedes it —
    # and it's a learning signal (worker's answer vs the AI draft).
    draft = db.get_pending_by_chat(event.chat_id)
    if draft:
        status = "approved" if text == draft["ai_draft"].strip() else "edited"
        db.decide_draft(draft["id"], status, text)
        await update_card(
            draft,
            "📱 <b>Answered manually from the account:</b>\n" + html.escape(text),
        )
        asyncio.create_task(maybe_update_style())


# --- delivery (always from the personal account) ---

async def _deliver(draft, final_text: str, status: str) -> None:
    try:
        await TG.send_read_acknowledge(draft["chat_id"])
    except Exception:
        pass
    sent = await TG.send_message(draft["chat_id"], final_text)
    AGENT_SENT.add(sent.id)
    if len(AGENT_SENT) > 1000:
        AGENT_SENT.clear()  # stale ids only; in-flight window is seconds
    db.add_message(draft["chat_id"], "agent", final_text)
    db.decide_draft(draft["id"], status, final_text)
    asyncio.create_task(maybe_update_style())


# --- approval bot: buttons ---

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, _, raw_id = query.data.partition(":")
    draft = db.get_draft(int(raw_id))

    if draft is None or draft["status"] not in ("pending", "rejected"):
        await query.answer("Already handled.")
        return

    if action == "a":
        try:
            await _deliver(draft, draft["ai_draft"], "approved")
        except Exception:
            log.exception("Send failed")
            await query.answer("⚠️ Sending failed — check the logs.", show_alert=True)
            return
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

    elif action == "i":
        db.decide_draft(draft["id"], "rejected", None)
        db.ignore_chat(draft["chat_id"], draft["customer_name"] or "")
        await query.answer("Chat ignored 🔕")
        await query.edit_message_text(
            review_text(
                draft,
                "🔕 <b>Chat ignored</b> — no more drafts for this person. "
                "Use /ignored to review, /unignore &lt;id&gt; to undo.",
            ),
            parse_mode="HTML",
        )


# --- approval bot: worker replies = final answers ---

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg.reply_to_message:
        return
    draft = db.find_draft_by_admin_reply(msg.reply_to_message.message_id)
    if draft is None:
        return

    final_text = msg.text.strip()
    status = "approved" if final_text == draft["ai_draft"].strip() else "edited"
    try:
        await _deliver(draft, final_text, status)
    except Exception:
        log.exception("Send failed")
        await msg.reply_text("⚠️ Sending failed — check the logs.")
        return

    if status == "edited":
        note = ("✏️ <b>Sent (edited).</b>\n\n<b>Final reply:</b>\n"
                + html.escape(final_text))
    else:
        note = "✅ <b>Sent as-is.</b>"
    await update_card(draft, note)
    await msg.reply_text("Sent to the customer 👍")


# --- learning ---

async def maybe_update_style(force: bool = False) -> None:
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
                await BOT.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text="🧠 Style guide updated:\n\n" + guide,
                )
    except Exception:
        log.exception("Style guide update failed")
    finally:
        _style_task_running = False


# --- approval bot: commands ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Gold Club approval panel.\n\n"
        "Drafts for incoming customer DMs will appear here with "
        "Send / Edit / Reject buttons.\n"
        "Commands: /stats /style /relearn /ignored /unignore /id"
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Setup helper: shows the chat id to put in ADMIN_CHAT_ID."""
    await update.message.reply_text(f"This chat's ID: {update.effective_chat.id}")


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
    await maybe_update_style(force=True)


async def cmd_ignored(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.ignored_list()
    if not rows:
        await update.message.reply_text("No ignored chats.")
        return
    lines = [f"• {r['name'] or 'Unknown'} — /unignore {r['chat_id']}" for r in rows]
    await update.message.reply_text("🔕 Ignored chats:\n" + "\n".join(lines))


async def cmd_unignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /unignore <chat_id> (see /ignored)")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("That's not a chat id. See /ignored.")
        return
    if db.unignore_chat(chat_id):
        await update.message.reply_text("Un-ignored — drafts resume for that chat. ✅")
    else:
        await update.message.reply_text("That chat wasn't on the ignore list.")


# --- wiring ---

def build_approval_bot() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    admin = filters.Chat(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else filters.Chat(-1)

    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats, filters=admin))
    app.add_handler(CommandHandler("style", cmd_style, filters=admin))
    app.add_handler(CommandHandler("relearn", cmd_relearn, filters=admin))
    app.add_handler(CommandHandler("ignored", cmd_ignored, filters=admin))
    app.add_handler(CommandHandler("unignore", cmd_unignore, filters=admin))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(admin & filters.TEXT & ~filters.COMMAND,
                                   handle_admin_message))
    return app


async def main() -> None:
    global TG, BOT, ME_ID
    db.conn()  # create tables up front

    TG = TelegramClient(SESSION_NAME, TG_API_ID, TG_API_HASH)
    TG.add_event_handler(on_incoming, events.NewMessage(incoming=True))
    TG.add_event_handler(on_outgoing, events.NewMessage(outgoing=True))

    app = build_approval_bot()
    async with app:  # approval bot first, so review cards can always be sent
        BOT = app.bot
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await app.start()
        log.info("Approval bot running (admin chat: %s)…", ADMIN_CHAT_ID or "NOT SET")

        # First run prompts for your phone number + login code in the terminal,
        # then saves a session file so later runs are non-interactive.
        await TG.start()
        me = await TG.get_me()
        ME_ID = me.id
        log.info("Personal account connected: %s (id %s)", peer_name(me), me.id)
        try:
            await TG.run_until_disconnected()
        finally:
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
