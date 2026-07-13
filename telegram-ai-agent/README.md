# Gold Club Telegram AI Agent — Personal Account Edition

An AI assistant for the Gold Club IPTV sales & support chats **on your
personal Telegram account**. Customers DM you like they always have; Claude
drafts a reply; **nothing is sent until your support worker approves it**;
the approved reply is sent from your account, so customers just see you
answering. Every edit the worker makes teaches the AI to write more like them.

## How it works

Two Telegram identities run in one process:

- **Your personal account** (via Telethon / MTProto) — receives customer DMs
  and sends the final replies.
- **A small approval bot** (via @BotFather) — the worker's control panel.
  Telegram doesn't allow buttons on user accounts, so approvals happen here.

```
Customer DMs your account ──▶ Claude drafts a reply
                                     │
                    Worker's approval bot shows the draft:
                    ✅ Send   ✏️ Edit   ❌ Reject   🔕 Ignore chat
                                     │
          ✅ draft is sent from YOUR account, as-is
          ✏️ worker replies with their version → that gets sent
          ❌ nothing sent (worker can still reply with their own answer)
          🔕 stop drafting for this chat (friends/family/non-customers)
```

**It also learns from your phone.** If the worker answers a customer directly
from the account (phone/desktop) while a draft is pending, the agent notices,
sends nothing itself, and uses that manual answer as a learning example.

### The learning loop

1. **Style guide** — after every `STYLE_UPDATE_EVERY` handled chats, Claude
   compares its drafts to the worker's final versions (edits + manual replies)
   and updates a persistent style guide: tone, greetings, emoji habits, length,
   wording. It's injected into every future draft. See it with `/style`.
2. **Few-shot retrieval** — past approved/edited answers similar to the new
   question are shown to Claude as "imitate these" examples.
3. **Conversation memory** — the last few messages with that customer are
   included, so follow-ups make sense.

Facts (prices, plans, setup steps) come only from `knowledge.md`, which you
edit yourself — the AI is instructed never to invent pricing or promises.

## Setup

1. **Get personal-account API credentials**: log in at
   [my.telegram.org](https://my.telegram.org) → *API development tools* →
   create an app → copy `api_id` and `api_hash`.

2. **Create the approval bot**: talk to [@BotFather](https://t.me/BotFather)
   → `/newbot` → copy the token.

3. **Install, configure & start — one command**:

   ```bash
   cd telegram-ai-agent
   ./setup.sh
   ```

   The script installs everything, asks for the credentials from steps 1–2
   (plus your Anthropic API key), writes `.env`, and starts the bot.
   Manual alternative: `pip install -r requirements.txt`, copy `.env.example`
   to `.env`, edit it, run `python bot.py`.

4. **First run is interactive**: it asks for your phone number, the login
   code Telegram sends you, and your 2FA password if you have one. Never
   share that code with anyone — type it only into the terminal. This creates
   `goldclub_user.session`; later runs (and server deployments) are fully
   non-interactive. **Treat the .session file like a password** — it's full
   access to your account.

5. **Set the admin chat**: from the support worker's account (or a private
   staff group the bot was added to), send `/id` to the approval bot, put the
   number into `ADMIN_CHAT_ID` in `.env`, restart.

6. **Fill in `knowledge.md`** with your real plans, prices, trial policy,
   setup steps, and common fixes — this is what the AI may state as fact.

## Daily use (support worker)

- Each customer DM arrives in the approval bot as a draft card with
  **✅ Send / ✏️ Edit / ❌ Reject / 🔕 Ignore** buttons.
- **Edit**: tap ✏️ and reply with your version — it's sent from the personal
  account and the AI learns from the difference. You can also just reply
  directly to the draft card; same effect.
- **Reject**: nothing is sent; replying to the card afterwards sends your own
  answer (the AI learns from that too).
- **Ignore**: for non-customer chats (friends, family) — no more drafts for
  that person. Manage with `/ignored` and `/unignore <id>`.
- Answering straight from the phone also works — the pending draft is closed
  automatically and your answer is used for learning.
- Media/voice messages can't be auto-drafted; the bot pings the worker to
  handle them in Telegram.

### Admin commands

| Command | What it does |
|---|---|
| `/stats` | Draft counts, approved-as-is rate |
| `/style` | Show the current learned style guide |
| `/relearn` | Force a style-guide update from recent edits now |
| `/ignored` / `/unignore <id>` | Manage ignored chats |
| `/id` | Show this chat's ID (setup helper) |

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `TG_API_ID` / `TG_API_HASH` | — | Personal account API creds (my.telegram.org) |
| `TELEGRAM_BOT_TOKEN` | — | Approval bot token (@BotFather) |
| `ADMIN_CHAT_ID` | `0` | Worker's user ID or staff group ID |
| `ANTHROPIC_API_KEY` | — | From platform.claude.com |
| `CLAUDE_MODEL` | `claude-opus-4-8` | Model used for drafting & learning |
| `STYLE_UPDATE_EVERY` | `10` | Re-learn style after this many handled drafts |
| `DB_PATH` | `goldclub.db` | SQLite file (conversations, drafts, style guide) |
| `SESSION_NAME` | `goldclub_user` | Telethon session file name |

Draft generation uses adaptive thinking with `effort: "medium"` for fast
replies; raise it to `"high"` in `ai.py` to trade speed for quality. The
business-knowledge and style-guide prompt sections are cached (Anthropic
prompt caching), so repeat messages are cheap.

## Good to know

- **Human-in-the-loop by design.** Automating a user account is against
  Telegram's rules when used for spam or unsolicited messaging; this agent
  only ever *replies* to people who message you first, and only after a human
  approves — which is the safe way to use it. Don't bolt on broadcast/bulk
  features.
- **Log out other suspicious sessions** and keep 2FA on — the session file
  plus your `.env` fully control the account.
- **Back up `goldclub.db`** — it holds conversation history and everything
  the AI has learned.

## Running 24/7

Do the first interactive login on your own machine, then copy the folder
(including the `.session` file, minus `venv/`) to a small VPS and run
`./setup.sh` there once — it rebuilds the venv and reuses your existing
`.env` and session. For always-on operation use systemd. Example unit:

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
