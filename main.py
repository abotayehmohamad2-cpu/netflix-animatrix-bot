import os
import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ======================
# CONFIG (Render Env Vars)
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")  # مثال: https://netflixanimatrixgiveawy-bot.onrender.com
DB_PATH = os.getenv("DB_PATH", "bot.db")

# (اختياري) أدمنات: "123,456"
ADMIN_IDS = set()
if os.getenv("ADMIN_IDS"):
    try:
        ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
    except Exception:
        ADMIN_IDS = set()

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars")
if not APP_URL:
    raise ValueError("APP_URL missing. Add it in Render Env Vars")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("animatrix-bot")


# ======================
# DB Helpers
# ======================
def db_connect():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    con = db_connect()
    cur = con.cursor()

    # users: معلومات أساسية + عدد المحاولات/entries
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id     INTEGER PRIMARY KEY,
        username    TEXT,
        first_name  TEXT,
        created_at  TEXT,
        entries     INTEGER DEFAULT 0
    )
    """)

    # codes: أكواد الجوائز/السحبة (مثال)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS codes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT UNIQUE,
        used        INTEGER DEFAULT 0,
        used_by     INTEGER,
        used_at     TEXT
    )
    """)

    # purchases/logs (اختياري)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        action      TEXT,
        created_at  TEXT
    )
    """)

    con.commit()
    con.close()


def upsert_user(user):
    con = db_connect()
    cur = con.cursor()

    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    exists = cur.fetchone()

    if not exists:
        cur.execute(
            "INSERT INTO users(user_id, username, first_name, created_at, entries) VALUES(?,?,?,?,?)",
            (user.id, user.username or "", user.first_name or "", datetime.utcnow().isoformat(), 0)
        )
    else:
        cur.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (user.username or "", user.first_name or "", user.id)
        )

    con.commit()
    con.close()


def add_log(user_id: int, action: str):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO logs(user_id, action, created_at) VALUES(?,?,?)",
        (user_id, action, datetime.utcnow().isoformat())
    )
    con.commit()
    con.close()


def get_entries(user_id: int) -> int:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT entries FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return int(row[0]) if row else 0


def increment_entries(user_id: int, n: int = 1):
    con = db_connect()
    cur = con.cursor()
    cur.execute("UPDATE users SET entries = COALESCE(entries,0) + ? WHERE user_id=?", (n, user_id))
    con.commit()
    con.close()


def seed_codes_if_empty(default_codes=None):
    """يحط أكواد تجريبية إذا جدول الأكواد فاضي"""
    if default_codes is None:
        default_codes = ["A1B2C3", "Z9Y8X7", "QW12ER", "NX55TT"]

    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM codes")
    cnt = cur.fetchone()[0]

    if cnt == 0:
        for c in default_codes:
            try:
                cur.execute("INSERT INTO codes(code, used) VALUES(?,0)", (c,))
            except sqlite3.IntegrityError:
                pass
        con.commit()

    con.close()


def claim_one_code(user_id: int):
    """يرجع كود فاضي، ويعلّمه used=1"""
    con = db_connect()
    cur = con.cursor()

    cur.execute("SELECT id, code FROM codes WHERE used=0 ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()

    if not row:
        con.close()
        return None

    code_id, code = row
    cur.execute(
        "UPDATE codes SET used=1, used_by=?, used_at=? WHERE id=?",
        (user_id, datetime.utcnow().isoformat(), code_id)
    )
    con.commit()
    con.close()
    return code


# ======================
# UI
# ======================
def main_menu(is_admin: bool = False):
    buttons = [
        [InlineKeyboardButton("✅ دخول السحبـة (+1 محاولة)", callback_data="join")],
        [InlineKeyboardButton("🎟️ محاولاتي", callback_data="my_entries")],
        [InlineKeyboardButton("🧾 استلام كود (مثال)", callback_data="claim_code")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("🛠️ لوحة الأدمن", callback_data="admin")])
    return InlineKeyboardMarkup(buttons)


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة أكواد", callback_data="admin_add_codes_help")],
        [InlineKeyboardButton("📊 إحصائيات سريعة", callback_data="admin_stats")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="back_home")],
    ])


# ======================
# Handlers
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user)
    add_log(user.id, "start")

    is_admin = user.id in ADMIN_IDS
    text = (
        "👋 أهلاً في بوت Animatrix Giveaway!\n\n"
        "اختر من القائمة:\n"
        "✅ دخول السحبة يزيد لك محاولة\n"
        "🎟️ محاولاتي يعرض عدد المحاولات\n"
        "🧾 استلام كود (مثال) يعطيك كود إذا متوفر\n"
    )

    await update.message.reply_text(text, reply_markup=main_menu(is_admin=is_admin))


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر اختياري: /check يعرض عدد المحاولات"""
    user = update.effective_user
    upsert_user(user)
    entries = get_entries(user.id)
    await update.message.reply_text(f"🎟️ محاولاتك الحالية: {entries}")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    upsert_user(user)
    is_admin = user.id in ADMIN_IDS

    data = query.data

    if data == "join":
        increment_entries(user.id, 1)
        add_log(user.id, "join_giveaway")
        entries = get_entries(user.id)
        await query.edit_message_text(
            f"✅ تم تسجيلك في السحبة! محاولاتك الآن: {entries}",
            reply_markup=main_menu(is_admin=is_admin)
        )
        return

    if data == "my_entries":
        entries = get_entries(user.id)
        await query.edit_message_text(
            f"🎟️ محاولاتك الحالية: {entries}",
            reply_markup=main_menu(is_admin=is_admin)
        )
        return

    if data == "claim_code":
        code = claim_one_code(user.id)
        add_log(user.id, "claim_code")

        if not code:
            await query.edit_message_text(
                "❌ ما في أكواد متوفرة حالياً.\nارجع لاحقاً.",
                reply_markup=main_menu(is_admin=is_admin)
            )
        else:
            await query.edit_message_text(
                f"🧾 هذا كودك:\n`{code}`",
                parse_mode="Markdown",
                reply_markup=main_menu(is_admin=is_admin)
            )
        return

    if data == "admin":
        if not is_admin:
            await query.edit_message_text("❌ غير مسموح.", reply_markup=main_menu(is_admin=False))
            return
        await query.edit_message_text("🛠️ لوحة الأدمن:", reply_markup=admin_menu())
        return

    if data == "admin_add_codes_help":
        if not is_admin:
            await query.edit_message_text("❌ غير مسموح.", reply_markup=main_menu(is_admin=False))
            return
        msg = (
            "➕ لإضافة أكواد:\n"
            "ابعت رسالة للبوت بالشكل التالي:\n\n"
            "`/addcodes CODE1 CODE2 CODE3`\n\n"
            "مثال:\n"
            "`/addcodes AAA111 BBB222 CCC333`"
        )
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=admin_menu())
        return

    if data == "admin_stats":
        if not is_admin:
            await query.edit_message_text("❌ غير مسموح.", reply_markup=main_menu(is_admin=False))
            return

        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        users_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM codes")
        codes_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM codes WHERE used=1")
        codes_used = cur.fetchone()[0]
        con.close()

        await query.edit_message_text(
            f"📊 إحصائيات:\n"
            f"- المستخدمين: {users_count}\n"
            f"- الأكواد: {codes_total}\n"
            f"- المستخدمة: {codes_used}\n",
            reply_markup=admin_menu()
        )
        return

    if data == "back_home":
        await query.edit_message_text("🏠 القائمة الرئيسية:", reply_markup=main_menu(is_admin=is_admin))
        return


async def addcodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ غير مسموح.")
        return

    if not context.args:
        await update.message.reply_text("استخدم: /addcodes CODE1 CODE2 ...")
        return

    codes = context.args

    con = db_connect()
    cur = con.cursor()
    added = 0
    for c in codes:
        c = c.strip()
        if not c:
            continue
        try:
            cur.execute("INSERT INTO codes(code, used) VALUES(?,0)", (c,))
            added += 1
        except sqlite3.IntegrityError:
            pass
    con.commit()
    con.close()

    await update.message.reply_text(f"✅ تم إضافة {added} كود.")


# ======================
# WEBHOOK RUN (Render)
# ======================
def run():
    init_db()
    seed_codes_if_empty()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CommandHandler("addcodes", addcodes))
    app.add_handler(CallbackQueryHandler(on_button))

    port = int(os.environ.get("PORT", "10000"))

    # نخلي url_path = BOT_TOKEN (صعب التخمين)
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
    )


if __name__ == "__main__":
    run()
