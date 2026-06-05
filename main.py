import os
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
from telegram.error import InvalidToken
from storage import add_key, get_key, load_keys, save_keys

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

USERS_FILE = "users.json"
HISTORY_FILE = "history.json"
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

STATUS = {"healthy": False, "error": None}


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(data):
    with open(USERS_FILE, "w") as f:
        json.dump(data, f, indent=4)


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_history(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=4)


def get_balance(user_id: int) -> float:
    users = load_users()
    return users.get(str(user_id), 0.0)


def set_balance(user_id: int, amount: float):
    users = load_users()
    users[str(user_id)] = amount
    save_users(users)


def record_history(user_id: int, panel: str, days: str, key: str):
    history = load_history()
    uid = str(user_id)
    if uid not in history:
        history[uid] = []
    history[uid].append({"panel": panel, "days": days, "key": key})
    save_history(history)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ── Commands ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"👋 Welcome, {user.first_name}!\n\n"
        "I am a key management bot. Use the commands below:\n\n"
        "💰 /balance — Check your balance\n"
        "🔑 /buy <panel> <days> — Buy a key\n"
        "📜 /history — View your purchase history\n"
        "📦 /stock — View available stock\n"
    )
    if is_admin(user.id):
        text += (
            "\n🔧 Admin commands:\n"
            "/addkey <panel> <days> <key> — Add a key to stock\n"
            "/addbalance <user_id> <amount> — Add balance to a user\n"
            "/allusers — List all users and balances\n"
        )
    await update.message.reply_text(text)


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bal = get_balance(user.id)
    await update.message.reply_text(f"💰 Your balance: {bal:.2f}")


async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_keys()
    if not data:
        await update.message.reply_text("📦 No stock available.")
        return
    lines = ["📦 Available Stock:\n"]
    for panel, days_dict in data.items():
        for days, keys in days_dict.items():
            count = len(keys)
            if count > 0:
                lines.append(f"  • {panel} / {days} days — {count} key(s)")
    if len(lines) == 1:
        await update.message.reply_text("📦 No stock available at the moment.")
    else:
        await update.message.reply_text("\n".join(lines))


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /buy <panel> <days>")
        return
    panel = context.args[0].lower()
    days = context.args[1]
    user = update.effective_user
    key = get_key(panel, days)
    if key is None:
        await update.message.reply_text(
            f"❌ No keys available for panel '{panel}' / {days} days."
        )
        return
    record_history(user.id, panel, days, key)
    await update.message.reply_text(
        f"✅ Here is your key for {panel} ({days} days):\n\n`{key}`",
        parse_mode="Markdown",
    )


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    hist = load_history()
    uid = str(user.id)
    if uid not in hist or not hist[uid]:
        await update.message.reply_text("📜 No purchase history found.")
        return
    entries = hist[uid][-10:]
    lines = ["📜 Your last purchases:\n"]
    for e in reversed(entries):
        lines.append(f"  • {e['panel']} / {e['days']} days → `{e['key']}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def addkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admins only.")
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /addkey <panel> <days> <key>")
        return
    panel, days, key = context.args[0], context.args[1], " ".join(context.args[2:])
    add_key(panel, days, key)
    await update.message.reply_text(f"✅ Key added to {panel} / {days} days.")


async def addbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admins only.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addbalance <user_id> <amount>")
        return
    try:
        uid = int(context.args[0])
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id or amount.")
        return
    current = get_balance(uid)
    set_balance(uid, current + amount)
    await update.message.reply_text(
        f"✅ Added {amount:.2f} to user {uid}. New balance: {current + amount:.2f}"
    )


async def allusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admins only.")
        return
    users = load_users()
    if not users:
        await update.message.reply_text("No users found.")
        return
    lines = ["👥 All users:\n"]
    for uid, bal in users.items():
        lines.append(f"  • {uid} — {bal:.2f}")
    await update.message.reply_text("\n".join(lines))


# ── Health HTTP server ────────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        if STATUS["error"]:
            self.wfile.write(f"ERROR: {STATUS['error']}".encode())
        elif STATUS["healthy"]:
            self.wfile.write(b"OK - Bot running")
        else:
            self.wfile.write(b"OK - Starting")

    def log_message(self, format, *args):
        pass


def start_health_server() -> HTTPServer:
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"Health server listening on 0.0.0.0:{port}")
    return server


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    # Start health server ONCE — reused even if bot fails
    start_health_server()

    try:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("balance", balance))
        app.add_handler(CommandHandler("stock", stock))
        app.add_handler(CommandHandler("buy", buy))
        app.add_handler(CommandHandler("history", history_cmd))
        app.add_handler(CommandHandler("addkey", addkey))
        app.add_handler(CommandHandler("addbalance", addbalance))
        app.add_handler(CommandHandler("allusers", allusers))

        STATUS["healthy"] = True
        logger.info("Bot starting (long-polling)...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except InvalidToken as e:
        STATUS["healthy"] = False
        STATUS["error"] = str(e)
        logger.error(f"❌ INVALID TELEGRAM BOT TOKEN: {e}")
        logger.error(
            "Please update the TELEGRAM_BOT_TOKEN env var with a valid token from @BotFather "
            "(format: 123456789:ABC-DEFghijklmnoP-Q)."
        )
        # Block forever so the container stays alive for health checks
        import signal
        signal.pause()


if __name__ == "__main__":
    main()
