import os
import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")  # مثال: https://xxxx.onrender.com
ADMIN_ID = os.getenv("ADMIN_ID")  # رقم حسابك بالتليجرام
FORCE_CHATS = os.getenv("FORCE_CHATS", "")  # مثال: @ch1,@ch2,@ch3
SUPPORT_USER = os.getenv("SUPPORT_USER", "@Support")  # حساب الدعم
PROOFS_URL = os.getenv("PROOFS_URL", "")  # رابط قناة/بوست الإثباتات

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars")
if not APP_URL:
    raise ValueError("APP_URL missing. Add it in Render Env Vars (must be full https://xxxx.onrender.com)")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BOT")

DB_PATH = "bot.db"

# =========================
# DB
# =========================
def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        points INTEGER DEFAULT 0,
        ref_by INTEGER DEFAULT NULL,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rewards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        cost INTEGER,
        stock INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS withdraw_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        reward_id INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )
    """)

    con.commit()
    con.close()

def get_user(user_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id, username, points, ref_by FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return row

def add_user(user_id: int, username: str, ref_by: int | None):
    con = db()
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, points, ref_by, created_at) VALUES (?, ?, 0, ?, ?)",
                (user_id, username, ref_by, datetime.utcnow().isoformat()))
    con.commit()
    con.close()

def add_points(user_id: int, amount: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
    con.commit()
    con.close()

def set_points(user_id: int, amount: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET points=? WHERE user_id=?", (amount, user_id))
    con.commit()
    con.close()

def list_rewards():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, title, cost, stock FROM rewards ORDER BY id DESC")
    rows = cur.fetchall()
    con.close()
    return rows

def get_reward(reward_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, title, cost, stock FROM rewards WHERE id=?", (reward_id,))
    row = cur.fetchone()
    con.close()
    return row

def create_reward(title: str, cost: int, stock: int):
    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO rewards (title, cost, stock) VALUES (?, ?, ?)", (title, cost, stock))
    con.commit()
    con.close()

def update_stock(reward_id: int, stock: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE rewards SET stock=? WHERE id=?", (stock, reward_id))
    con.commit()
    con.close()

def create_withdraw(user_id: int, reward_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO withdraw_requests (user_id, reward_id, status, created_at) VALUES (?, ?, 'pending', ?)",
                (user_id, reward_id, datetime.utcnow().isoformat()))
    con.commit()
    con.close()

# =========================
# FORCE JOIN
# =========================
def parse_force_chats():
    chats = []
    for x in FORCE_CHATS.split(","):
        x = x.strip()
        if x:
            chats.append(x)
    return chats

async def is_user_joined_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chats = parse_force_chats()
    if not chats:
        return True

    user_id = update.effective_user.id

    for chat in chats:
        try:
            member = await context.bot.get_chat_member(chat_id=chat, user_id=user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception as e:
            logger.warning(f"Join check failed for {chat}: {e}")
            return False
    return True

def join_keyboard():
    chats = parse_force_chats()
    buttons = []

    for ch in chats:
        link = f"https://t.me/{ch.replace('@','')}"
        buttons.append([InlineKeyboardButton("JOIN", url=link)])

    buttons.append([InlineKeyboardButton("💡 [ JOINED ] 💡", callback_data="joined_check")])
    return InlineKeyboardMarkup(buttons)

# =========================
# MENUS
# =========================
def main_menu():
    keyboard = [
        [InlineKeyboardButton("💰 BALANCE", callback_data="balance"),
         InlineKeyboardButton("👥 REFER", callback_data="refer")],
        [InlineKeyboardButton("🏧 WITHDRAW", callback_data="withdraw"),
         InlineKeyboardButton("🆘 SUPPORT", callback_data="support")],
        [InlineKeyboardButton("🧾 PROOFS", callback_data="proofs"),
         InlineKeyboardButton("🎁 REWARDS", callback_data="rewards")],
        [InlineKeyboardButton("📦 STOCK", callback_data="stock")],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="back")]])

# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()

    user = update.effective_user
    user_id = user.id
    username = user.username or ""

    # referral
    ref_by = None
    if context.args:
        try:
            ref_by = int(context.args[0])
            if ref_by == user_id:
                ref_by = None
        except:
            ref_by = None

    add_user(user_id, username, ref_by)

    # referral reward once
    u = get_user(user_id)
    if u and u[3] is not None:  # ref_by exists
        # if user points still 0 and first time, give ref bonus to ref_by
        pass

    joined = await is_user_joined_all(update, context)
    if not joined:
        await update.message.reply_text(
            "👋 Welcome!\n\n"
            "⏳ Join all channels then click [JOINED] to start the bot.",
            reply_markup=join_keyboard()
        )
        return

    await update.message.reply_text(
        "👋 Welcome!\n\nSelect from menu:",
        reply_markup=main_menu()
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    user_id = user.id

    # Force join check always
    if query.data != "joined_check":
        ok = await is_user_joined_all(update, context)
        if not ok:
            await query.edit_message_text(
                "⛔ لازم تشترك بكل القنوات أولاً.\n\nبعدها اضغط [JOINED].",
                reply_markup=join_keyboard()
            )
            return

    if query.data == "joined_check":
        ok = await is_user_joined_all(update, context)
        if not ok:
            await query.edit_message_text(
                "❌ لسه مش مشترك بكل القنوات.\n\nاشترك وبعدين اضغط [JOINED].",
                reply_markup=join_keyboard()
            )
        else:
            await query.edit_message_text(
                "✅ تم التحقق! البوت شغال.\n\nاختر من القائمة:",
                reply_markup=main_menu()
            )
        return

    if query.data == "back":
        await query.edit_message_text("Main menu:", reply_markup=main_menu())
        return

    if query.data == "balance":
        u = get_user(user_id)
        points = u[2] if u else 0
        await query.edit_message_text(
            f"💰 *Your Balance*\n\nPoints: *{points}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_btn()
        )
        return

    if query.data == "refer":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
        await query.edit_message_text(
            "👥 *Referral System*\n\n"
            "Invite friends with your link and earn points.\n\n"
            f"🔗 Your link:\n`{ref_link}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_btn()
        )
        return

    if query.data == "support":
        await query.edit_message_text(
            f"🆘 Support:\n\nContact: {SUPPORT_USER}",
            reply_markup=back_btn()
        )
        return

    if query.data == "proofs":
        if PROOFS_URL:
            await query.edit_message_text(
                f"🧾 Proofs:\n\n{PROOFS_URL}",
                reply_markup=back_btn()
            )
        else:
            await query.edit_message_text(
                "🧾 Proofs not set yet.",
                reply_markup=back_btn()
            )
        return

    if query.data == "stock":
        rewards = list_rewards()
        if not rewards:
            await query.edit_message_text("📦 No rewards added yet.", reply_markup=back_btn())
            return

        text = "📦 *Stock*\n\n"
        for r in rewards:
            text += f"• {r[1]} | cost: {r[2]} | stock: {r[3]}\n"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_btn())
        return

    if query.data == "rewards":
        rewards = list_rewards()
        if not rewards:
            await query.edit_message_text("🎁 No rewards yet.", reply_markup=back_btn())
            return

        buttons = []
        for r in rewards:
            rid, title, cost, stock = r
            buttons.append([InlineKeyboardButton(f"{title} ({cost} pts)", callback_data=f"reward_{rid}")])
        buttons.append([InlineKeyboardButton("⬅️ BACK", callback_data="back")])

        await query.edit_message_text(
            "🎁 Choose reward:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if query.data.startswith("reward_"):
        rid = int(query.data.split("_")[1])
        r = get_reward(rid)
        if not r:
            await query.edit_message_text("❌ Reward not found.", reply_markup=back_btn())
            return

        _, title, cost, stock = r
        buttons = [
            [InlineKeyboardButton("✅ Redeem", callback_data=f"redeem_{rid}")],
            [InlineKeyboardButton("⬅️ BACK", callback_data="rewards")]
        ]
        await query.edit_message_text(
            f"🎁 *Reward*\n\n"
            f"Name: *{title}*\n"
            f"Cost: *{cost} points*\n"
            f"Stock: *{stock}*\n",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if query.data.startswith("redeem_"):
        rid = int(query.data.split("_")[1])
        r = get_reward(rid)
        if not r:
            await query.edit_message_text("❌ Reward not found.", reply_markup=back_btn())
            return

        _, title, cost, stock = r
        u = get_user(user_id)
        points = u[2] if u else 0

        if stock <= 0:
            await query.edit_message_text("❌ Out of stock.", reply_markup=back_btn())
            return

        if points < cost:
            await query.edit_message_text("❌ Not enough points.", reply_markup=back_btn())
            return

        # deduct points + reduce stock
        con = db()
        cur = con.cursor()
        cur.execute("UPDATE users SET points = points - ? WHERE user_id=?", (cost, user_id))
        cur.execute("UPDATE rewards SET stock = stock - 1 WHERE id=?", (rid,))
        con.commit()
        con.close()

        create_withdraw(user_id, rid)

        await query.edit_message_text(
            f"✅ Redeem request sent!\n\nReward: {title}\nStatus: pending",
            reply_markup=back_btn()
        )
        return

    if query.data == "withdraw":
        await query.edit_message_text(
            "🏧 Withdraw = Redeem rewards.\n\nGo to 🎁 REWARDS and redeem.",
            reply_markup=back_btn()
        )
        return

# =========================
# ADMIN COMMANDS
# =========================
def is_admin(user_id: int) -> bool:
    return ADMIN_ID and str(user_id) == str(ADMIN_ID)

async def admin_add_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    # /addreward title | cost | stock
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text("Usage:\n/addreward Title | cost | stock")
        return

    parts = [x.strip() for x in text.split("|")]
    if len(parts) != 3:
        await update.message.reply_text("Usage:\n/addreward Title | cost | stock")
        return

    title = parts[0]
    cost = int(parts[1])
    stock = int(parts[2])

    create_reward(title, cost, stock)
    await update.message.reply_text("✅ Reward added!")

async def admin_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    # /addpoints user_id amount
    if len(context.args) != 2:
        await update.message.reply_text("Usage:\n/addpoints user_id amount")
        return
    uid = int(context.args[0])
    amount = int(context.args[1])
    add_points(uid, amount)
    await update.message.reply_text("✅ Points added!")

# =========================
# MAIN
# =========================
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))

    # admin
    app.add_handler(CommandHandler("addreward", admin_add_reward))
    app.add_handler(CommandHandler("addpoints", admin_add_points))

    port = int(os.environ.get("PORT", "10000"))

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()
