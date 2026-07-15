#!/usr/bin/env bash
# Gold Club Telegram AI agent — one-command setup & launch.
# Run it on your own computer first (Telegram login happens there),
# then run the same script on your VPS after copying the folder over.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Gold Club Telegram AI agent — setup ==="

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 is not installed."
    if [ "$(uname)" = "Darwin" ]; then
        echo "   On macOS, install Apple's command-line tools with:"
        echo "       xcode-select --install"
        echo "   (or 'brew install python' if you use Homebrew), then re-run ./setup.sh"
    else
        echo "   Install Python 3.10+ and re-run ./setup.sh"
    fi
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
    echo "3/5  Claude access — how should the bot reach Claude?"
    echo "     [1] Your Claude subscription via the CLI  (personal use; no per-message"
    echo "         cost, but needs the 'ant' CLI and an occasional re-login)"
    echo "     [2] An Anthropic API key                  (pay-per-use; best for always-on)"
    read -rp "Choose 1 or 2 [1]: " AUTH_CHOICE
    AUTH_CHOICE=${AUTH_CHOICE:-1}
    ANTHROPIC_KEY=""
    if [ "$AUTH_CHOICE" = "2" ]; then
        echo "     → get a key at https://platform.claude.com (billing enabled)"
        read -rp "ANTHROPIC_API_KEY (sk-ant-...): " ANTHROPIC_KEY
    fi
    echo
    echo "4/5  Admin chat ID (where drafts go for approval)."
    echo "     Don't know it yet? Press Enter — after startup, send /id to the"
    echo "     approval bot from your worker's account, put the number in .env,"
    echo "     and restart."
    read -rp "ADMIN_CHAT_ID [0]: " ADMIN_ID
    ADMIN_ID=${ADMIN_ID:-0}

    {
        echo "TG_API_ID=$TG_API_ID"
        echo "TG_API_HASH=$TG_API_HASH"
        echo "TELEGRAM_BOT_TOKEN=$BOT_TOKEN"
        # Omit the key entirely in CLI mode — a blank key would break login.
        if [ -n "$ANTHROPIC_KEY" ]; then echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY"; fi
        echo "ADMIN_CHAT_ID=$ADMIN_ID"
        echo "CLAUDE_MODEL=claude-opus-4-8"
        echo "STYLE_UPDATE_EVERY=10"
        echo "DB_PATH=goldclub.db"
        echo "SESSION_NAME=goldclub_user"
    } > .env
    chmod 600 .env
    echo "✅ .env written."
fi

# --- Ensure Claude is reachable ---
if grep -qE '^ANTHROPIC_API_KEY=.+' .env; then
    echo "✅ Claude access: using an API key from .env."
else
    echo "Claude access: using your Claude subscription via the Anthropic CLI ('ant')."
    if ! command -v ant >/dev/null 2>&1; then
        echo "❌ The 'ant' CLI isn't installed yet. Install it, then re-run ./setup.sh:"
        if [ "$(uname)" = "Darwin" ]; then
            echo "     brew install anthropics/tap/ant"
            echo "     xattr -d com.apple.quarantine \"\$(brew --prefix)/bin/ant\" 2>/dev/null || true"
            echo "   No Homebrew? Install it from https://brew.sh first, or download a"
            echo "   macOS binary from https://github.com/anthropics/anthropic-cli/releases"
        else
            echo "   See https://github.com/anthropics/anthropic-cli/releases"
        fi
        exit 1
    fi
    if ant auth status 2>&1 | grep -qiE 'logged in|active|profile|expires|@'; then
        echo "✅ Claude CLI already logged in."
    else
        echo "Opening Claude login in your browser (add --no-browser on a headless server)…"
        ant auth login
    fi
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
echo "anyone — type it only into this terminal. (This is the Telegram login,"
echo "separate from any Claude login above.)"
echo "Stop with Ctrl+C."
echo
exec ./venv/bin/python bot.py
