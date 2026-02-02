import os
import sqlite3
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
)

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
APP_URL = os.getenv("APP_URL", "").strip().rstrip("/")
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

PROOFS_URL = os.getenv("PROOFS_URL", "").strip()
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()

# Mandatory join channels (public)
MANDATORY_CHANNELS = ["animatrix2026", "animatrix27"]

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing in Render Env Vars")
if not APP_URL.startswith("https://"):
    raise ValueError("APP_URL missing/invalid. Must be like https://xxxx.onrender.com")
if not ADMIN_ID.isdigit():
    raise ValueError("ADMIN_ID missing/invalid (numeric Telegram ID required)")

ADMIN_ID = int(ADMIN_ID)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bot")

DB_PATH = "bot.db"

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
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        points INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        referrer_id INTEGER DEFAULT NULL,   -- positive = not rewarded yet, negative = rewarded
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        k TEXT PRIMARY KEY,
        v TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rewards(
        reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        cost_points INTEGER NOT NULL,
        discount_percent INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock_codes(
        code_id INTEGER PRIMARY KEY AUTOINCREMENT,
        reward_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        is_used INTEGER DEFAULT 0,
        used_by INTEGER,
        used_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS withdraw_requests(
        w_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount_points INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )
    """)

    # defaults
    cur.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('require_join','1')")
    cur.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('ref_points','1')")
    cur.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('min_withdraw','10')")

    conn.commit()
    conn.close()

def get_setting(k: str, default=""):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT v FROM settings WHERE k=?", (k,))
    row = cur.fetchone()
    conn.close()
    return row["v"] if row else default

def set_setting(k: str, v: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO settings(k,v) VALUES(?,?)
        ON CONFLICT(k) DO UPDATE SET v=excluded.v
    """, (k, v))
    conn.commit()
    conn.close()

def ensure_user(tg_user):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (tg_user.id,))
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO users(user_id, username, first_name, created_at)
            VALUES(?,?,?,?)
        """, (tg_user.id, tg_user.username or "", tg_user.first_name or "", datetime.utcnow().isoformat()))
    else:
        cur.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                    (tg_user.username or "", tg_user.first_name or "", tg_user.id))
    conn.commit()
    conn.close()

def get_user(uid: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    return row

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def is_banned(uid: int) -> bool:
    u = get_user(uid)
    return bool(u and u["is_banned"] == 1)

def set_ban(uid: int, banned: bool):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if banned else 0, uid))
    conn.commit()
    conn.close()

def add_points(uid: int, pts: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + ? WHERE user_id=?", (pts, uid))
    conn.commit()
    conn.close()

def set_points(uid: int, pts: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points=? WHERE user_id=?", (pts, uid))
    conn.commit()
    conn.close()

def mark_verified(uid: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_verified=1 WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()

def set_referrer_if_empty(new_uid: int, ref_uid: int):
    if new_uid == ref_uid:
        return
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT referrer_id FROM users WHERE user_id=?", (new_uid,))
    row = cur.fetchone()
    if row and row["referrer_id"] is None:
        cur.execute("UPDATE users SET referrer_id=? WHERE user_id=?", (ref_uid, new_uid))
        conn.commit()
    conn.close()

def reward_price(cost: int, discount: int) -> int:
    if discount <= 0:
        return cost
    return max(0, int(round(cost * (100 - discount) / 100)))

# =========================
# FORCE JOIN
# =========================
async def is_member(app: Application, uid: int, channel_username: str) -> bool:
    try:
        member = await app.bot.get_chat_member(chat_id=f"@{channel_username}", user_id=uid)
        return member.status in ("creator", "administrator", "member")
    except Exception as e:
        # غالبًا البوت مش admin بالقناة أو القناة خاصة
        log.warning(f"get_chat_member failed @{channel_username}: {e}")
        return False

async def joined_all(app: Application, uid: int) -> bool:
    if get_setting("require_join", "1") != "1":
        return True
    for ch in MANDATORY_CHANNELS:
        if not await is_member(app, uid, ch):
            return False
    return True

def join_markup() -> InlineKeyboardMarkup:
    rows = []
    for ch in MANDATORY_CHANNELS:
        rows.append([InlineKeyboardButton(f"JOIN @{ch}", url=f"https://t.me/{ch}")])
    rows.append([InlineKeyboardButton("✅ I Joined (Verify)", callback_data="verify_join")])
    return InlineKeyboardMarkup(rows)

# =========================
# UI
# =========================
def main_menu(admin: bool) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("💰 BALANCE", callback_data="balance"),
         InlineKeyboardButton("👥 REFER", callback_data="refer")],
        [InlineKeyboardButton("🎁 REWARDS", callback_data="rewards"),
         InlineKeyboardButton("📦 STOCK", callback_data="stock")],
        [InlineKeyboardButton("🏧 WITHDRAW", callback_data="withdraw"),
         InlineKeyboardButton("🆘 SUPPORT", callback_data="support")],
        [InlineKeyboardButton("🧾 PROOFS", callback_data="proofs")],
    ]
    if admin:
        kb.append([InlineKeyboardButton("🔧 ADMIN PANEL", callback_data="admin")])
    return InlineKeyboardMarkup(kb)

def back(where="home"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data=where)]])

def admin_panel():
    kb = [
        [InlineKeyboardButton("➕ Add Reward", callback_data="admin_help_add_reward")],
        [InlineKeyboardButton("➕ Add Stock Codes", callback_data="admin_help_add_codes")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("➕ Add Points", callback_data="admin_help_add_points")],
        [InlineKeyboardButton("⛔ Ban", callback_data="admin_help_ban"),
         InlineKeyboardButton("✅ Unban", callback_data="admin_help_unban")],
        [InlineKeyboardButton("⬅️ BACK", callback_data="home")],
    ]
    return InlineKeyboardMarkup(kb)

# =========================
# REFERRAL CREDIT (after verified)
# =========================
async def credit_referral_if_needed(context: ContextTypes.DEFAULT_TYPE, new_uid: int):
    u = get_user(new_uid)
    if not u:
        return

    ref = u["referrer_id"]
    if not ref:
        return

    # if negative => already rewarded
    if ref < 0:
        return

    ref_points = int(get_setting("ref_points", "1"))

    add_points(ref, ref_points)

    # mark rewarded by negating referrer_id
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET referrer_id=? WHERE user_id=?", (-ref, new_uid))
    conn.commit()
    conn.close()

    # notify referrer (optional)
    try:
        await context.application.bot.send_message(ref, f"🎉 New verified referral! +{ref_points} point(s).")
    except:
        pass

# =========================
# SHOP / STOCK
# =========================
def list_rewards():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards WHERE is_active=1 ORDER BY reward_id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def reward_stock_count(rid: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM stock_codes WHERE reward_id=? AND is_used=0", (rid,))
    c = cur.fetchone()["c"]
    conn.close()
    return int(c)

def get_reward(rid: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards WHERE reward_id=? AND is_active=1", (rid,))
    row = cur.fetchone()
    conn.close()
    return row

def take_one_code(rid: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM stock_codes WHERE reward_id=? AND is_used=0 LIMIT 1", (rid,))
    row = cur.fetchone()
    conn.close()
    return row

# =========================
# ADMIN add codes mode
# =========================
ADMIN_ADD_CODES = {}  # admin_id -> reward_id

# =========================
# HANDLERS
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    ensure_user(update.effective_user)

    uid = update.effective_user.id
    if is_banned(uid):
        return await update.message.reply_text("⛔ You are banned.")

    # parse referral: /start <ref_id>
    if context.args:
        try:
            ref_id = int(context.args[0])
            set_referrer_if_empty(uid, ref_id)
        except:
            pass

    # IMPORTANT: first show mandatory join screen if not joined
    ok = await joined_all(context.application, uid)
    if not ok:
        return await update.message.reply_text(
            "⛔ الاشتراك إجباري قبل استخدام البوت:\n\n"
            "اشترك بالقنوات ثم اضغط ✅ Verify:",
            reply_markup=join_markup()
        )

    # verified logic (first time only)
    u = get_user(uid)
    if u and u["is_verified"] == 0:
        mark_verified(uid)
        await credit_referral_if_needed(context, uid)

    return await update.message.reply_text("✅ Welcome! Select from menu:", reply_markup=main_menu(is_admin(uid)))

async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(q.from_user)

    if is_banned(uid):
        return await q.edit_message_text("⛔ You are banned.")

    # verify join
    if q.data == "verify_join":
        ok = await joined_all(context.application, uid)
        if not ok:
            return await q.edit_message_text(
                "❌ لسه مش مشترك بكل القنوات.\nاشترك وبعدين اضغط Verify:",
                reply_markup=join_markup()
            )

        u = get_user(uid)
        if u and u["is_verified"] == 0:
            mark_verified(uid)
            await credit_referral_if_needed(context, uid)

        return await q.edit_message_text("✅ Verified! Select from menu:", reply_markup=main_menu(is_admin(uid)))

    # home
    if q.data == "home":
        return await q.edit_message_text("✅ Menu:", reply_markup=main_menu(is_admin(uid)))

    # block all if not joined (except verify)
    if not await joined_all(context.application, uid):
        return await q.edit_message_text("⛔ Join required first:", reply_markup=join_markup())

    # BALANCE
    if q.data == "balance":
        u = get_user(uid)
        return await q.edit_message_text(f"💰 Balance: {u['points']} point(s)", reply_markup=back("home"))

    # REFER
    if q.data == "refer":
        ref_points = int(get_setting("ref_points", "1"))
        link = f"https://t.me/{context.application.bot.username}?start={uid}"
        return await q.edit_message_text(
            "👥 Referral Link:\n\n"
            f"{link}\n\n"
            f"⭐ You get +{ref_points} point(s) when the new user joins & verifies.",
            reply_markup=back("home")
        )

    # SUPPORT
    if q.data == "support":
        if SUPPORT_URL:
            return await q.edit_message_text(f"🆘 Support:\n{SUPPORT_URL}", reply_markup=back("home"))
        return await q.edit_message_text("🆘 Support not set.", reply_markup=back("home"))

    # PROOFS
    if q.data == "proofs":
        if PROOFS_URL:
            return await q.edit_message_text(f"🧾 Proofs:\n{PROOFS_URL}", reply_markup=back("home"))
        return await q.edit_message_text("🧾 Proofs not set.", reply_markup=back("home"))

    # STOCK
    if q.data == "stock":
        rewards = list_rewards()
        if not rewards:
            return await q.edit_message_text("📦 No rewards/stock yet.", reply_markup=back("home"))

        text = "📦 Stock:\n\n"
        for r in rewards:
            c = reward_stock_count(r["reward_id"])
            price = reward_price(r["cost_points"], r["discount_percent"])
            text += f"- {r['name']} | price: {price}p | available: {c}\n"
        return await q.edit_message_text(text, reply_markup=back("home"))

    # REWARDS list
    if q.data == "rewards":
        rewards = list_rewards()
        if not rewards:
            return await q.edit_message_text("🎁 No rewards yet.", reply_markup=back("home"))

        kb = []
        for r in rewards:
            price = reward_price(r["cost_points"], r["discount_percent"])
            kb.append([InlineKeyboardButton(f"{r['name']} ({price}p)", callback_data=f"rw:{r['reward_id']}")])
        kb.append([InlineKeyboardButton("⬅️ BACK", callback_data="home")])
        return await q.edit_message_text("🎁 Choose a reward:", reply_markup=InlineKeyboardMarkup(kb))

    # reward details
    if q.data.startswith("rw:"):
        rid = int(q.data.split(":")[1])
        r = get_reward(rid)
        if not r:
            return await q.edit_message_text("❌ Reward not found.", reply_markup=back("rewards"))

        stock = reward_stock_count(rid)
        price = reward_price(r["cost_points"], r["discount_percent"])
        kb = [
            [InlineKeyboardButton("✅ Buy / Redeem", callback_data=f"buy:{rid}")],
            [InlineKeyboardButton("⬅️ BACK", callback_data="rewards")]
        ]
        return await q.edit_message_text(
            f"🎁 {r['name']}\nPrice: {price} point(s)\nAvailable: {stock}",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    # BUY
    if q.data.startswith("buy:"):
        rid = int(q.data.split(":")[1])
        r = get_reward(rid)
        if not r:
            return await q.edit_message_text("❌ Reward not found.", reply_markup=back("rewards"))

        u = get_user(uid)
        price = reward_price(r["cost_points"], r["discount_percent"])

        if u["points"] < price:
            return await q.edit_message_text(
                f"❌ Not enough points.\nPrice: {price}\nYour balance: {u['points']}",
                reply_markup=back("rewards")
            )

        code_row = take_one_code(rid)
        if not code_row:
            return await q.edit_message_text("❌ Out of stock.", reply_markup=back("rewards"))

        # atomic update
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE users SET points = points - ? WHERE user_id=?", (price, uid))
        cur.execute("""
            UPDATE stock_codes SET is_used=1, used_by=?, used_at=?
            WHERE code_id=? AND is_used=0
        """, (uid, datetime.utcnow().isoformat(), code_row["code_id"]))
        conn.commit()
        conn.close()

        return await q.edit_message_text(
            f"✅ Done!\n\nReward: {r['name']}\nCode:\n{code_row['code']}",
            reply_markup=back("home")
        )

    # WITHDRAW (simple request to admin)
    if q.data == "withdraw":
        u = get_user(uid)
        min_w = int(get_setting("min_withdraw", "10"))
        if u["points"] < min_w:
            return await q.edit_message_text(
                f"🏧 Withdraw\nMinimum: {min_w} point(s)\nYour balance: {u['points']}",
                reply_markup=back("home")
            )

        conn = db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO withdraw_requests(user_id, amount_points, created_at)
            VALUES(?,?,?)
        """, (uid, u["points"], datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()

        try:
            await context.application.bot.send_message(
                ADMIN_ID,
                f"🏧 Withdraw Request\nUser: {uid}\nPoints: {u['points']}\n@{q.from_user.username}"
            )
        except:
            pass

        return await q.edit_message_text("✅ Withdraw request sent to admin.", reply_markup=back("home"))

    # ADMIN PANEL
    if q.data == "admin":
        if not is_admin(uid):
            return await q.edit_message_text("❌ Not allowed.", reply_markup=back("home"))
        return await q.edit_message_text("🔧 Admin Panel:", reply_markup=admin_panel())

    if q.data == "admin_settings":
        if not is_admin(uid):
            return
        txt = (
            "⚙️ Settings\n\n"
            f"require_join = {get_setting('require_join','1')}\n"
            f"ref_points = {get_setting('ref_points','1')}\n"
            f"min_withdraw = {get_setting('min_withdraw','10')}\n\n"
            "Change with:\n"
            "/set require_join 1\n"
            "/set ref_points 2\n"
            "/set min_withdraw 50\n"
        )
        return await q.edit_message_text(txt, reply_markup=back("admin"))

    if q.data == "admin_help_add_reward":
        return await q.edit_message_text(
            "➕ Add Reward:\n\n"
            "/add_reward NAME | COST | DISCOUNT%\n\n"
            "Example:\n/add_reward Premium 1 Month | 10 | 0",
            reply_markup=back("admin")
        )

    if q.data == "admin_help_add_codes":
        return await q.edit_message_text(
            "➕ Add Codes:\n\n"
            "/add_codes REWARD_ID\n"
            "Then send codes (one per line).\n"
            "Finish with: /done",
            reply_markup=back("admin")
        )

    if q.data == "admin_help_add_points":
        return await q.edit_message_text(
            "➕ Add/Set Points:\n\n"
            "/addpoints USER_ID AMOUNT\n"
            "/setpoints USER_ID AMOUNT",
            reply_markup=back("admin")
        )

    if q.data == "admin_help_ban":
        return await q.edit_message_text("⛔ Ban:\n/ban USER_ID", reply_markup=back("admin"))

    if q.data == "admin_help_unban":
        return await q.edit_message_text("✅ Unban:\n/unban USER_ID", reply_markup=back("admin"))

    return await q.edit_message_text("✅ Menu:", reply_markup=main_menu(is_admin(uid)))

# =========================
# ADMIN COMMANDS
# =========================
async def set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /set KEY VALUE")
    k = context.args[0].strip()
    v = " ".join(context.args[1:]).strip()
    set_setting(k, v)
    await update.message.reply_text(f"✅ Set {k} = {v}")

async def add_reward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    raw = update.message.text.replace("/add_reward", "", 1).strip()
    if "|" not in raw:
        return await update.message.reply_text("Usage: /add_reward NAME | COST | DISCOUNT%")
    parts = [p.strip() for p in raw.split("|")]
    name = parts[0]
    cost = int(parts[1]) if len(parts) > 1 else 0
    disc = int(parts[2]) if len(parts) > 2 else 0
    if not name or cost <= 0:
        return await update.message.reply_text("❌ Invalid name/cost.")

    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO rewards(name,cost_points,discount_percent,is_active) VALUES(?,?,?,1)", (name, cost, disc))
    rid = cur.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Reward added. ID={rid}")

async def add_codes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await update.message.reply_text("Usage: /add_codes REWARD_ID")
    rid = int(context.args[0])

    r = get_reward(rid)
    if not r:
        return await update.message.reply_text("❌ Reward not found.")

    ADMIN_ADD_CODES[ADMIN_ID] = rid
    await update.message.reply_text(
        f"✅ Send codes now for reward_id={rid}\n"
        "Send codes (one per line). Finish with /done"
    )

async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    ADMIN_ADD_CODES.pop(ADMIN_ID, None)
    await update.message.reply_text("✅ Done adding codes.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if ADMIN_ID not in ADMIN_ADD_CODES:
        return

    rid = ADMIN_ADD_CODES[ADMIN_ID]
    lines = (update.message.text or "").splitlines()
    codes = [x.strip() for x in lines if x.strip()]
    if not codes:
        return

    conn = db()
    cur = conn.cursor()
    for c in codes:
        cur.execute("INSERT INTO stock_codes(reward_id, code, is_used) VALUES(?,?,0)", (rid, c))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Added {len(codes)} code(s) to reward_id={rid}")

async def addpoints_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(context.args) != 2:
        return await update.message.reply_text("Usage: /addpoints USER_ID AMOUNT")
    uid = int(context.args[0]); amt = int(context.args[1])
    add_points(uid, amt)
    await update.message.reply_text("✅ Points added.")

async def setpoints_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(context.args) != 2:
        return await update.message.reply_text("Usage: /setpoints USER_ID AMOUNT")
    uid = int(context.args[0]); amt = int(context.args[1])
    set_points(uid, amt)
    await update.message.reply_text("✅ Points set.")

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await update.message.reply_text("Usage: /ban USER_ID")
    uid = int(context.args[0])
    set_ban(uid, True)
    await update.message.reply_text("⛔ Banned.")

async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await update.message.reply_text("Usage: /unban USER_ID")
    uid = int(context.args[0])
    set_ban(uid, False)
    await update.message.reply_text("✅ Unbanned.")

# =========================
# RUN (Webhook)
# =========================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_cb))

    # admin commands
    app.add_handler(CommandHandler("set", set_cmd))
    app.add_handler(CommandHandler("add_reward", add_reward_cmd))
    app.add_handler(CommandHandler("add_codes", add_codes_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("addpoints", addpoints_cmd))
    app.add_handler(CommandHandler("setpoints", setpoints_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))

    # admin text for bulk codes
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    port = int(os.environ.get("PORT", "10000"))

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()
