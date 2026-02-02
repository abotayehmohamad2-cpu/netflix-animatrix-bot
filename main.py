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
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV / CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")  # https://xxxx.onrender.com
ADMIN_ID = os.getenv("ADMIN_ID")  # numeric telegram user id

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars")
if not APP_URL or not APP_URL.startswith("https://") or "onrender.com" not in APP_URL:
    raise ValueError("APP_URL missing/invalid. Example: https://xxxx.onrender.com")
if not ADMIN_ID or not ADMIN_ID.isdigit():
    raise ValueError("ADMIN_ID missing/invalid. Put numeric ID from @userinfobot")

ADMIN_ID = int(ADMIN_ID)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
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
    CREATE TABLE IF NOT EXISTS users (
      user_id INTEGER PRIMARY KEY,
      username TEXT,
      first_name TEXT,
      points INTEGER DEFAULT 0,
      referred_by INTEGER,
      joined_at TEXT,
      banned INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS config (
      k TEXT PRIMARY KEY,
      v TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rewards (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      cost INTEGER NOT NULL,
      discount INTEGER DEFAULT 0,
      created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS codes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      reward_id INTEGER NOT NULL,
      code TEXT NOT NULL,
      used_by INTEGER,
      used_at TEXT
    )
    """)

    conn.commit()
    conn.close()

    # default config
    set_config_default("ref_reward", "1")
    set_config_default("support_link", "")
    set_config_default("proofs_link", "")
    set_config_default("channels", "")  # space-separated @usernames or t.me links


def set_config_default(k: str, v: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT v FROM config WHERE k=?", (k,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO config (k,v) VALUES (?,?)", (k, v))
        conn.commit()
    conn.close()


def set_config(k: str, v: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO config (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    conn.commit()
    conn.close()


def get_config(k: str, default: str = "") -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT v FROM config WHERE k=?", (k,))
    row = cur.fetchone()
    conn.close()
    return row["v"] if row else default


def upsert_user(u) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO users (user_id, username, first_name, joined_at)
    VALUES (?,?,?,?)
    ON CONFLICT(user_id) DO UPDATE SET
      username=excluded.username,
      first_name=excluded.first_name
    """, (u.id, u.username or "", u.first_name or "", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def is_banned(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT banned FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["banned"] == 1)


def add_points(user_id: int, amount: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = COALESCE(points,0) + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def get_points(user_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["points"]) if row else 0


def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def set_referred_by_if_new(user_id: int, ref_id: int) -> bool:
    """Return True if set happened (i.e., user was new and got ref)"""
    if user_id == ref_id:
        return False
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT referred_by FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    if row["referred_by"]:
        conn.close()
        return False

    # set referred_by
    cur.execute("UPDATE users SET referred_by=? WHERE user_id=?", (ref_id, user_id))
    conn.commit()
    conn.close()
    return True


def add_reward(name: str, cost: int, discount: int = 0) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rewards (name,cost,discount,created_at) VALUES (?,?,?,?)",
        (name, cost, discount, datetime.utcnow().isoformat())
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return int(rid)


def list_rewards():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def add_codes(reward_id: int, codes: list[str]) -> int:
    conn = db()
    cur = conn.cursor()
    n = 0
    for c in codes:
        c = c.strip()
        if not c:
            continue
        cur.execute("INSERT INTO codes (reward_id, code) VALUES (?,?)", (reward_id, c))
        n += 1
    conn.commit()
    conn.close()
    return n


def available_codes_count(reward_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM codes WHERE reward_id=? AND used_by IS NULL", (reward_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["c"])


def take_code(reward_id: int, user_id: int) -> str | None:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
      SELECT id, code FROM codes
      WHERE reward_id=? AND used_by IS NULL
      ORDER BY id ASC LIMIT 1
    """, (reward_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    cur.execute(
        "UPDATE codes SET used_by=?, used_at=? WHERE id=?",
        (user_id, datetime.utcnow().isoformat(), row["id"])
    )
    conn.commit()
    conn.close()
    return row["code"]


# =========================
# HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def normalize_channel_identifier(x: str) -> str:
    x = x.strip()
    # accept @channel, t.me/channel, https://t.me/channel
    x = x.replace("https://", "").replace("http://", "")
    if x.startswith("t.me/"):
        x = x[len("t.me/"):]
    if x.startswith("@"):
        x = x[1:]
    # now x is channel username
    return x


def get_required_channels() -> list[str]:
    raw = get_config("channels", "")
    if not raw.strip():
        return []
    parts = raw.split()
    chans = []
    for p in parts:
        u = normalize_channel_identifier(p)
        if u:
            chans.append(u)
    return chans


async def is_member_of_all_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, list[str]]:
    required = get_required_channels()
    if not required:
        return True, []

    missing = []
    for ch in required:
        chat_id = f"@{ch}"
        try:
            member = await context.bot.get_chat_member(chat_id, update.effective_user.id)
            status = getattr(member, "status", "")
            if status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            # if bot can't access channel (not admin) consider it missing
            missing.append(ch)
    return (len(missing) == 0), missing


def join_keyboard(missing_channels: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing_channels:
        rows.append([InlineKeyboardButton(f"JOIN @{ch}", url=f"https://t.me/{ch}")])
    rows.append([InlineKeyboardButton("✅ I JOINED", callback_data="verify_join")])
    return InlineKeyboardMarkup(rows)


def main_menu_kb() -> InlineKeyboardMarkup:
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


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="back")]])


def rewards_kb(rewards_rows) -> InlineKeyboardMarkup:
    rows = []
    for r in rewards_rows:
        rid = r["id"]
        rows.append([InlineKeyboardButton(f"🎁 {rid}) {r['name']}", callback_data=f"reward:{rid}")])
    rows.append([InlineKeyboardButton("⬅️ BACK", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def admin_kb() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin:settings"),
         InlineKeyboardButton("🎁 Rewards", callback_data="admin:rewards")],
        [InlineKeyboardButton("👤 Users", callback_data="admin:users"),
         InlineKeyboardButton("📦 Stock", callback_data="admin:stock")],
        [InlineKeyboardButton("⬅️ BACK", callback_data="back")],
    ]
    return InlineKeyboardMarkup(kb)


# =========================
# COMMANDS
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)

    if is_banned(update.effective_user.id):
        return await update.message.reply_text("🚫 You are banned.")

    # referral
    # /start <ref_id>
    if context.args and context.args[0].isdigit():
        ref_id = int(context.args[0])
        if ref_id != update.effective_user.id:
            # only reward if user is new (no referred_by yet)
            changed = set_referred_by_if_new(update.effective_user.id, ref_id)
            if changed:
                ref_reward = int(get_config("ref_reward", "1") or "1")
                add_points(ref_id, ref_reward)
                # notify referrer (optional)
                try:
                    await context.bot.send_message(
                        chat_id=ref_id,
                        text=f"✅ دخل مستخدم جديد من رابطك!\n+{ref_reward} نقطة",
                    )
                except Exception:
                    pass

    ok, missing = await is_member_of_all_required(update, context)
    if not ok:
        return await update.message.reply_text(
            "⛔ لازم تشترك بالقنوات التالية أولاً ثم اضغط ✅ I JOINED:",
            reply_markup=join_keyboard(missing),
        )

    await update.message.reply_text(
        "✅ Welcome! Select from menu:",
        reply_markup=main_menu_kb(),
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("🚫 Admin only.")
    await update.message.reply_text("👑 Admin Panel:", reply_markup=admin_kb())


# =========================
# ADMIN TEXT COMMANDS
# =========================
async def set_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await update.message.reply_text("اكتب:\n/set_channels https://t.me/channel1 https://t.me/channel2")
    chans = [normalize_channel_identifier(x) for x in context.args]
    chans = [c for c in chans if c]
    set_config("channels", " ".join(chans))
    await update.message.reply_text(f"✅ تم ضبط قنوات الاشتراك الإجباري:\n" + "\n".join([f"@{c}" for c in chans]))


async def set_support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    link = " ".join(context.args).strip() if context.args else ""
    set_config("support_link", link)
    await update.message.reply_text(f"✅ Support link set:\n{link or '(empty)'}")


async def set_proofs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    link = " ".join(context.args).strip() if context.args else ""
    set_config("proofs_link", link)
    await update.message.reply_text(f"✅ Proofs link set:\n{link or '(empty)'}")


async def set_ref_reward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("اكتب:\n/set_ref_reward 1")
    set_config("ref_reward", str(int(context.args[0])))
    await update.message.reply_text(f"✅ Referral reward set to {context.args[0]} point(s)")


async def add_reward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add_reward Name | cost | discount
    discount optional
    """
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text.replace("/add_reward", "", 1).strip()
    if "|" not in text:
        return await update.message.reply_text(
            "اكتب مثال:\n/add_reward Netflix 1 Month | 10 | 0"
        )
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 2:
        return await update.message.reply_text("صيغة غلط.")
    name = parts[0]
    cost = int(parts[1]) if parts[1].isdigit() else None
    discount = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
    if cost is None:
        return await update.message.reply_text("cost لازم رقم.")
    rid = add_reward(name, cost, discount)
    await update.message.reply_text(f"✅ Reward added.\nID={rid}\nName={name}\nCost={cost}\nDiscount={discount}")


async def add_codes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add_codes <reward_id>
    then admin sends codes line-by-line
    finish with /done
    """
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("اكتب:\n/add_codes 1")
    rid = int(context.args[0])
    context.user_data["adding_codes"] = True
    context.user_data["adding_codes_reward_id"] = rid
    context.user_data["codes_buffer"] = []
    await update.message.reply_text(
        f"✅ تمام. ابعت الأكواد (كل كود بسطر).\nلما تخلص ابعت /done"
    )


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("adding_codes"):
        return await update.message.reply_text("ما في عملية إضافة أكواد حالياً.")
    rid = int(context.user_data.get("adding_codes_reward_id"))
    buf = context.user_data.get("codes_buffer", [])
    n = add_codes(rid, buf)
    context.user_data["adding_codes"] = False
    context.user_data["adding_codes_reward_id"] = None
    context.user_data["codes_buffer"] = []
    await update.message.reply_text(f"✅ تم إضافة {n} كود للجائزة ID={rid}")


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("اكتب:\n/ban 123456")
    uid = int(context.args[0])
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Banned {uid}")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("اكتب:\n/unban 123456")
    uid = int(context.args[0])
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Unbanned {uid}")


async def add_points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2 or (not context.args[0].isdigit()) or (not re.fullmatch(r"-?\d+", context.args[1])):
        return await update.message.reply_text("اكتب:\n/add_points 123456 10")
    uid = int(context.args[0])
    amt = int(context.args[1])
    add_points(uid, amt)
    await update.message.reply_text(f"✅ Added {amt} points to {uid}")


# =========================
# TEXT HANDLER (for add_codes mode)
# =========================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if is_admin(update.effective_user.id) and context.user_data.get("adding_codes"):
        line = update.message.text.strip()
        if line and not line.startswith("/"):
            context.user_data["codes_buffer"].append(line)
            return await update.message.reply_text("✅ added")
    # ignore other text


# =========================
# CALLBACKS
# =========================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    upsert_user(user)

    if is_banned(user.id):
        return await query.edit_message_text("🚫 You are banned.")

    # verify join
    if query.data == "verify_join":
        ok, missing = await is_member_of_all_required(update, context)
        if not ok:
            return await query.edit_message_text(
                "⛔ لسه ناقص اشتراك بالقنوات:\n" + "\n".join([f"@{c}" for c in missing]),
                reply_markup=join_keyboard(missing),
            )
        return await query.edit_message_text("✅ Welcome! Select from menu:", reply_markup=main_menu_kb())

    if query.data == "back":
        return await query.edit_message_text("✅ Welcome! Select from menu:", reply_markup=main_menu_kb())

    # main menu actions
    if query.data == "balance":
        pts = get_points(user.id)
        return await query.edit_message_text(f"💰 Your balance: *{pts}* point(s)", parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

    if query.data == "refer":
        ref_reward = int(get_config("ref_reward", "1") or "1")
        link = f"https://t.me/{context.bot.username}?start={user.id}"
        txt = (
            f"👥 REFER\n\n"
            f"🔗 Your Link:\n{link}\n\n"
            f"⭐ Reward per join+verify: *{ref_reward}* point(s)."
        )
        return await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

    if query.data == "withdraw":
        return await query.edit_message_text(
            "🏧 WITHDRAW\n\n"
            "حاليًا السحب عبر الأدمن.\n"
            "ابعت للدعم مع إثبات رصيدك.",
            reply_markup=back_kb(),
        )

    if query.data == "support":
        link = get_config("support_link", "").strip()
        if link:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🆘 Open Support", url=link)],
                [InlineKeyboardButton("⬅️ BACK", callback_data="back")]
            ])
            return await query.edit_message_text("🆘 SUPPORT", reply_markup=kb)
        return await query.edit_message_text("🆘 SUPPORT\n\n(مش محدد رابط دعم لسه)", reply_markup=back_kb())

    if query.data == "proofs":
        link = get_config("proofs_link", "").strip()
        if link:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧾 Open Proofs", url=link)],
                [InlineKeyboardButton("⬅️ BACK", callback_data="back")]
            ])
            return await query.edit_message_text("🧾 PROOFS", reply_markup=kb)
        return await query.edit_message_text("🧾 PROOFS\n\n(مش محدد رابط proofs لسه)", reply_markup=back_kb())

    if query.data == "stock":
        rows = list_rewards()
        if not rows:
            return await query.edit_message_text("📦 STOCK\n\nلا يوجد Rewards حالياً.", reply_markup=back_kb())
        lines = ["📦 STOCK\n"]
        for r in rows:
            rid = r["id"]
            cnt = available_codes_count(rid)
            lines.append(f"• ID {rid} — {r['name']} — Codes: {cnt}")
        return await query.edit_message_text("\n".join(lines), reply_markup=back_kb())

    if query.data == "rewards":
        rows = list_rewards()
        if not rows:
            return await query.edit_message_text("🎁 REWARDS\n\nلا يوجد Rewards حالياً.", reply_markup=back_kb())
        return await query.edit_message_text("🎁 اختر جائزة:", reply_markup=rewards_kb(rows))

    if query.data.startswith("reward:"):
        rid = int(query.data.split(":")[1])
        # fetch reward
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM rewards WHERE id=?", (rid,))
        r = cur.fetchone()
        conn.close()
        if not r:
            return await query.edit_message_text("الجائزة غير موجودة.", reply_markup=back_kb())

        stock = available_codes_count(rid)
        pts = get_points(user.id)
        txt = (
            f"🎁 *{r['name']}*\n"
            f"Cost: *{r['cost']}* point(s)\n"
            f"Discount: *{r['discount']}*\n"
            f"Stock: *{stock}*\n"
            f"Your balance: *{pts}*\n"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ BUY", callback_data=f"buy:{rid}")],
            [InlineKeyboardButton("⬅️ BACK", callback_data="rewards")],
        ])
        return await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    if query.data.startswith("buy:"):
        rid = int(query.data.split(":")[1])
        # load reward
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM rewards WHERE id=?", (rid,))
        r = cur.fetchone()
        conn.close()
        if not r:
            return await query.edit_message_text("Reward not found.", reply_markup=back_kb())

        stock = available_codes_count(rid)
        if stock <= 0:
            return await query.edit_message_text("❌ Out of stock.", reply_markup=back_kb())

        pts = get_points(user.id)
        cost = int(r["cost"])
        if pts < cost:
            return await query.edit_message_text(f"❌ Not enough points.\nYou have {pts}, need {cost}.", reply_markup=back_kb())

        code = take_code(rid, user.id)
        if not code:
            return await query.edit_message_text("❌ No code available right now.", reply_markup=back_kb())

        add_points(user.id, -cost)
        new_pts = get_points(user.id)
        return await query.edit_message_text(
            f"✅ Purchase successful!\n\n🎁 {r['name']}\n🔑 Code:\n`{code}`\n\n💰 New balance: {new_pts}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_kb(),
        )

    # admin panel callbacks
    if query.data.startswith("admin:"):
        if not is_admin(user.id):
            return await query.edit_message_text("🚫 Admin only.")

        if query.data == "admin:settings":
            chans = get_required_channels()
            support = get_config("support_link", "")
            proofs = get_config("proofs_link", "")
            ref_reward = get_config("ref_reward", "1")
            txt = (
                "⚙️ Admin Settings\n\n"
                f"Channels: {(' '.join(['@'+c for c in chans]) if chans else '(none)')}\n"
                f"Support: {support or '(empty)'}\n"
                f"Proofs: {proofs or '(empty)'}\n"
                f"Ref Reward: {ref_reward}\n\n"
                "أوامر:\n"
                "/set_channels <links..>\n"
                "/set_support <link>\n"
                "/set_proofs <link>\n"
                "/set_ref_reward <num>\n"
            )
            return await query.edit_message_text(txt, reply_markup=admin_kb())

        if query.data == "admin:rewards":
            txt = (
                "🎁 Admin Rewards\n\n"
                "أوامر:\n"
                "/add_reward Name | cost | discount\n"
                "/add_codes <reward_id> ثم ابعت الأكواد وبعدين /done\n"
            )
            return await query.edit_message_text(txt, reply_markup=admin_kb())

        if query.data == "admin:users":
            return await query.edit_message_text(
                "👤 Admin Users\n\n"
                "أوامر:\n"
                "/ban <user_id>\n"
                "/unban <user_id>\n"
                "/add_points <user_id> <amount>\n",
                reply_markup=admin_kb(),
            )

        if query.data == "admin:stock":
            rows = list_rewards()
            if not rows:
                return await query.edit_message_text("📦 No rewards.", reply_markup=admin_kb())
            lines = ["📦 Admin Stock\n"]
            for r in rows:
                rid = r["id"]
                cnt = available_codes_count(rid)
                lines.append(f"• ID {rid} — {r['name']} — available codes: {cnt}")
            return await query.edit_message_text("\n".join(lines), reply_markup=admin_kb())

    # fallback
    try:
        await query.edit_message_text("✅ Welcome! Select from menu:", reply_markup=main_menu_kb())
    except Exception:
        pass


# =========================
# MAIN (WEBHOOK for Render)
# =========================
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # user
    app.add_handler(CommandHandler("start", start_cmd))

    # admin
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("set_channels", set_channels_cmd))
    app.add_handler(CommandHandler("set_support", set_support_cmd))
    app.add_handler(CommandHandler("set_proofs", set_proofs_cmd))
    app.add_handler(CommandHandler("set_ref_reward", set_ref_reward_cmd))
    app.add_handler(CommandHandler("add_reward", add_reward_cmd))
    app.add_handler(CommandHandler("add_codes", add_codes_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("add_points", add_points_cmd))

    # callbacks + text (for add_codes mode)
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    port = int(os.environ.get("PORT", "10000"))

    # Webhook path = BOT_TOKEN (safer)
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
    )


if __name__ == "__main__":
    main()
