import os
import re
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
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
APP_URL = os.getenv("APP_URL", "").strip()  # must be like: https://xxxx.onrender.com
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars.")
if not APP_URL or not APP_URL.startswith("https://") or ".onrender.com" not in APP_URL:
    raise ValueError("APP_URL missing/invalid. Example: https://xxxx.onrender.com")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID missing. Put your Telegram numeric ID.")

PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")

# =========================
# DB
# =========================
def db():
    conn = sqlite3.connect(DB_PATH)
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
        referred_by INTEGER,
        ref_rewarded INTEGER NOT NULL DEFAULT 0,
        verified INTEGER NOT NULL DEFAULT 0,
        banned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item TEXT NOT NULL,          -- e.g. "Netflix Account"
        price INTEGER NOT NULL,      -- points needed
        payload TEXT NOT NULL,       -- the actual account/code text
        added_at TEXT NOT NULL,
        claimed_by INTEGER,
        claimed_at TEXT
    )
    """)

    # defaults
    cur.execute("INSERT OR IGNORE INTO settings (k,v) VALUES ('reward_per_ref', '1')")
    cur.execute("INSERT OR IGNORE INTO settings (k,v) VALUES ('support_user', '@Support')")
    cur.execute("INSERT OR IGNORE INTO settings (k,v) VALUES ('required_channels', '@animatrix2026,@animatrix27')")

    conn.commit()
    conn.close()

def get_setting(key: str) -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT v FROM settings WHERE k=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["v"] if row else ""

def set_setting(key: str, value: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
    conn.commit()
    conn.close()

def ensure_user(user_id: int, referred_by: int | None = None):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (user_id, points, referred_by, created_at) VALUES (?,?,?,?)",
            (user_id, 0, referred_by, datetime.utcnow().isoformat()),
        )
    else:
        # if user exists but no referred_by stored yet, store it once
        if referred_by:
            cur.execute("SELECT referred_by FROM users WHERE user_id=?", (user_id,))
            r = cur.fetchone()
            if r and (r["referred_by"] is None):
                cur.execute("UPDATE users SET referred_by=? WHERE user_id=?", (referred_by, user_id))
    conn.commit()
    conn.close()

def is_banned(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT banned FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["banned"] == 1)

def set_verified(user_id: int, verified: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET verified=? WHERE user_id=?", (verified, user_id))
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

def take_points(user_id: int, amount: int) -> bool:
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

def referral_reward_if_needed(new_user_id: int) -> int | None:
    """
    If new user has referred_by and not rewarded yet and is verified => reward referrer
    Returns referrer_id if rewarded else None
    """
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT referred_by, ref_rewarded, verified FROM users WHERE user_id=?", (new_user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    if row["verified"] != 1 or row["ref_rewarded"] == 1 or not row["referred_by"]:
        conn.close()
        return None

    referrer_id = int(row["referred_by"])
    reward = int(get_setting("reward_per_ref") or "1")

    # mark rewarded + give points
    cur.execute("UPDATE users SET ref_rewarded=1 WHERE user_id=?", (new_user_id,))
    cur.execute("UPDATE users SET points = points + ? WHERE user_id=?", (reward, referrer_id))
    conn.commit()
    conn.close()
    return referrer_id

def add_stock(item: str, price: int, payload: str):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO stock (item, price, payload, added_at) VALUES (?,?,?,?)",
        (item, price, payload, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

def stock_count(item: str, price: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS c FROM stock WHERE item=? AND price=? AND claimed_by IS NULL",
        (item, price),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["c"]) if row else 0

def claim_one_stock(item: str, price: int, user_id: int) -> str | None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, payload FROM stock WHERE item=? AND price=? AND claimed_by IS NULL ORDER BY id ASC LIMIT 1",
        (item, price),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    stock_id = int(row["id"])
    payload = str(row["payload"])

    cur.execute(
        "UPDATE stock SET claimed_by=?, claimed_at=? WHERE id=?",
        (user_id, datetime.utcnow().isoformat(), stock_id),
    )
    conn.commit()
    conn.close()
    return payload

# =========================
# REQUIRED JOIN (channels)
# =========================
def parse_channels(raw: str) -> list[str]:
    # supports "@channel" or "https://t.me/channel"
    raw = (raw or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out = []
    for p in parts:
        if p.startswith("https://t.me/"):
            p = p.replace("https://t.me/", "").strip()
        if p.startswith("@"):
            out.append(p)
        else:
            out.append("@"+p)
    return out

async def is_member(bot, channel: str, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        # member.status can be: creator, administrator, member, restricted, left, kicked
        return m.status in ("creator", "administrator", "member")
    except Exception as e:
        # Most common: bot not admin in channel, or channel username wrong.
        logger.warning(f"get_chat_member failed for {channel}: {e}")
        return False

async def check_required_join(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    channels = parse_channels(get_setting("required_channels"))
    if not channels:
        return True

    for ch in channels:
        ok = await is_member(context.bot, ch, user_id)
        if not ok:
            return False
    return True

def join_keyboard() -> InlineKeyboardMarkup:
    channels = parse_channels(get_setting("required_channels"))
    buttons = []
    for i, ch in enumerate(channels, start=1):
        url = f"https://t.me/{ch.lstrip('@')}"
        buttons.append([InlineKeyboardButton(f"➡️ Join Channel {i}", url=url)])
    buttons.append([InlineKeyboardButton("✅ JOINED", callback_data="joined_check")])
    return InlineKeyboardMarkup(buttons)

# =========================
# MENUS
# =========================
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 BALANCE", callback_data="balance"),
         InlineKeyboardButton("👥 REFER", callback_data="refer")],
        [InlineKeyboardButton("💳 WITHDRAW", callback_data="withdraw"),
         InlineKeyboardButton("🆘 SUPPORT", callback_data="support")],
        [InlineKeyboardButton("📦 STOCK", callback_data="stock")],
    ])

def back_btn() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="back")]])

def withdraw_menu() -> InlineKeyboardMarkup:
    # You can expand later for multiple items/prices
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Netflix Account [4 Points]", callback_data="buy_netflix_4")],
        [InlineKeyboardButton("⬅️ BACK", callback_data="back")],
    ])

# =========================
# HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def parse_start_ref(text: str) -> int | None:
    # /start 123456 or /start=123 etc
    m = re.search(r"/start\s+(\d+)", text or "")
    if m:
        rid = int(m.group(1))
        return rid
    return None

# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    user_id = user.id

    if is_banned(user_id):
        return

    ref_id = None
    if update.message and update.message.text:
        ref_id = parse_start_ref(update.message.text)

    # avoid self-ref
    if ref_id == user_id:
        ref_id = None

    ensure_user(user_id, referred_by=ref_id)

    # force join check
    ok = await check_required_join(update, context, user_id)
    if not ok:
        set_verified(user_id, 0)
        await update.message.reply_text(
            "❌ Join channel first!\n\n"
            "Join all channels ثم اضغط ✅ JOINED.",
            reply_markup=join_keyboard(),
        )
        return

    # verified
    set_verified(user_id, 1)

    # reward referrer if needed
    referrer = referral_reward_if_needed(user_id)
    if referrer:
        reward = int(get_setting("reward_per_ref") or "1")
        try:
            await context.bot.send_message(
                chat_id=referrer,
                text=f"✅ New referral verified!\nYou earned +{reward} point(s).",
            )
        except Exception:
            pass

    await update.message.reply_text(
        "✅ Welcome! Select from menu:",
        reply_markup=main_menu(),
    )

async def on_joined_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    user_id = q.from_user.id
    await q.answer()

    ok = await check_required_join(update, context, user_id)
    if not ok:
        await q.edit_message_text(
            "❌ Still not joined!\nJoin all channels ثم اضغط ✅ JOINED.",
            reply_markup=join_keyboard(),
        )
        return

    set_verified(user_id, 1)

    # reward referrer if needed (now that verified)
    referrer = referral_reward_if_needed(user_id)
    if referrer:
        reward = int(get_setting("reward_per_ref") or "1")
        try:
            await context.bot.send_message(
                chat_id=referrer,
                text=f"✅ New referral verified!\nYou earned +{reward} point(s).",
            )
        except Exception:
            pass

    await q.edit_message_text(
        "✅ Verified! Select from menu:",
        reply_markup=main_menu(),
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    user_id = q.from_user.id
    await q.answer()

    if is_banned(user_id):
        return

    # if not verified, block menu actions
    if q.data not in ("joined_check",) and get_points(user_id) is not None:
        # enforce join for any action too
        ok = await check_required_join(update, context, user_id)
        if not ok:
            set_verified(user_id, 0)
            await q.edit_message_text(
                "❌ Join channel first!\nJoin all channels ثم اضغط ✅ JOINED.",
                reply_markup=join_keyboard(),
            )
            return
        else:
            set_verified(user_id, 1)

    if q.data == "balance":
        pts = get_points(user_id)
        await q.edit_message_text(
            f"💰 *Your Balance:* `{pts}` point(s).",
            reply_markup=back_btn(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif q.data == "refer":
        reward = int(get_setting("reward_per_ref") or "1")
        link = f"https://t.me/{context.bot.username}?start={user_id}"
        await q.edit_message_text(
            "👥 *REFER*\n\n"
            f"🔗 Your Link:\n`{link}`\n\n"
            f"⭐ Reward per join+verify: *{reward}* point(s).",
            reply_markup=back_btn(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif q.data == "support":
        sup = get_setting("support_user") or "@Support"
        await q.edit_message_text(
            f"🆘 Support: {sup}",
            reply_markup=back_btn(),
        )

    elif q.data == "stock":
        # example item/price
        c = stock_count("Netflix Account", 4)
        await q.edit_message_text(
            f"📦 Stock for Netflix Account [4 points]: *{c}* item(s) available.",
            reply_markup=back_btn(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif q.data == "withdraw":
        await q.edit_message_text(
            "💳 WITHDRAW\nChoose item:",
            reply_markup=withdraw_menu(),
        )

    elif q.data == "buy_netflix_4":
        price = 4
        pts = get_points(user_id)
        if pts < price:
            await q.edit_message_text(
                f"❌ Not enough points.\nYou have {pts}, need {price}.",
                reply_markup=withdraw_menu(),
            )
            return

        # claim stock first (to ensure availability), then take points
        payload = claim_one_stock("Netflix Account", price, user_id)
        if not payload:
            await q.edit_message_text(
                "❌ Out of stock.\nCome back later.",
                reply_markup=withdraw_menu(),
            )
            return

        # deduct points
        ok = take_points(user_id, price)
        if not ok:
            # rollback is complex; keep simple: give back stock by messaging admin
            await q.edit_message_text(
                "❌ Error deducting points. Contact support.",
                reply_markup=withdraw_menu(),
            )
            # notify admin
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"⚠️ Deduct failed after stock claim. user={user_id}",
                )
            except Exception:
                pass
            return

        await q.edit_message_text(
            "✅ Success!\nHere is your item:\n\n"
            f"```\n{payload}\n```",
            reply_markup=back_btn(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif q.data == "back":
        await q.edit_message_text(
            "✅ Main menu:",
            reply_markup=main_menu(),
        )

    elif q.data == "joined_check":
        await on_joined_check(update, context)

# =========================
# ADMIN COMMANDS
# =========================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    txt = (
        "🛠 ADMIN COMMANDS:\n"
        "/set_channels @channel1 @channel2\n"
        "/set_support @username\n"
        "/set_ref_reward 1\n"
        "/add_stock 4 | Netflix Account | email:pass\n"
        "/ban 123\n"
        "/unban 123\n"
        "/add_points 123 10\n"
        "/broadcast your message...\n"
    )
    await update.message.reply_text(txt)

async def set_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    parts = context.args
    if not parts or len(parts) < 1:
        await update.message.reply_text("Usage: /set_channels @animatrix2026 @animatrix27")
        return

    chans = []
    for p in parts:
        p = p.strip()
        if p.startswith("https://t.me/"):
            p = "@"+p.replace("https://t.me/", "").strip()
        if not p.startswith("@"):
            p = "@"+p
        chans.append(p)

    set_setting("required_channels", ",".join(chans))
    await update.message.reply_text(f"✅ Required channels set:\n" + "\n".join(chans))

async def set_support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    if not context.args:
        await update.message.reply_text("Usage: /set_support @SupportUser")
        return
    sup = context.args[0].strip()
    if not sup.startswith("@"):
        sup = "@"+sup
    set_setting("support_user", sup)
    await update.message.reply_text(f"✅ Support set to {sup}")

async def set_ref_reward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    if not context.args:
        await update.message.reply_text("Usage: /set_ref_reward 1")
        return
    try:
        n = int(context.args[0])
        if n < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Invalid number.")
        return
    set_setting("reward_per_ref", str(n))
    await update.message.reply_text(f"✅ Referral reward set to {n}")

async def add_stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    # format: /add_stock 4 | Netflix Account | email:pass
    text = update.message.text or ""
    raw = text.replace("/add_stock", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        await update.message.reply_text("Usage:\n/add_stock 4 | Netflix Account | email:pass")
        return
    try:
        price = int(parts[0])
    except Exception:
        await update.message.reply_text("Invalid price.")
        return
    item = parts[1]
    payload = "|".join(parts[2:]).strip()
    add_stock(item=item, price=price, payload=payload)
    await update.message.reply_text(f"✅ Added stock: {item} [{price}]")

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ban 123")
        return
    target = int(context.args[0])
    conn = db()
    cur = conn.cursor()
    ensure_user(target)
    cur.execute("UPDATE users SET banned=1 WHERE user_id=?", (target,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Banned {target}")

async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unban 123")
        return
    target = int(context.args[0])
    conn = db()
    cur = conn.cursor()
    ensure_user(target)
    cur.execute("UPDATE users SET banned=0 WHERE user_id=?", (target,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Unbanned {target}")

async def add_points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /add_points user_id amount")
        return
    target = int(context.args[0])
    amount = int(context.args[1])
    ensure_user(target)
    add_points(target, amount)
    await update.message.reply_text(f"✅ Added {amount} points to {target}")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    text = update.message.text or ""
    msg = text.replace("/broadcast", "", 1).strip()
    if not msg:
        await update.message.reply_text("Usage: /broadcast your message...")
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE banned=0")
    users = [int(r["user_id"]) for r in cur.fetchall()]
    conn.close()

    sent = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u, text=msg)
            sent += 1
        except Exception:
            pass

    await update.message.reply_text(f"✅ Broadcast done. Sent to {sent} users.")

# =========================
# MAIN
# =========================
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # user
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))

    # admin
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("set_channels", set_channels_cmd))
    app.add_handler(CommandHandler("set_support", set_support_cmd))
    app.add_handler(CommandHandler("set_ref_reward", set_ref_reward_cmd))
    app.add_handler(CommandHandler("add_stock", add_stock_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("add_points", add_points_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    # IMPORTANT: url_path uses BOT_TOKEN (hard to guess)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()
