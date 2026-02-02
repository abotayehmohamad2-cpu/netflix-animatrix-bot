import os
import sqlite3
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ======================
# ENV CONFIG
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Render يعطي هذا تلقائياً — الأفضل تستخدمه بدل ما تتعب مع APP_URL
BASE_URL = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_URL") or "").strip().rstrip("/")

FORCE_CHATS = os.getenv("FORCE_CHATS", "").strip()  # مثال: @animatrix2026,@animatrix27
SUPPORT_USER = os.getenv("SUPPORT_USER", "@Support").strip()
PROOFS_URL = os.getenv("PROOFS_URL", "").strip()

ADMIN_ID = os.getenv("ADMIN_ID", "").strip()  # رقم تيليجرام تبعك
REF_POINTS = int(os.getenv("REF_POINTS", "1").strip())

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing in Render Env Vars")
if not BASE_URL.startswith("https://"):
    raise ValueError("BASE URL missing. Set APP_URL (https://xxxx.onrender.com) OR use RENDER_EXTERNAL_URL")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("ref-bot")

DB_PATH = "bot.db"


# ======================
# DB
# ======================
def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        points INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    # لمنع إعطاء نقاط الدعوة أكثر من مرة
    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals(
        new_user_id INTEGER PRIMARY KEY,
        referrer_id INTEGER,
        created_at TEXT
    )
    """)

    # Rewards / Stock
    cur.execute("""
    CREATE TABLE IF NOT EXISTS rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        cost INTEGER,
        stock INTEGER DEFAULT 0
    )
    """)

    # طلبات السحب (Redeem)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS redeem_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        reward_id INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )
    """)

    con.commit()
    con.close()

def ensure_user(user_id: int, username: str):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users(user_id, username, points, created_at) VALUES (?,?,0,?)",
            (user_id, username or "", datetime.utcnow().isoformat())
        )
    else:
        cur.execute("UPDATE users SET username=? WHERE user_id=?", (username or "", user_id))
    con.commit()
    con.close()

def get_points(user_id: int) -> int:
    con = db()
    cur = con.cursor()
    cur.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return int(row[0]) if row else 0

def add_points(user_id: int, amount: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
    con.commit()
    con.close()

def set_points(user_id: int, amount: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET points = ? WHERE user_id=?", (amount, user_id))
    con.commit()
    con.close()

def referral_exists(new_user_id: int) -> bool:
    con = db()
    cur = con.cursor()
    cur.execute("SELECT 1 FROM referrals WHERE new_user_id=?", (new_user_id,))
    ok = cur.fetchone() is not None
    con.close()
    return ok

def save_referral(new_user_id: int, referrer_id: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO referrals(new_user_id, referrer_id, created_at) VALUES (?,?,?)",
        (new_user_id, referrer_id, datetime.utcnow().isoformat())
    )
    con.commit()
    con.close()

def pop_referral(new_user_id: int):
    """ترجع referrer_id ثم تحذف السجل (عشان ما تتكرر)"""
    con = db()
    cur = con.cursor()
    cur.execute("SELECT referrer_id FROM referrals WHERE new_user_id=?", (new_user_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        return None
    ref_id = int(row[0])
    cur.execute("DELETE FROM referrals WHERE new_user_id=?", (new_user_id,))
    con.commit()
    con.close()
    return ref_id

def list_rewards():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, title, cost, stock FROM rewards ORDER BY id DESC")
    rows = cur.fetchall()
    con.close()
    return rows

def get_reward(rid: int):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, title, cost, stock FROM rewards WHERE id=?", (rid,))
    row = cur.fetchone()
    con.close()
    return row

def add_reward(title: str, cost: int, stock: int):
    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO rewards(title, cost, stock) VALUES (?,?,?)", (title, cost, stock))
    con.commit()
    con.close()

def set_stock(rid: int, stock: int):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE rewards SET stock=? WHERE id=?", (stock, rid))
    con.commit()
    con.close()

def create_redeem_request(user_id: int, reward_id: int):
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO redeem_requests(user_id, reward_id, status, created_at) VALUES (?,?, 'pending', ?)",
        (user_id, reward_id, datetime.utcnow().isoformat())
    )
    con.commit()
    con.close()

def is_admin(user_id: int) -> bool:
    return ADMIN_ID.isdigit() and int(ADMIN_ID) == int(user_id)


# ======================
# FORCE JOIN
# ======================
def parse_force_chats():
    chats = []
    for x in FORCE_CHATS.split(","):
        x = x.strip()
        if x:
            chats.append(x)
    return chats

async def joined_all(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chats = parse_force_chats()
    if not chats:
        return True
    for ch in chats:
        try:
            member = await context.bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked"):
                return False
        except:
            return False
    return True

def join_markup():
    chats = parse_force_chats()
    btns = []
    for ch in chats:
        url = f"https://t.me/{ch.replace('@','')}"
        btns.append([InlineKeyboardButton("JOIN", url=url)])
    btns.append([InlineKeyboardButton("💡 [ JOINED ] 💡", callback_data="joined")])
    return InlineKeyboardMarkup(btns)


# ======================
# MENUS (English like screenshot)
# ======================
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

def back_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="back")]])


# ======================
# COMMANDS
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()

    user = update.effective_user
    ensure_user(user.id, user.username or "")

    # Save referral if /start <ref_id>
    ref_id = None
    if context.args:
        try:
            ref_id = int(context.args[0])
            if ref_id == user.id:
                ref_id = None
        except:
            ref_id = None

    if ref_id and not referral_exists(user.id):
        save_referral(user.id, ref_id)

    # Force join gate
    if not await joined_all(user.id, context):
        await update.message.reply_text(
            "👋 Welcome!\n\n⏳ Join all channels then click [JOINED] to start the bot.",
            reply_markup=join_markup()
        )
        return

    await update.message.reply_text("✅ Welcome! Select from menu:", reply_markup=main_menu())


# ======================
# CALLBACKS
# ======================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    ensure_user(user.id, user.username or "")

    # Joined check button
    if q.data == "joined":
        if not await joined_all(user.id, context):
            await q.edit_message_text("❌ You still didn't join all channels.", reply_markup=join_markup())
            return

        # Give referral points once AFTER join success
        referrer_id = pop_referral(user.id)
        if referrer_id:
            ensure_user(referrer_id, "")
            add_points(referrer_id, REF_POINTS)

        await q.edit_message_text("✅ Verified! Select from menu:", reply_markup=main_menu())
        return

    # Block features until joined
    if not await joined_all(user.id, context):
        await q.edit_message_text("⛔ You must join all channels first.", reply_markup=join_markup())
        return

    if q.data == "back":
        await q.edit_message_text("Main menu:", reply_markup=main_menu())
        return

    if q.data == "balance":
        pts = get_points(user.id)
        await q.edit_message_text(
            f"💰 *BALANCE*\n\nPoints: *{pts}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_menu()
        )
        return

    if q.data == "refer":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={user.id}"
        await q.edit_message_text(
            "👥 *REFER*\n\nInvite users with your link and earn points.\n\n"
            f"🔗 Your Link:\n`{link}`\n\n"
            f"⭐ Reward per join+verify: *{REF_POINTS}* point(s).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_menu()
        )
        return

    if q.data == "support":
        await q.edit_message_text(f"🆘 Support: {SUPPORT_USER}", reply_markup=back_menu())
        return

    if q.data == "proofs":
        if PROOFS_URL:
            await q.edit_message_text(f"🧾 Proofs:\n{PROOFS_URL}", reply_markup=back_menu())
        else:
            await q.edit_message_text("🧾 Proofs not set.", reply_markup=back_menu())
        return

    if q.data == "stock":
        items = list_rewards()
        if not items:
            await q.edit_message_text("📦 Stock is empty (no rewards yet).", reply_markup=back_menu())
            return
        text = "📦 *STOCK*\n\n"
        for rid, title, cost, stock in items:
            text += f"• {title} | cost: {cost} | stock: {stock}\n"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_menu())
        return

    if q.data == "rewards":
        items = list_rewards()
        if not items:
            await q.edit_message_text("🎁 No rewards yet.", reply_markup=back_menu())
            return
        kb = []
        for rid, title, cost, stock in items:
            kb.append([InlineKeyboardButton(f"{title} ({cost} pts)", callback_data=f"rw:{rid}")])
        kb.append([InlineKeyboardButton("⬅️ BACK", callback_data="back")])
        await q.edit_message_text("🎁 Select reward:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if q.data.startswith("rw:"):
        rid = int(q.data.split(":")[1])
        r = get_reward(rid)
        if not r:
            await q.edit_message_text("❌ Reward not found.", reply_markup=back_menu())
            return
        _, title, cost, stock = r
        kb = [
            [InlineKeyboardButton("✅ Redeem", callback_data=f"redeem:{rid}")],
            [InlineKeyboardButton("⬅️ BACK", callback_data="rewards")]
        ]
        await q.edit_message_text(
            f"🎁 *REWARD*\n\nName: *{title}*\nCost: *{cost} pts*\nStock: *{stock}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if q.data.startswith("redeem:"):
        rid = int(q.data.split(":")[1])
        r = get_reward(rid)
        if not r:
            await q.edit_message_text("❌ Reward not found.", reply_markup=back_menu())
            return
        _, title, cost, stock = r
        pts = get_points(user.id)

        if stock <= 0:
            await q.edit_message_text("❌ Out of stock.", reply_markup=back_menu())
            return
        if pts < cost:
            await q.edit_message_text("❌ Not enough points.", reply_markup=back_menu())
            return

        # Deduct points + decrease stock (atomic-ish)
        con = db()
        cur = con.cursor()
        cur.execute("UPDATE users SET points = points - ? WHERE user_id=?", (cost, user.id))
        cur.execute("UPDATE rewards SET stock = stock - 1 WHERE id=?", (rid,))
        con.commit()
        con.close()

        create_redeem_request(user.id, rid)

        await q.edit_message_text(
            f"✅ Withdraw request created!\n\nReward: {title}\nStatus: pending",
            reply_markup=back_menu()
        )
        return

    if q.data == "withdraw":
        await q.edit_message_text("🏧 Withdraw = Redeem rewards.\nGo to 🎁 REWARDS.", reply_markup=back_menu())
        return


# ======================
# ADMIN COMMANDS
# ======================
async def addreward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    # /addreward Title | cost | stock
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text("Usage:\n/addreward Title | cost | stock")
        return
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3:
        await update.message.reply_text("Usage:\n/addreward Title | cost | stock")
        return
    title = parts[0]
    cost = int(parts[1])
    stock = int(parts[2])
    add_reward(title, cost, stock)
    await update.message.reply_text("✅ Reward added.")

async def setstock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    # /setstock reward_id stock
    if len(context.args) != 2:
        await update.message.reply_text("Usage:\n/setstock reward_id stock")
        return
    rid = int(context.args[0])
    stock = int(context.args[1])
    set_stock(rid, stock)
    await update.message.reply_text("✅ Stock updated.")

async def addpoints_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    # /addpoints user_id amount
    if len(context.args) != 2:
        await update.message.reply_text("Usage:\n/addpoints user_id amount")
        return
    uid = int(context.args[0])
    amt = int(context.args[1])
    ensure_user(uid, "")
    add_points(uid, amt)
    await update.message.reply_text("✅ Points added.")

async def setpoints_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    # /setpoints user_id amount
    if len(context.args) != 2:
        await update.message.reply_text("Usage:\n/setpoints user_id amount")
        return
    uid = int(context.args[0])
    amt = int(context.args[1])
    ensure_user(uid, "")
    set_points(uid, amt)
    await update.message.reply_text("✅ Points set.")

# ======================
# RUN (Webhook for Render)
# ======================
def run():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))

    # admin
    app.add_handler(CommandHandler("addreward", addreward_cmd))
    app.add_handler(CommandHandler("setstock", setstock_cmd))
    app.add_handler(CommandHandler("addpoints", addpoints_cmd))
    app.add_handler(CommandHandler("setpoints", setpoints_cmd))

    port = int(os.environ.get("PORT", "10000"))
    url_path = BOT_TOKEN
    webhook_url = f"{BASE_URL}/{url_path}"

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=url_path,
        webhook_url=webhook_url,
    )

if __name__ == "__main__":
    run()
