import os
import sqlite3
import logging
from typing import List, Optional

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
# ENV CONFIG (Render)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")  # مثال: https://xxxx.onrender.com  (بدون / آخر)
ADMIN_ID = int(os.getenv("ADMIN_ID", "6417297177"))  # إنت
SUPPORT_USER = os.getenv("SUPPORT_USER", "@XK6272_bot")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars")
if not APP_URL or not APP_URL.startswith("https://"):
    raise ValueError("APP_URL missing/invalid. Must be like: https://xxxx.onrender.com")

DEFAULT_CHANNELS = ["@animatrix2026", "@animatrix27"]  # اشتراك إجباري

DB_PATH = "bot.db"
REF_REWARD_POINTS = 1  # إحالة واحدة = 1 نقطة

# =========================
# LOGGING
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("animatrix-bot")

# =========================
# DB
# =========================
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            points INTEGER NOT NULL DEFAULT 0,
            referred_by INTEGER,
            referral_rewarded INTEGER NOT NULL DEFAULT 0,
            verified_join INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            cost INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT NOT NULL,
            qty INTEGER NOT NULL DEFAULT 0,
            note TEXT
        )
    """)
    # defaults
    cur.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('channels', ?)", (" ".join(DEFAULT_CHANNELS),))
    cur.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('support', ?)", (SUPPORT_USER,))
    conn.commit()
    conn.close()

def get_setting(key: str, default: str = "") -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT v FROM settings WHERE k=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["v"] if row else default

def set_setting(key: str, value: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
    conn.commit()
    conn.close()

def ensure_user(user_id: int, referred_by: Optional[int] = None):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users(user_id, points, referred_by) VALUES(?,?,?)",
            (user_id, 0, referred_by if referred_by and referred_by != user_id else None),
        )
        conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def add_points(user_id: int, amount: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def mark_verified(user_id: int, verified: int = 1):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET verified_join=? WHERE user_id=?", (verified, user_id))
    conn.commit()
    conn.close()

def reward_referrer_if_needed(user_id: int):
    """Give points to referrer once, when user completes join verification."""
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT referred_by, referral_rewarded, verified_join FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return

    referred_by = row["referred_by"]
    rewarded = row["referral_rewarded"]
    verified = row["verified_join"]

    if referred_by and rewarded == 0 and verified == 1:
        cur.execute("UPDATE users SET referral_rewarded=1 WHERE user_id=?", (user_id,))
        cur.execute("UPDATE users SET points = points + ? WHERE user_id=?", (REF_REWARD_POINTS, referred_by))
        conn.commit()
    conn.close()

# =========================
# HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def parse_channels_args(args: List[str]) -> List[str]:
    chans = []
    for a in args:
        a = a.strip()
        if not a:
            continue
        # accept @username or t.me/username
        if "t.me/" in a:
            a = a.split("t.me/")[-1]
            a = "@" + a.replace("@", "")
        if not a.startswith("@"):
            a = "@" + a
        chans.append(a)
    # remove duplicates keep order
    seen = set()
    out = []
    for c in chans:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out

def required_channels() -> List[str]:
    v = get_setting("channels", " ".join(DEFAULT_CHANNELS)).strip()
    if not v:
        return DEFAULT_CHANNELS
    return v.split()

def support_user() -> str:
    return get_setting("support", SUPPORT_USER) or SUPPORT_USER

def main_menu_kb() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("💰 BALANCE", callback_data="menu_balance"),
            InlineKeyboardButton("👥 REFER", callback_data="menu_refer"),
        ],
        [
            InlineKeyboardButton("🏧 WITHDRAW", callback_data="menu_withdraw"),
            InlineKeyboardButton("🆘 SUPPORT", callback_data="menu_support"),
        ],
        [
            InlineKeyboardButton("🎁 REWARDS", callback_data="menu_rewards"),
            InlineKeyboardButton("📦 STOCK", callback_data="menu_stock"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="menu_home")]])

def join_required_kb(channels: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for c in channels:
        url = f"https://t.me/{c.replace('@','')}"
        rows.append([InlineKeyboardButton(f"🔗 JOIN {c}", url=url)])
    rows.append([InlineKeyboardButton("✅ Joined", callback_data="check_join")])
    return InlineKeyboardMarkup(rows)

async def user_joined_all(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Check if user joined all required channels. Bot must be admin in channels to check."""
    chans = required_channels()
    for ch in chans:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception as e:
            # If bot isn't admin or channel invalid -> treat as not joined (and log it)
            logger.warning("Join check failed for %s: %s", ch, e)
            return False
    return True

async def send_join_gate(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = None):
    chans = required_channels()
    msg = text or (
        "⚠️ لازم تشترك بالقنوات أولاً حتى يفتح البوت.\n\n"
        "بعد ما تشترك اضغط ✅ Joined للتأكيد."
    )
    if update.message:
        await update.message.reply_text(msg, reply_markup=join_required_kb(chans))
    elif update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=join_required_kb(chans))

async def send_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = "✅ Welcome! Select from menu:"
    if update.message:
        await update.message.reply_text(txt, reply_markup=main_menu_kb())
    else:
        await update.callback_query.edit_message_text(txt, reply_markup=main_menu_kb())

def referral_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start={user_id}"

# =========================
# COMMANDS
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Parse referral payload: /start <ref_id>
    referred_by = None
    if context.args:
        try:
            referred_by = int(context.args[0])
        except:
            referred_by = None

    ensure_user(user_id, referred_by=referred_by)

    # Gate: require channel join first
    joined = await user_joined_all(update, context, user_id)
    if not joined:
        await send_join_gate(update, context)
        return

    # Mark verified (joined) in DB
    mark_verified(user_id, 1)
    reward_referrer_if_needed(user_id)

    await send_home(update, context)

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    chans = " ".join(required_channels())
    sup = support_user()
    txt = (
        "🛠️ <b>Admin Panel</b>\n\n"
        f"• Channels: <code>{chans}</code>\n"
        f"• Support: <code>{sup}</code>\n\n"
        "أوامر الأدمن:\n"
        "• /set_channels @ch1 @ch2\n"
        "• /set_support @username\n"
        "• /add_reward Name | cost\n"
        "• /del_reward id\n"
        "• /add_stock item | qty | note(optional)\n"
        "• /set_stock id qty\n"
        "• /ban user_id\n"
        "• /unban user_id\n"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# (اختياري) بان بسيط
def ensure_ban_table():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bans (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

def is_banned(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM bans WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row)

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("اكتب: /ban 123456")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("ID غلط.")
       
