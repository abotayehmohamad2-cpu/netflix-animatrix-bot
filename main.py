import os
import sqlite3
import time
import logging
from typing import Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
APP_URL = os.getenv("APP_URL", "").strip().rstrip("/")  # https://xxxx.onrender.com
ADMIN_ID = int(os.getenv("ADMIN_ID", "6417297177").strip() or "6417297177")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars")
if not APP_URL or not APP_URL.startswith("https://"):
    raise ValueError("APP_URL missing/invalid. Must be full https://xxxx.onrender.com in Render Env Vars")

# =========================
# LOGGING
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("netflix-bot")

# =========================
# DB
# =========================
DB_PATH = "bot.db"

def now_ts() -> int:
    return int(time.time())

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        points INTEGER NOT NULL DEFAULT 0,
        ref_by INTEGER,
        joined_verified INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )
    """)

    # Stock items (Netflix accounts/codes)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item TEXT NOT NULL,
        added_at INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        used_by INTEGER
    )
    """)

    conn.commit()
    conn.close()

def set_setting(k: str, v: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    conn.commit()
    conn.close()

def get_setting(k: str, default: str = "") -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT v FROM settings WHERE k=?", (k,))
    row = cur.fetchone()
    conn.close()
    return row["v"] if row else default

def ensure_user(user_id: int, ref_by: Optional[int] = None):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    exists = cur.fetchone() is not None
    if not exists:
        cur.execute(
            "INSERT INTO users(user_id, points, ref_by, joined_verified, created_at) VALUES(?,?,?,?,?)",
            (user_id, 0, ref_by, 0, now_ts())
        )
    conn.commit()
    conn.close()

def get_points(user_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["points"]) if row else 0

def add_points(user_id: int, amount: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def deduct_points(user_id: int, amount: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    pts = int(row["points"])
    if pts < amount:
        conn.close()
        return False
    cur.execute("UPDATE users SET points = points - ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()
    return True

def get_joined_verified(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT joined_verified FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["joined_verified"] == 1)

def set_joined_verified(user_id: int, value: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET joined_verified=? WHERE user_id=?", (value, user_id))
    conn.commit()
    conn.close()

def get_ref_by(user_id: int) -> Optional[int]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT ref_by FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["ref_by"]) if row and row["ref_by"] is not None else None

def set_channels(ch1: str, ch2: str):
    set_setting("ch1", ch1.strip())
    set_setting("ch2", ch2.strip())

def get_channels() -> Tuple[str, str]:
    return get_setting("ch1", "").strip(), get_setting("ch2", "").strip()

def set_support(username: str):
    set_setting("support", username.strip())

def get_support() -> str:
    return get_setting("support", "@Support").strip() or "@Support"

def set_help_center(text: str):
    set_setting("help_center", text)

def get_help_center() -> str:
    return get_setting(
        "help_center",
        "Help Center:\n- Join required channels then press ✅ JOINED\n- Earn points via referrals\n- Buy Netflix account from Withdraw"
    ).strip()

def set_ref_reward(points: int):
    set_setting("ref_reward", str(int(points)))

def get_ref_reward() -> int:
    v = get_setting("ref_reward", "1")
    try:
        return int(v)
    except:
        return 1

def set_netflix_price(points: int):
    set_setting("netflix_price", str(int(points)))

def get_netflix_price() -> int:
    v = get_setting("netflix_price", "4")  # default like your screenshot
    try:
        return int(v)
    except:
        return 4

# ===== Stock
def add_stock_items(lines: list[str]):
    conn = db()
    cur = conn.cursor()
    for line in lines:
        item = line.strip()
        if item:
            cur.execute("INSERT INTO stock(item, added_at, used, used_by) VALUES(?,?,0,NULL)", (item, now_ts()))
    conn.commit()
    conn.close()

def stock_count_available() -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM stock WHERE used=0")
    row = cur.fetchone()
    conn.close()
    return int(row["c"]) if row else 0

def pop_stock_item_for_user(user_id: int) -> Optional[str]:
    """Atomically reserve one stock item."""
    conn = db()
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    cur.execute("SELECT id, item FROM stock WHERE used=0 ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return None
    sid = int(row["id"])
    item = row["item"]
    cur.execute("UPDATE stock SET used=1, used_by=? WHERE id=?", (user_id, sid))
    conn.commit()
    conn.close()
    return item

# =========================
# Telegram helpers
# =========================
def normalize_channel(x: str) -> str:
    x = (x or "").strip()
    if not x:
        return ""
    if x.startswith("https://"):
        return x
    if x.startswith("@"):
        return x
    return f"@{x}"

async def is_member(context: ContextTypes.DEFAULT_TYPE, channel: str, user_id: int) -> bool:
    if not channel:
        return True
    try:
        chat_id = channel if channel.startswith("@") else channel
        m = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"get_chat_member failed for {channel}: {e}")
        return False

def main_menu_kb(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("🧑‍🤝‍🧑 Referral", callback_data="referral")],
        [InlineKeyboardButton("💵 Withdraw", callback_data="withdraw"),
         InlineKeyboardButton("🆘 Support", callback_data="support")],
        [InlineKeyboardButton("❓ Help Center", callback_data="help_center")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 Admin", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home")]])

def join_gate_kb(ch1: str, ch2: str) -> InlineKeyboardMarkup:
    rows = []
    if ch1:
        url = ch1 if ch1.startswith("https://") else f"https://t.me/{ch1.lstrip('@')}"
        rows.append([InlineKeyboardButton("➡️ Join Channel 1", url=url)])
    if ch2:
        url = ch2 if ch2.startswith("https://") else f"https://t.me/{ch2.lstrip('@')}"
        rows.append([InlineKeyboardButton("➡️ Join Channel 2", url=url)])
    rows.append([InlineKeyboardButton("✅ JOINED", callback_data="verify_join")])
    return InlineKeyboardMarkup(rows)

def admin_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⚙️ Set Channels", callback_data="admin_help_channels")],
        [InlineKeyboardButton("🎯 Set Referral Reward", callback_data="admin_help_refreward")],
        [InlineKeyboardButton("💸 Set Netflix Price", callback_data="admin_help_price")],
        [InlineKeyboardButton("📦 Stock (add/view)", callback_data="admin_help_stock")],
        [InlineKeyboardButton("🆘 Set Support", callback_data="admin_help_support")],
        [InlineKeyboardButton("❓ Set Help Center", callback_data="admin_help_helpcenter")],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")],
    ]
    return InlineKeyboardMarkup(rows)

def withdraw_kb() -> InlineKeyboardMarkup:
    price = get_netflix_price()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎁 Netflix Account [{price} Points]", callback_data="buy_netflix")],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")]
    ])

# =========================
# Gate logic
# =========================
async def user_passed_gate(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if get_joined_verified(user_id):
        return True

    ch1, ch2 = get_channels()
    ch1 = normalize_channel(ch1)
    ch2 = normalize_channel(ch2)

    if not ch1 and not ch2:
        set_joined_verified(user_id, 1)
        return True

    ok1 = await is_member(context, ch1, user_id) if ch1 else True
    ok2 = await is_member(context, ch2, user_id) if ch2 else True
    if ok1 and ok2:
        set_joined_verified(user_id, 1)
        return True

    text = "❌ Join channel first!\n\nJoin both channels then press ✅ JOINED"
    kb = join_gate_kb(ch1, ch2)
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    return False

async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    ensure_user(user.id)
    if not await user_passed_gate(update, context, user.id):
        return

    is_admin = (user.id == ADMIN_ID)
    text = "✅ Welcome! Select from menu:"
    kb = main_menu_kb(is_admin)

    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb)

# =========================
# Commands
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    ref_by = None
    if context.args:
        arg = context.args[0].strip()
        if arg.isdigit():
            ref_by = int(arg)

    ensure_user(user.id, ref_by=ref_by)
    await show_home(update, context)

def is_admin_user(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def set_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage:\n/set_channels @channel1 @channel2")
        return
    ch1 = normalize_channel(context.args[0])
    ch2 = normalize_channel(context.args[1])
    set_channels(ch1, ch2)
    await update.message.reply_text(f"✅ Channels set:\n1) {ch1}\n2) {ch2}\n\n⚠️ Make bot admin in both channels.")

async def set_support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_support @Support")
        return
    set_support(context.args[0])
    await update.message.reply_text(f"✅ Support set to: {get_support()}")

async def set_helpcenter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        return
    txt = update.message.text.split(" ", 1)
    if len(txt) < 2 or not txt[1].strip():
        await update.message.reply_text("Usage: /set_helpcenter <text>")
        return
    set_help_center(txt[1].strip())
    await update.message.reply_text("✅ Help Center updated.")

async def set_refreward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /set_refreward 1")
        return
    set_ref_reward(int(context.args[0]))
    await update.message.reply_text(f"✅ Referral reward set to {get_ref_reward()} point(s).")

async def set_price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /set_price 4")
        return
    set_netflix_price(int(context.args[0]))
    await update.message.reply_text(f"✅ Netflix price set to {get_netflix_price()} points.")

async def add_stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        return

    # /add_stock then next lines as accounts
    text = update.message.text
    parts = text.split("\n", 1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "Usage:\n/add_stock\nemail:pass\nemail2:pass2\n...\n\n(Each line = one account/code)"
        )
        return

    lines = [x.strip() for x in parts[1].splitlines() if x.strip()]
    add_stock_items(lines)
    await update.message.reply_text(f"✅ Added {len(lines)} item(s).\nAvailable stock: {stock_count_available()}")

async def stock_count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        return
    await update.message.reply_text(f"📦 Available stock: {stock_count_available()}")

async def reset_me_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    ensure_user(user.id)
    set_joined_verified(user.id, 0)
    await update.message.reply_text("✅ Reset done. Send /start again.")

# =========================
# Buttons
# =========================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    user = update.effective_user
    if not user:
        return
    ensure_user(user.id)

    data = q.data

    if data == "verify_join":
        passed = await user_passed_gate(update, context, user.id)
        if passed:
            # Pay referrer ONLY once, after user verifies join
            ref_by = get_ref_by(user.id)
            if ref_by and not get_setting(f"ref_paid_{user.id}", ""):
                add_points(ref_by, get_ref_reward())
                set_setting(f"ref_paid_{user.id}", "1")
            await show_home(update, context)
        return

    if data == "home":
        await show_home(update, context)
        return

    # Block everything until passed gate
    if not await user_passed_gate(update, context, user.id):
        return

    admin = is_admin_user(user.id)

    if data == "balance":
        pts = get_points(user.id)
        await q.edit_message_text(f"💰 Your balance: *{pts}* points.", parse_mode="Markdown", reply_markup=back_kb())

    elif data == "referral":
        link = f"https://t.me/{context.bot.username}?start={user.id}"
        reward = get_ref_reward()
        await q.edit_message_text(
            "🧑‍🤝‍🧑 *Referral*\n\n"
            f"Your link:\n`{link}`\n\n"
            f"⭐ Reward per join+verify: *{reward}* point(s).",
            parse_mode="Markdown",
            reply_markup=back_kb(),
        )

    elif data == "withdraw":
        pts = get_points(user.id)
        price = get_netflix_price()
        stock = stock_count_available()
        await q.edit_message_text(
            "📩 You can exchange your points for Netflix accounts.\n\n"
            f"💰 Balance: *{pts}* points\n"
            f"📦 Stock available: *{stock}*\n",
            parse_mode="Markdown",
            reply_markup=withdraw_kb(),
        )

    elif data == "buy_netflix":
        price = get_netflix_price()
        pts = get_points(user.id)

        if pts < price:
            await q.edit_message_text(
                f"❌ Not enough points.\n\nPrice: {price}\nYour balance: {pts}",
                reply_markup=back_kb()
            )
            return

        if stock_count_available() <= 0:
            await q.edit_message_text(
                "❌ Out of stock.\n\nPlease try later.",
                reply_markup=back_kb()
            )
            return

        # Reserve stock item first (so no double-spend)
        item = pop_stock_item_for_user(user.id)
        if not item:
            await q.edit_message_text("❌ Out of stock.", reply_markup=back_kb())
            return

        # Deduct points
        ok = deduct_points(user.id, price)
        if not ok:
            # rollback note: stock already marked used, but this is rare; admin can re-add.
            await q.edit_message_text("❌ Purchase failed (points). Try again.", reply_markup=back_kb())
            return

        # Send account securely in chat
        await q.edit_message_text("✅ Purchased successfully! Check the message below 👇", reply_markup=back_kb())
        await context.bot.send_message(
            chat_id=user.id,
            text=f"🎁 Netflix Account:\n\n`{item}`\n\n✅ Keep it private.",
            parse_mode="Markdown"
        )

    elif data == "support":
        await q.edit_message_text(f"🆘 Support: {get_support()}", reply_markup=back_kb())

    elif data == "help_center":
        await q.edit_message_text(get_help_center(), reply_markup=back_kb())

    elif data == "admin":
        if not admin:
            await q.edit_message_text("Not allowed.", reply_markup=back_kb())
            return
        await q.edit_message_text(
            "🛠 Admin Panel\n\n"
            "Commands:\n"
            "/set_channels @ch1 @ch2\n"
            "/set_refreward 1\n"
            "/set_price 4\n"
            "/add_stock (then lines)\n"
            "/stock_count\n"
            "/set_support @Support\n"
            "/set_helpcenter text...\n"
            "/reset_me (test join gate)\n",
            reply_markup=admin_kb(),
        )

    elif data == "admin_help_channels" and admin:
        await q.edit_message_text(
            "⚙️ Set required channels\n\nSend:\n/set_channels @channel1 @channel2\n\n"
            "⚠️ Make the bot ADMIN in both channels.",
            reply_markup=admin_kb(),
        )

    elif data == "admin_help_refreward" and admin:
        await q.edit_message_text("🎯 Send:\n/set_refreward 1", reply_markup=admin_kb())

    elif data == "admin_help_price" and admin:
        await q.edit_message_text("💸 Send:\n/set_price 4", reply_markup=admin_kb())

    elif data == "admin_help_stock" and admin:
        await q.edit_message_text(
            "📦 Stock\n\nAdd accounts/codes like this:\n"
            "/add_stock\n"
            "email:pass\n"
            "email2:pass2\n\n"
            "Check count:\n/stock_count",
            reply_markup=admin_kb(),
        )

    elif data == "admin_help_support" and admin:
        await q.edit_message_text("🆘 Send:\n/set_support @Support", reply_markup=admin_kb())

    elif data == "admin_help_helpcenter" and admin:
        await q.edit_message_text("❓ Send:\n/set_helpcenter Your message...", reply_markup=admin_kb())

# =========================
# MAIN
# =========================
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))

    # Admin
    app.add_handler(CommandHandler("set_channels", set_channels_cmd))
    app.add_handler(CommandHandler("set_support", set_support_cmd))
    app.add_handler(CommandHandler("set_helpcenter", set_helpcenter_cmd))
    app.add_handler(CommandHandler("set_refreward", set_refreward_cmd))
    app.add_handler(CommandHandler("set_price", set_price_cmd))
    app.add_handler(CommandHandler("add_stock", add_stock_cmd))
    app.add_handler(CommandHandler("stock_count", stock_count_cmd))
    app.add_handler(CommandHandler("reset_me", reset_me_cmd))

    app.add_handler(CallbackQueryHandler(on_button))

    port = int(os.environ.get("PORT", "10000"))

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()
