# Gold Club Telegram AI Agent

An AI assistant for your Gold Club IPTV sales & support chats on Telegram.
Customers message your bot, Claude drafts a reply, and **nothing is sent until
your support worker approves it**. Every edit the worker makes teaches the AI
to write more like them.

## How it works

```
Customer ──▶ Telegram bot ──▶ Claude drafts a reply
                                    │
                     Worker gets the draft with buttons:
                     ✅ Send   ✏️ Edit   ❌ Reject
                                    │
              ✅ sends the draft as-is to the customer
              ✏️ worker replies with their version → that gets sent
              ❌ nothing sent (worker can still reply with their own answer)
                                    │
              Every decision is stored. After every N handled chats,
              Claude compares its drafts to the worker's final versions
              and updates a persistent "style guide" used for all
              future drafts — so it learns the worker's way of messaging.
```

The AI also learns in two more ways on every single message:

- **Few-shot retrieval** — past approved/edited answers similar to the new
  question are shown to Claude as "imitate these" examples.
- **Conversation memory** — the last few messages with that customer are
  included, so follow-ups make sense.

Facts (prices, plans, setup steps) come only from `knowledge.md`, which you
edit yourself — the AI is instructed never to invent pricing or promises.

## Setup

1. **Create the bot**: talk to [@BotFather](https://t.me/BotFather) on
   Telegram → `/newbot` → copy the token.

2. **Install & configure**:

   ```bash
   cd telegram-ai-agent
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env   # then edit .env
   ```

   Put your bot token and Anthropic API key in `.env`.

3. **Find the admin chat ID** (where drafts go for approval):
   - Run the bot once (`python bot.py`).
   - From your support worker's account (or from a private staff group the
     bot has been added to), send `/id` to the bot.
   - Put the number it replies with into `ADMIN_CHAT_ID` in `.env` and restart.

4. **Fill in `knowledge.md`** with your real plans, prices, trial policy,
   setup steps, and common fixes. This is what the AI is allowed to state as fact.

5. **Run it**:

   ```bash
   python bot.py
   ```

Share the bot's @username with customers (or link it from your channel/site).

## Daily use (support worker)

- New customer messages arrive in the admin chat as a draft card with
  **✅ Send / ✏️ Edit / ❌ Reject** buttons.
- **Edit**: tap ✏️, then reply to the prompt with your version — it's sent to
  the customer and the AI learns from the difference. You can also just reply
  directly to the draft card with your text; same effect.
- **Reject**: nothing is sent; reply to the card afterwards to send your own
  answer (the AI learns from that too).
- Photos/voice notes from customers are forwarded for manual handling.

### Admin commands

| Command | What it does |
|---|---|
| `/stats` | Draft counts, approved-as-is rate |
| `/style` | Show the current learned style guide |
| `/relearn` | Force a style-guide update from recent edits now |
| `/id` | Show this chat's ID (setup helper) |

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | From @BotFather |
| `ANTHROPIC_API_KEY` | — | From platform.claude.com |
| `ADMIN_CHAT_ID` | `0` | Worker's user ID or staff group ID |
| `CLAUDE_MODEL` | `claude-opus-4-8` | Model used for drafting & learning |
| `STYLE_UPDATE_EVERY` | `10` | Re-learn style after this many handled drafts |
| `DB_PATH` | `goldclub.db` | SQLite file (conversations, drafts, style guide) |

Draft generation uses adaptive thinking with `effort: "medium"` for fast
replies; raise it to `"high"` in `ai.py` if you'd rather trade speed for
quality. The business-knowledge and style-guide sections of the prompt are
cached (Anthropic prompt caching), so repeat messages are cheap.

## Running 24/7

Any small VPS works. Example systemd unit:

```ini
[Unit]
Description=Gold Club Telegram AI agent
After=network-online.target

[Service]
WorkingDirectory=/opt/telegram-ai-agent
ExecStart=/opt/telegram-ai-agent/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Back up `goldclub.db` — it holds the conversation history and everything the
AI has learned.
