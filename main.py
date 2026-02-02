import os
import re
import time
import sqlite3
import logging
import asyncio
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
PORT = int(os.environ.get("PORT", "10000"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing (Render Env Vars)")
if not APP_URL.startswith("https://"):
    raise ValueError("APP_URL invalid. Example: https://xxxx.onrender.com")

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("rewards-bot")

# =========================
# DB
# =========================
DB_PATH = "bot.db"

def now_ts() -> int:
    return int(time.time())

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()

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
        ref_paid INTEGER NOT NULL DEFAULT 0,
        join_verified INTEGER NOT NULL DEFAULT 0,
        banned INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item TEXT NOT NULL,          -- الكود/القسيمة/المنتج الرقمي
        used INTEGER NOT NULL DEFAULT 0,
        used_by INTEGER,
        used_at INTEGER,
        added_at INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        item TEXT NOT NULL,
        cost INTEGER NOT NULL,
        delivered_text TEXT,
        created_at INTEGER NOT NULL
    )
    """)

    con.commit()
    con.close()

def set_setting(k: str, v: str):
    con = db()
    con.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    con.commit()
    con.close()

def get_setting(k: str, default: str = "") -> str:
    con = db()
    row = con.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    con.close()
    return row["v"] if row else default

def ensure_defaults():
    # قنوات اشتراك اجباري افتراضيًا (تقدر تغيرهم)
    if not get_setting("ch1"):
        set_setting("ch1", "@animatrix2026")
    if not get_setting("ch2"):
        set_setting("ch2", "@animatrix27")

    if not get_setting("support"):
        set_setting("support", "@Support")
    if not get_setting("help_center"):
        set_setting("help_center", "Help Center:\n- Join required channels then press ✅ JOINED\n- Earn points via referrals\n- Use Withdraw to redeem your reward")

    if not get_setting("ref_reward"):
        set_setting("ref_reward", "1")

    # اسم المنتج في Withdraw + سعره بالنقاط (عام)
    if not get_setting("item_name"):
        set_setting("item_name", "Premium Reward")
    if not get_setting("item_price"):
        set_setting("item_price", "4")

# =========================
# USERS
# =========================
def ensure_user(user_id: int, ref_by: Optional[int] = None):
    con = db()
    cur = con.cursor()
    row = cur.execute("SELECT user_id, ref_by FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users(user_id, points, ref_by, ref_paid, join_verified, banned, created_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, 0, ref_by, 0, 0, 0, now_ts())
        )
    else:
        # إذا user جديد وبدا ref_by نثبته مرة وحدة
        if ref_by and (row["ref_by"] is None or int(row["ref_by"] or 0) == 0) and ref_by != user_id:
            cur.execute("UPDATE users SET ref_by=? WHERE user_id=?", (ref_by, user_id))
    con.commit()
    con.close()

def get_user(user_id: int) -> sqlite3.Row:
    con = db()
    row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row

def is_banned(user_id: int) -> bool:
    u = get_user(user_id)
    return bool(u and int(u["banned"]) == 1)

def set_ban(user_id: int, banned: bool):
    con = db()
    con.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if banned else 0, user_id))
    con.commit()
    con.close()

def add_points(user_id: int, amount: int):
    con = db()
    con.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
    con.commit()
    con.close()

def set_points(user_id: int, points: int):
    con = db()
    con.execute("UPDATE users SET points=? WHERE user_id=?", (points, user_id))
    con.commit()
    con.close()

def users_count() -> int:
    con = db()
    row = con.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    con.close()
    return int(row["c"] or 0)

# =========================
# SETTINGS HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def ref_reward_points() -> int:
    try:
        return max(0, int(get_setting("ref_reward", "1")))
    except:
        return 1

def item_name() -> str:
    return get_setting("item_name", "Premium Reward").strip() or "Premium Reward"

def item_price() -> int:
    try:
        return max(0, int(get_setting("item_price", "4")))
    except:
        return 4

def support_text() -> str:
    return get_setting("support", "@Support").strip() or "@Support"

def help_center_text() -> str:
    return get_setting("help_center", "Help Center").strip()

def normalize_channel(x: str) -> str:
    x = (x or "").strip()
    if not x:
        return ""
    # allow t.me/xxx, https://t.me/xxx, @xxx, xxx
    x = x.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "")
    x = x.strip().strip("/")
    if not x:
        return ""
    if x.startswith("@"):
        return x
    return f"@{x}"

def get_channels() -> Tuple[str, str]:
    ch1 = normalize_channel(get_setting("ch1", ""))
    ch2 = normalize_channel(get_setting("ch2", ""))
    return ch1, ch2

# =========================
# FORCE JOIN CHECK
# =========================
async def is_member(context: ContextTypes.DEFAULT_TYPE, channel: str, user_id: int) -> bool:
    if not channel:
        return True
    try:
        m = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        # إذا البوت مش Admin بالقناة غالبًا بيصير خطأ
        log.warning(f"get_chat_member failed for {channel}: {e}")
        return False

def join_gate_kb(ch1: str, ch2: str) -> InlineKeyboardMarkup:
    rows = []
    if ch1:
        rows.append([InlineKeyboardButton("➡️ Join Channel 1", url=f"https://t.me/{ch1.lstrip('@')}")])
    if ch2:
        rows.append([InlineKeyboardButton("➡️ Join Channel 2", url=f"https://t.me/{ch2.lstrip('@')}")])
    rows.append([InlineKeyboardButton("✅ JOINED", callback_data="verify_join")])
    return InlineKeyboardMarkup(rows)

async def require_join_or_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    u = get_user(user_id)
    if u and int(u["join_verified"]) == 1:
        return True

    ch1, ch2 = get_channels()
    # إذا ما في قنوات، اعتبره Verified
    if not ch1 and not ch2:
        con = db()
        con.execute("UPDATE users SET join_verified=1 WHERE user_id=?", (user_id,))
        con.commit()
        con.close()
        return True

    ok1 = await is_member(context, ch1, user_id) if ch1 else True
    ok2 = await is_member(context, ch2, user_id) if ch2 else True
    if ok1 and ok2:
        con = db()
        con.execute("UPDATE users SET join_verified=1 WHERE user_id=?", (user_id,))
        con.commit()
        con.close()
        return True

    text = "❌ Join channel first!\n\nJoin both channels then press ✅ JOINED"
    kb = join_gate_kb(ch1, ch2)

    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    return False

# =========================
# STOCK / PURCHASE (ATOMIC)
# =========================
def stock_available_count() -> int:
    con = db()
    row = con.execute("SELECT COUNT(*) AS c FROM stock WHERE used=0").fetchone()
    con.close()
    return int(row["c"] or 0)

def add_stock_items(items: list[str]) -> int:
    items = [x.strip() for x in items if x.strip()]
    if not items:
        return 0
    con = db()
    con.executemany(
        "INSERT INTO stock(item, used, used_by, used_at, added_at) VALUES(?,0,NULL,NULL,?)",
        [(it, now_ts()) for it in items]
    )
    con.commit()
    con.close()
    return len(items)

def purchase_one(user_id: int, cost: int) -> Tuple[bool, str]:
    """
    Atomic:
    - check points
    - take stock
    - deduct points
    - mark used
    - insert purchase
    """
    con = db()
    cur = con.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")

        u = cur.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not u:
            con.rollback()
            return False, "User not found."

        pts = int(u["points"] or 0)
        if pts < cost:
            con.rollback()
            return False, f"Not enough points. You have {pts}, need {cost}."

        row = cur.execute("SELECT id, item FROM stock WHERE used=0 ORDER BY id ASC LIMIT 1").fetchone()
        if not row:
            con.rollback()
            return False, "Out of stock."

        sid = int(row["id"])
        item = row["item"]

        cur.execute("UPDATE stock SET used=1, used_by=?, used_at=? WHERE id=?", (user_id, now_ts(), sid))
        cur.execute("UPDATE users SET points = points - ? WHERE user_id=?", (cost, user_id))
        cur.execute(
            "INSERT INTO purchases(user_id, item, cost, delivered_text, created_at) VALUES(?,?,?,?,?)",
            (user_id, item_name(), cost, item, now_ts())
        )

        con.commit()
        return True, item
    except Exception as e:
        con.rollback()
        return False, f"Purchase error: {e}"
    finally:
        con.close()

# =========================
# UI
# =========================
def main_menu_kb(admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("🧑‍🤝‍🧑 Referral", callback_data="referral")],
        [InlineKeyboardButton("💵 Withdraw", callback_data="withdraw"),
         InlineKeyboardButton("🆘 Support", callback_data="support")],
        [InlineKeyboardButton("❓ Help Center", callback_data="help_center")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("🛠 Admin", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home")]])

def withdraw_kb() -> InlineKeyboardMarkup:
    price = item_price()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎁 {item_name()} [{price} Points]", callback_data="buy_item")],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")]
    ])

def admin_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📦 Stock", callback_data="admin_stock")],
        [InlineKeyboardButton("📣 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👤 Users/Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")],
    ]
    return InlineKeyboardMarkup(rows)

# =========================
# HANDLERS
# =========================
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id)

    if is_banned(user.id):
        if update.message:
            await update.message.reply_text("🚫 You are banned.")
        else:
            await update.callback_query.edit_message_text("🚫 You are banned.")
        return

    if not await require_join_or_block(update, context):
        return

    admin = is_admin(user.id)
    text = "✅ Welcome! Select from menu:"
    kb = main_menu_kb(admin)

    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # referral: /start 123456
    ref_by = None
    if context.args and context.args[0].isdigit():
        ref_by = int(context.args[0])
        if ref_by == user.id:
            ref_by = None

    ensure_user(user.id, ref_by=ref_by)
    await show_home(update, context)

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    await q.answer()

    ensure_user(user.id)

    if is_banned(user.id):
        await q.edit_message_text("🚫 You are banned.")
        return

    data = q.data

    if data == "home":
        await show_home(update, context)
        return

    if data == "verify_join":
        # verify and open menu + referral reward once
        passed = await require_join_or_block(update, context)
        if not passed:
            return

        # referral reward (only once after verified)
        u = get_user(user.id)
        if u and int(u["ref_paid"]) == 0 and u["ref_by"]:
            ref_by = int(u["ref_by"])
            pts = ref_reward_points()
            add_points(ref_by, pts)

            con = db()
            con.execute("UPDATE users SET ref_paid=1 WHERE user_id=?", (user.id,))
            con.commit()
            con.close()

            try:
                await context.bot.send_message(ref_by, f"🎉 New verified referral! +{pts} point(s).")
            except:
                pass

        await q.edit_message_text("✅ Verified! Select from menu:", reply_markup=main_menu_kb(is_admin(user.id)))
        return

    # Block all actions until joined
    if not await require_join_or_block(update, context):
        return

    if data == "balance":
        pts = get_user(user.id)["points"]
        await q.edit_message_text(f"💰 Your balance: *{pts}* points.", parse_mode="Markdown", reply_markup=back_kb())

    elif data == "referral":
        link = f"https://t.me/{context.bot.username}?start={user.id}"
        reward = ref_reward_points()
        await q.edit_message_text(
            "🧑‍🤝‍🧑 *Referral*\n\n"
            f"Your link:\n`{link}`\n\n"
            f"⭐ Reward per join+verify: *{reward}* point(s).",
            parse_mode="Markdown",
            reply_markup=back_kb(),
        )

    elif data == "withdraw":
        pts = get_user(user.id)["points"]
        price = item_price()
        stock = stock_available_count()
        await q.edit_message_text(
            "📩 Exchange your points for a reward.\n\n"
            f"💰 Balance: *{pts}* points\n"
            f"📦 Stock: *{stock}*\n"
            f"💵 Price: *{price}* points\n",
            parse_mode="Markdown",
            reply_markup=withdraw_kb(),
        )

    elif data == "buy_item":
        price = item_price()
        ok, result = purchase_one(user.id, price)
        if not ok:
            await q.edit_message_text(f"❌ {result}", reply_markup=back_kb())
            return

        # Send item in separate message (أفضل)
        await q.edit_message_text("✅ Purchased successfully! Check your DM/message below 👇", reply_markup=back_kb())
        await context.bot.send_message(
            chat_id=user.id,
            text=f"🎁 {item_name()}:\n\n`{result}`\n\n✅ Keep it private.",
            parse_mode="Markdown"
        )

    elif data == "support":
        sup = support_text()
        await q.edit_message_text(f"🆘 Support: {sup}", reply_markup=back_kb())

    elif data == "help_center":
        await q.edit_message_text(help_center_text(), reply_markup=back_kb())

    elif data == "admin":
        if not is_admin(user.id):
            await q.edit_message_text("Not allowed.", reply_markup=back_kb())
            return
        await q.edit_message_text(
            "🛠 Admin Panel\n\n"
            "Commands:\n"
            "/set_channels @ch1 @ch2\n"
            "/set_refreward 1\n"
            "/set_item Premium Reward | 4\n"
            "/add_stock (multiline)\n"
            "/stock_count\n"
            "/broadcast your message\n"
            "/ban 123\n"
            "/unban 123\n"
            "/add_points 123 10\n"
            "/set_points 123 10\n"
            "/stats\n",
            reply_markup=admin_kb()
        )

    elif data == "admin_stock":
        if not is_admin(user.id):
            return
        await q.edit_message_text(
            f"📦 Stock\n\nAvailable: {stock_available_count()}\n\n"
            "Add stock like this:\n"
            "/add_stock\n"
            "CODE1\n"
            "CODE2\n"
            "...\n",
            reply_markup=admin_kb()
        )

    elif data == "admin_broadcast":
        if not is_admin(user.id):
            return
        await q.edit_message_text(
            "📣 Broadcast\n\nUse:\n/broadcast Your message here",
            reply_markup=admin_kb()
        )

    elif data == "admin_stats":
        if not is_admin(user.id):
            return
        await q.edit_message_text(
            f"👤 Stats\n\nUsers: {users_count()}\nStock: {stock_available_count()}\n",
            reply_markup=admin_kb()
        )

    elif data == "admin_settings":
        if not is_admin(user.id):
            return
        ch1, ch2 = get_channels()
        await q.edit_message_text(
            "⚙️ Settings\n\n"
            f"Channels:\n1) {ch1}\n2) {ch2}\n\n"
            f"Referral reward: {ref_reward_points()}\n"
            f"Item: {item_name()} | Price: {item_price()}\n"
            f"Support: {support_text()}\n\n"
            "Set with commands:\n"
            "/set_channels @ch1 @ch2\n"
            "/set_refreward 1\n"
            "/set_item Name | Price\n"
            "/set_support @Support\n"
            "/set_helpcenter text...\n",
            reply_markup=admin_kb()
        )

# =========================
# ADMIN COMMANDS
# =========================
async def admin_guard(update: Update) -> bool:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only.")
        return False
    return True

async def set_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage:\n/set_channels @channel1 @channel2")
        return
    ch1 = normalize_channel(context.args[0])
    ch2 = normalize_channel(context.args[1])
    set_setting("ch1", ch1)
    set_setting("ch2", ch2)
    await update.message.reply_text(f"✅ Channels saved:\n1) {ch1}\n2) {ch2}\n\n⚠️ Add bot as ADMIN in both channels!")

async def set_support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    if not context.args:
        await update.message.reply_text("Usage:\n/set_support @Support")
        return
    set_setting("support", context.args[0].strip())
    await update.message.reply_text(f"✅ Support saved: {support_text()}")

async def set_helpcenter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    text = update.message.text.split(" ", 1)
    if len(text) < 2 or not text[1].strip():
        await update.message.reply_text("Usage:\n/set_helpcenter your help text here...")
        return
    set_setting("help_center", text[1].strip())
    await update.message.reply_text("✅ Help Center saved.")

async def set_refreward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage:\n/set_refreward 1")
        return
    set_setting("ref_reward", str(int(context.args[0])))
    await update.message.reply_text(f"✅ Referral reward = {ref_reward_points()}")

async def set_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /set_item Premium Reward | 4
    """
    if not await admin_guard(update):
        return
    raw = update.message.text.replace("/set_item", "", 1).strip()
    if "|" not in raw:
        await update.message.reply_text("Usage:\n/set_item Name | Price\nExample:\n/set_item Premium Reward | 4")
        return
    name, price = [x.strip() for x in raw.split("|", 1)]
    if not name:
        await update.message.reply_text("❌ Name is empty.")
        return
    if not price.isdigit():
        await update.message.reply_text("❌ Price must be a number.")
        return
    set_setting("item_name", name)
    set_setting("item_price", price)
    await update.message.reply_text(f"✅ Item saved: {item_name()} | Price: {item_price()}")

async def add_stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add_stock
    CODE1
    CODE2
    """
    if not await admin_guard(update):
        return
    text = update.message.text.split("\n", 1)
    if len(text) < 2 or not text[1].strip():
        await update.message.reply_text(
            "Usage:\n/add_stock\nCODE1\nCODE2\n...\n\nEach line = one code/serial you own."
        )
        return
    lines = [x.strip() for x in text[1].splitlines() if x.strip()]
    n = add_stock_items(lines)
    await update.message.reply_text(f"✅ Added {n} stock item(s).\nAvailable: {stock_available_count()}")

async def stock_count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    await update.message.reply_text(f"📦 Available stock: {stock_available_count()}")

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage:\n/ban USER_ID")
        return
    uid = int(context.args[0])
    set_ban(uid, True)
    await update.message.reply_text(f"✅ Banned {uid}")

async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage:\n/unban USER_ID")
        return
    uid = int(context.args[0])
    set_ban(uid, False)
    await update.message.reply_text(f"✅ Unbanned {uid}")

async def add_points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    if len(context.args) != 2 or (not context.args[0].isdigit()) or (not re.fullmatch(r"-?\d+", context.args[1])):
        await update.message.reply_text("Usage:\n/add_points USER_ID AMOUNT")
        return
    uid = int(context.args[0])
    amt = int(context.args[1])
    add_points(uid, amt)
    await update.message.reply_text(f"✅ Added {amt} points to {uid}")

async def set_points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    if len(context.args) != 2 or (not context.args[0].isdigit()) or (not context.args[1].isdigit()):
        await update.message.reply_text("Usage:\n/set_points USER_ID POINTS")
        return
    uid = int(context.args[0])
    pts = int(context.args[1])
    set_points(uid, pts)
    await update.message.reply_text(f"✅ Set points of {uid} to {pts}")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    con = db()
    p = con.execute("SELECT COUNT(*) AS c FROM purchases").fetchone()
    con.close()
    purchases = int(p["c"] or 0)
    await update.message.reply_text(
        f"📊 Stats\nUsers: {users_count()}\nStock: {stock_available_count()}\nPurchases: {purchases}"
    )

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update):
        return
    msg = update.message.text.replace("/broadcast", "", 1).strip()
    if not msg:
        await update.message.reply_text("Usage:\n/broadcast your message here")
        return

    con = db()
    rows = con.execute("SELECT user_id FROM users").fetchall()
    con.close()

    ok = 0
    fail = 0
    for r in rows:
        uid = int(r["user_id"])
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
            ok += 1
        except:
            fail += 1
        await asyncio.sleep(0.06)  # لتجنب Flood

    await update.message.reply_text(f"✅ Broadcast done.\nSent: {ok}\nFailed: {fail}")

# =========================
# MAIN
# =========================
def main():
    init_db()
    ensure_defaults()

    app = Application.builder().token(BOT_TOKEN).build()

    # user
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_button))

    # admin
    app.add_handler(CommandHandler("set_channels", set_channels_cmd))
    app.add_handler(CommandHandler("set_support", set_support_cmd))
    app.add_handler(CommandHandler("set_helpcenter", set_helpcenter_cmd))
    app.add_handler(CommandHandler("set_refreward", set_refreward_cmd))
    app.add_handler(CommandHandler("set_item", set_item_cmd))
    app.add_handler(CommandHandler("add_stock", add_stock_cmd))
    app.add_handler(CommandHandler("stock_count", stock_count_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("add_points", add_points_cmd))
    app.add_handler(CommandHandler("set_points", set_points_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
