import os
import json
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
ADMIN_ID = int(os.getenv("ADMIN_ID", "6417297177"))  # ID تبعك
DB_PATH = "bot.db"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars")
if not APP_URL or not APP_URL.startswith("https://"):
    raise ValueError("APP_URL missing or invalid. Must be full https://xxxx.onrender.com")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("animatrix-bot")

# =========================
# DB HELPERS
# =========================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        points INTEGER DEFAULT 0,
        referred_by INTEGER,
        joined INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rewards (
        reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        cost INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock_codes (
        code_id INTEGER PRIMARY KEY AUTOINCREMENT,
        reward_name TEXT,
        code TEXT,
        used INTEGER DEFAULT 0,
        used_by INTEGER,
        used_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    conn.commit()
    conn.close()

def set_setting(key: str, value: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    conn.close()

def get_setting(key: str, default=None):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default

# =========================
# SETTINGS DEFAULTS
# =========================
def ensure_defaults():
    if get_setting("channels") is None:
        # قنوات اشتراك اجباري افتراضية
        set_setting("channels", json.dumps(["@animatrix2026", "@animatrix27"]))
    if get_setting("reward_per_ref") is None:
        set_setting("reward_per_ref", "1")  # نقطة لكل إحالة
    if get_setting("support_user") is None:
        set_setting("support_user", "@Support")
    if get_setting("proofs_channel") is None:
        set_setting("proofs_channel", "@proofs")

# =========================
# UTIL
# =========================
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def parse_channels() -> list[str]:
    raw = get_setting("channels", "[]")
    try:
        return json.loads(raw)
    except:
        return []

def mention_user(u):
    if u.username:
        return f"@{u.username}"
    return u.full_name

async def must_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Returns True if user is allowed (joined channels)
    Returns False if still not joined -> show join buttons
    """
    user = update.effective_user
    channels = parse_channels()

    if not channels:
        return True

    not_joined = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user.id)
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception:
            # إذا القناة خاصة/البوت مش أدمن فيها
            not_joined.append(ch)

    if not_joined:
        buttons = []
        for ch in not_joined:
            url = f"https://t.me/{ch.replace('@','')}"
            buttons.append([InlineKeyboardButton("JOIN", url=url)])
        buttons.append([InlineKeyboardButton("✅ JOINED", callback_data="joined_check")])

        text = (
            "👮‍♂️ *Mandatory Subscription*\n\n"
            "⏳ Join all channels then click *JOINED* to start the bot."
        )
        if update.message:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        return False

    return True

def add_user_if_not_exists(user, referred_by=None):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    row = cur.fetchone()
    if not row:
        cur.execute("""
        INSERT INTO users(user_id, username, first_name, points, referred_by, joined, is_banned, created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """, (
            user.id,
            user.username,
            user.first_name,
            0,
            referred_by,
            0,
            0,
            datetime.utcnow().isoformat()
        ))
        conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def set_joined(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET joined=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

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
    if row["points"] < amount:
        conn.close()
        return False
    cur.execute("UPDATE users SET points = points - ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()
    return True

def ban_user(user_id: int, val: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_banned=? WHERE user_id=?", (val, user_id))
    conn.commit()
    conn.close()

# =========================
# UI
# =========================
def main_menu():
    kb = [
        [InlineKeyboardButton("💰 BALANCE", callback_data="balance"),
         InlineKeyboardButton("👥 REFER", callback_data="refer")],
        [InlineKeyboardButton("🏧 WITHDRAW", callback_data="withdraw"),
         InlineKeyboardButton("🆘 SUPPORT", callback_data="support")],
        [InlineKeyboardButton("🧾 PROOFS", callback_data="proofs"),
         InlineKeyboardButton("🎁 REWARDS", callback_data="rewards")],
        [InlineKeyboardButton("📦 STOCK", callback_data="stock")],
    ]
    return InlineKeyboardMarkup(kb)

def back_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ BACK", callback_data="back")]])

def admin_menu():
    kb = [
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("🎁 Manage Rewards", callback_data="admin_rewards")],
        [InlineKeyboardButton("📦 Manage Stock", callback_data="admin_stock")],
        [InlineKeyboardButton("🚫 Ban/Unban", callback_data="admin_ban")],
        [InlineKeyboardButton("⬅ BACK", callback_data="back")],
    ]
    return InlineKeyboardMarkup(kb)

# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # banned?
    row = get_user(user.id)
    if row and row["is_banned"] == 1:
        await update.message.reply_text("🚫 You are banned.")
        return

    # referral
    referred_by = None
    if context.args:
        try:
            referred_by = int(context.args[0])
        except:
            referred_by = None

    add_user_if_not_exists(user, referred_by=referred_by)

    # mandatory join screen first
    allowed = await must_join(update, context)
    if not allowed:
        return

    # mark joined + reward referral once
    row = get_user(user.id)
    if row and row["joined"] == 0:
        set_joined(user.id)

        # referral reward
        if row["referred_by"]:
            ref_id = int(row["referred_by"])
            if ref_id != user.id:
                reward = int(get_setting("reward_per_ref", "1"))
                add_points(ref_id, reward)
                try:
                    await context.bot.send_message(
                        chat_id=ref_id,
                        text=f"🎉 New referral joined!\n+{reward} point(s)."
                    )
                except:
                    pass

    text = "✅ *Welcome!* Select from menu:"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

async def joined_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # user clicked JOINED
    allowed = await must_join(update, context)
    if not allowed:
        return
    await update.callback_query.edit_message_text(
        "✅ Joined successfully! Use /start again.",
        reply_markup=None
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    row = get_user(user.id)
    if row and row["is_banned"] == 1:
        await query.edit_message_text("🚫 You are banned.")
        return

    # mandatory join for any action
    if query.data not in ("joined_check",):
        allowed = await must_join(update, context)
        if not allowed:
            return

    if query.data == "back":
        await query.edit_message_text("Main menu:", reply_markup=main_menu())
        return

    if query.data == "balance":
        row = get_user(user.id)
        pts = row["points"] if row else 0
        await query.edit_message_text(f"💰 Your balance: *{pts}* point(s).", parse_mode=ParseMode.MARKDOWN, reply_markup=back_btn())
        return

    if query.data == "refer":
        link = f"https://t.me/{context.bot.username}?start={user.id}"
        reward = get_setting("reward_per_ref", "1")
        await query.edit_message_text(
            f"👥 *REFER*\n\n"
            f"🔗 Your link:\n`{link}`\n\n"
            f"⭐ Reward per join+verify: *{reward}* point(s).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_btn()
        )
        return

    if query.data == "withdraw":
        await query.edit_message_text(
            "🏧 Withdraw is under development.\nContact support.",
            reply_markup=back_btn()
        )
        return

    if query.data == "support":
        sup = get_setting("support_user", "@Support")
        await query.edit_message_text(f"🆘 Support: {sup}", reply_markup=back_btn())
        return

    if query.data == "proofs":
        proofs = get_setting("proofs_channel", "@proofs")
        await query.edit_message_text(f"🧾 Proofs channel: {proofs}", reply_markup=back_btn())
        return

    if query.data == "rewards":
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT name,cost FROM rewards ORDER BY cost ASC")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await query.edit_message_text("🎁 No rewards set yet.", reply_markup=back_btn())
            return

        text = "🎁 *Rewards list:*\n\n"
        kb = []
        for r in rows:
            text += f"• {r['name']} — {r['cost']} point(s)\n"
            kb.append([InlineKeyboardButton(f"Buy: {r['name']}", callback_data=f"buy:{r['name']}")])
        kb.append([InlineKeyboardButton("⬅ BACK", callback_data="back")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        return

    if query.data.startswith("buy:"):
        reward_name = query.data.split("buy:", 1)[1]

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT cost FROM rewards WHERE name=?", (reward_name,))
        r = cur.fetchone()
        conn.close()

        if not r:
            await query.edit_message_text("Reward not found.", reply_markup=back_btn())
            return

        cost = int(r["cost"])
        ok = deduct_points(user.id, cost)
        if not ok:
            await query.edit_message_text("❌ Not enough points.", reply_markup=back_btn())
            return

        # give code from stock
        conn = db()
        cur = conn.cursor()
        cur.execute("""
        SELECT code_id, code FROM stock_codes
        WHERE reward_name=? AND used=0
        ORDER BY code_id ASC LIMIT 1
        """, (reward_name,))
        code_row = cur.fetchone()

        if not code_row:
            conn.close()
            # refund
            add_points(user.id, cost)
            await query.edit_message_text("❌ No stock available for this reward. Points refunded.", reply_markup=back_btn())
            return

        code_id = code_row["code_id"]
        code = code_row["code"]
        cur.execute("""
        UPDATE stock_codes
        SET used=1, used_by=?, used_at=?
        WHERE code_id=?
        """, (user.id, datetime.utcnow().isoformat(), code_id))
        conn.commit()
        conn.close()

        await query.edit_message_text(
            f"✅ Purchase complete!\n\n🎁 Reward: {reward_name}\n🔑 Code:\n`{code}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_btn()
        )
        return

    if query.data == "stock":
        conn = db()
        cur = conn.cursor()
        cur.execute("""
        SELECT reward_name, COUNT(*) as cnt
        FROM stock_codes
        WHERE used=0
        GROUP BY reward_name
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await query.edit_message_text("📦 Stock empty.", reply_markup=back_btn())
            return

        text = "📦 *Available stock:*\n\n"
        for r in rows:
            text += f"• {r['reward_name']} — {r['cnt']} code(s)\n"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_btn())
        return

    # ADMIN PANEL ENTRY
    if query.data == "admin":
        if not is_admin(user.id):
            await query.edit_message_text("❌ Not allowed.")
            return
        await query.edit_message_text("👑 Admin Panel:", reply_markup=admin_menu())
        return

    if query.data == "admin_settings":
        if not is_admin(user.id):
            await query.edit_message_text("❌ Not allowed.")
            return
        chs = parse_channels()
        sup = get_setting("support_user", "@Support")
        proofs = get_setting("proofs_channel", "@proofs")
        reward = get_setting("reward_per_ref", "1")

        await query.edit_message_text(
            "⚙️ *Settings*\n\n"
            f"📌 Channels: `{', '.join(chs)}`\n"
            f"⭐ Reward per referral: `{reward}`\n"
            f"🆘 Support: `{sup}`\n"
            f"🧾 Proofs: `{proofs}`\n\n"
            "Commands:\n"
            "`/set_channels @ch1 @ch2`\n"
            "`/set_support @username`\n"
            "`/set_proofs @channel`\n"
            "`/set_ref_reward 3`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_menu()
        )
        return

    if query.data == "admin_rewards":
        if not is_admin(user.id):
            await query.edit_message_text("❌ Not allowed.")
            return
        await query.edit_message_text(
            "🎁 *Manage Rewards*\n\n"
            "Commands:\n"
            "`/add_reward Premium 5`\n"
            "`/del_reward Premium`\n"
            "`/list_rewards`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_menu()
        )
        return

    if query.data == "admin_stock":
        if not is_admin(user.id):
            await query.edit_message_text("❌ Not allowed.")
            return
        await query.edit_message_text(
            "📦 *Manage Stock*\n\n"
            "Commands:\n"
            "`/add_stock Premium CODE123`\n"
            "`/add_stock Premium CODE456`\n"
            "`/list_stock Premium`\n"
            "`/clear_stock Premium`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_menu()
        )
        return

    if query.data == "admin_ban":
        if not is_admin(user.id):
            await query.edit_message_text("❌ Not allowed.")
            return
        await query.edit_message_text(
            "🚫 *Ban/Unban*\n\n"
            "Commands:\n"
            "`/ban 123456789`\n"
            "`/unban 123456789`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_menu()
        )
        return

    # default
    await query.edit_message_text("Unknown action.", reply_markup=back_btn())

# =========================
# ADMIN COMMANDS
# =========================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("👑 Admin Panel:", reply_markup=admin_menu())

async def set_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_channels @ch1 @ch2")
        return
    channels = []
    for a in context.args:
        if not a.startswith("@"):
            a = "@" + a
        channels.append(a)
    set_setting("channels", json.dumps(channels))
    await update.message.reply_text(f"✅ Channels updated: {', '.join(channels)}")

async def set_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_support @username")
        return
    sup = context.args[0]
    if not sup.startswith("@"):
        sup = "@" + sup
    set_setting("support_user", sup)
    await update.message.reply_text(f"✅ Support set to: {sup}")

async def set_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_proofs @channel")
        return
    ch = context.args[0]
    if not ch.startswith("@"):
        ch = "@" + ch
    set_setting("proofs_channel", ch)
    await update.message.reply_text(f"✅ Proofs channel set to: {ch}")

async def set_ref_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_ref_reward 3")
        return
    try:
        val = int(context.args[0])
        set_setting("reward_per_ref", str(val))
        await update.message.reply_text(f"✅ Referral reward set to: {val}")
    except:
        await update.message.reply_text("❌ Must be a number.")

async def add_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /add_reward Name Cost")
        return
    name = context.args[0]
    try:
        cost = int(context.args[1])
    except:
        await update.message.reply_text("Cost must be number.")
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO rewards(name,cost) VALUES(?,?)", (name, cost))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Reward saved: {name} ({cost} points)")

async def del_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /del_reward Name")
        return
    name = context.args[0]
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rewards WHERE name=?", (name,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Reward deleted: {name}")

async def list_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT name,cost FROM rewards ORDER BY cost ASC")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No rewards.")
        return
    text = "🎁 Rewards:\n\n"
    for r in rows:
        text += f"- {r['name']} : {r['cost']} points\n"
    await update.message.reply_text(text)

async def add_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /add_stock RewardName CODE")
        return
    reward = context.args[0]
    code = " ".join(context.args[1:]).strip()

    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO stock_codes(reward_name, code, used) VALUES(?,?,0)", (reward, code))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Stock added for {reward}")

async def list_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /list_stock RewardName")
        return
    reward = context.args[0]
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM stock_codes WHERE reward_name=? AND used=0", (reward,))
    c = cur.fetchone()["c"]
    conn.close()
    await update.message.reply_text(f"📦 Stock for {reward}: {c} code(s) available")

async def clear_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /clear_stock RewardName")
        return
    reward = context.args[0]
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM stock_codes WHERE reward_name=?", (reward,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🗑️ Stock cleared for {reward}")

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ban USER_ID")
        return
    uid = int(context.args[0])
    ban_user(uid, 1)
    await update.message.reply_text(f"🚫 Banned: {uid}")

async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unban USER_ID")
        return
    uid = int(context.args[0])
    ban_user(uid, 0)
    await update.message.reply_text(f"✅ Unbanned: {uid}")

# =========================
# MAIN WEBHOOK
# =========================
def main():
    init_db()
    ensure_defaults()

    application = Application.builder().token(BOT_TOKEN).build()

    # user handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_button))

    # admin commands
    application.add_handler(CommandHandler("admin", admin_cmd))
    application.add_handler(CommandHandler("set_channels", set_channels))
    application.add_handler(CommandHandler("set_support", set_support))
    application.add_handler(CommandHandler("set_proofs", set_proofs))
    application.add_handler(CommandHandler("set_ref_reward", set_ref_reward))

    application.add_handler(CommandHandler("add_reward", add_reward))
    application.add_handler(CommandHandler("del_reward", del_reward))
    application.add_handler(CommandHandler("list_rewards", list_rewards))

    application.add_handler(CommandHandler("add_stock", add_stock))
    application.add_handler(CommandHandler("list_stock", list_stock))
    application.add_handler(CommandHandler("clear_stock", clear_stock))

    application.add_handler(CommandHandler("ban", ban_cmd))
    application.add_handler(CommandHandler("unban", unban_cmd))

    # joined check
    application.add_handler(CallbackQueryHandler(joined_check, pattern="^joined_check$"))

    port = int(os.environ.get("PORT", "10000"))

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()
