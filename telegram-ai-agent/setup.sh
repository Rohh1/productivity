#!/usr/bin/env bash
# Gold Club Telegram AI agent — one-command setup & launch.
# Run it on your own computer first (Telegram login happens there),
# then run the same script on your VPS after copying the folder over.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Gold Club Telegram AI agent — setup ==="

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 is not installed. Install Python 3.10+ and re-run."
    exit 1
fi

# Virtualenv + dependencies
if [ ! -d venv ]; then
    echo "Creating virtualenv…"
    python3 -m venv venv
fi
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
echo "✅ Dependencies installed."

# .env wizard
if [ ! -f .env ]; then
    echo
    echo "No .env found — let's create one. Paste each value and press Enter."
    echo
    echo "1/5  Personal account API ID"
    echo "     → log in at https://my.telegram.org → 'API development tools'"
    read -rp "TG_API_ID: " TG_API_ID
    read -rp "TG_API_HASH (same page): " TG_API_HASH
    echo
    echo "2/5  Approval bot token"
    echo "     → message @BotFather on Telegram → /newbot → copy the token"
    read -rp "TELEGRAM_BOT_TOKEN: " BOT_TOKEN
    echo
    echo "3/5  Anthropic API key → https://platform.claude.com (billing enabled)"
    read -rp "ANTHROPIC_API_KEY: " ANTHROPIC_KEY
    echo
    echo "4/5  Admin chat ID (where drafts go for approval)."
    echo "     Don't know it yet? Press Enter — after startup, send /id to the"
    echo "     approval bot from your worker's account, put the number in .env,"
    echo "     and restart."
    read -rp "ADMIN_CHAT_ID [0]: " ADMIN_ID
    ADMIN_ID=${ADMIN_ID:-0}

    cat > .env <<EOF
TG_API_ID=$TG_API_ID
TG_API_HASH=$TG_API_HASH
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
ANTHROPIC_API_KEY=$ANTHROPIC_KEY
ADMIN_CHAT_ID=$ADMIN_ID
CLAUDE_MODEL=claude-opus-4-8
STYLE_UPDATE_EVERY=10
DB_PATH=goldclub.db
SESSION_NAME=goldclub_user
EOF
    chmod 600 .env
    echo "✅ .env written."
fi

echo
echo "5/5  Business knowledge"
if grep -q "\[PRICE\]" knowledge.md 2>/dev/null; then
    echo "⚠️  knowledge.md still has [PRICE] placeholders. The AI only states"
    echo "    facts from that file — fill in your real plans, prices, and setup"
    echo "    steps for good drafts. (The bot still runs without it.)"
else
    echo "✅ knowledge.md looks customised."
fi

echo
echo "Starting the agent…"
echo "First run: it will ask for your phone number and the login code Telegram"
echo "sends you (plus your 2FA password if set). NEVER share that code with"
echo "anyone — type it only into this terminal."
echo "Stop with Ctrl+C."
echo
exec ./venv/bin/python bot.py
