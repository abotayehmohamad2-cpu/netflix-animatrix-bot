import os
import sqlite3
import json
import traceback
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

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")

# قنوات الاشتراك الإجباري
CHANNELS = [
    "@animatrix2026",
    "@animatrix27",
]

# كل إحالة = كم نقطة
REF_POINTS = 1

# اسم ملف قاعدة البيانات
DB_FILE = "data.db"

# =========================
# PRIZES (الجوائز)
# =========================
# كل جائزة: id + اسم + نقاط + تسليم تلقائي (كود)
PRIZES = [
    {
        "id": "p1",
        "name": "اشتراك VIP 7 أيام",
        "cost": 5,
        "codes": [
            "VIP7-AAA111",
            "VIP7-BBB222",
            "VIP7-CCC333",
        ],
    },
    {
        "id": "p2",
        "name": "اشتراك VIP شهر",
        "cost": 10,
        "codes": [
            "VIP30-ZZZ999",
            "VIP30-YYY888",
        ],
    },
]

# =========================
# DB FUNCTIONS
# =========================
def db():
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        points INTEGER DEFAULT 0,
        joined_at TEXT DEFAULT NULL
    )
    """)

    # جدول لمنع تكرار نقاط الإحالة لنفس الشخص
    c.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        new_user_id INTEGER PRIMARY KEY,
        referrer_id INTEGER,
        created_at TEXT
    )
    """)

    # أكواد الجوائز
    c.execute("""
    CREATE TABLE IF NOT EXISTS prize_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prize_id TEXT,
        code TEXT,
        is_used INTEGER DEFAULT 0,
        used_by INTEGER DEFAULT NULL,
        used_at TEXT DEFAULT NULL
    )
    """)

    # سجل عمليات الشراء
    c.execute("""
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        prize_id TEXT,
        prize_name TEXT,
        cost INTEGER,
        delivered_code TEXT,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()

def seed_prizes_codes():
    """يحط أكواد الجوائز داخل DB مرة وحدة فقط"""
    conn = db()
    c = conn.cursor()

    for prize in PRIZES:
        for code in prize["codes"]:
            # إذا الكود موجود لا تعيده
            c.execute("SELECT 1 FROM prize_codes WHERE prize_id=? AND code=?", (prize["id"], code))
            exists = c.fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO prize_codes(prize_id, code, is_used) VALUES(?,?,0)",
                    (prize["id"], code)
                )

    conn.commit()
    conn.close()

def ensure_user(user_id: int):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not c.fetchone():
        c.execute(
            "INSERT INTO users(user_id, points, joined_at) VALUES(?,?,?)",
            (user_id, 0, datetime.utcnow().isoformat())
        )
    conn.commit()
    conn.close()

def get_points(user_id: int) -> int:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def add_points(user_id: int, amount: int):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def spend_points(user_id: int, amount: int) -> bool:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    if row[0] < amount:
        conn.close()
        return False

    c.execute("UPDATE users SET points = points - ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()
    return True

def referral_already_used(new_user_id: int) -> bool:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM referrals WHERE new_user_id=?", (new_user_id,))
    ok = c.fetchone() is not None
    conn.close()
    return ok

def save_referral(new_user_id: int, referrer_id: int):
    conn = db()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO referrals(new_user_id, referrer_id, created_at) VALUES(?,?,?)",
        (new_user_id, referrer_id, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

def get_prize(prize_id: str):
    for p in PRIZES:
        if p["id"] == prize_id:
            return p
    return None

def take_code(prize_id: str, user_id: int):
    """يرجع كود غير مستخدم ويعلمه مستخدم"""
    conn = db()
    c = conn.cursor()

    c.execute("""
        SELECT id, code FROM prize_codes
        WHERE prize_id=? AND is_used=0
        ORDER BY id ASC
        LIMIT 1
    """, (prize_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        return None

    code_row_id, code = row

    c.execute("""
        UPDATE prize_codes
        SET is_used=1, used_by=?, used_at=?
        WHERE id=?
    """, (user_id, datetime.utcnow().isoformat(), code_row_id))

    conn.commit()
    conn.close()
    return code

def save_purchase(user_id: int, prize_id: str, prize_name: str, cost: int, delivered_code: str | None):
    conn = db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO purchases(user_id, prize_id, prize_name, cost, delivered_code, created_at)
        VALUES(?,?,?,?,?,?)
    """, (user_id, prize_id, prize_name, cost, delivered_code, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

# =========================
# TELEGRAM HELPERS
# =========================
async def is_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    for ch in CHANNELS:
        try:
            member = await context.bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked"):
                return False
        except:
            return False
    return True

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ نقاطي", callback_data="points")],
        [InlineKeyboardButton("🔗 رابط الإحالة", callback_data="ref")],
        [InlineKeyboardButton("🎁 الجوائز", callback_data="prizes")],
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check")],
    ])

def prizes_menu():
    rows = []
    for p in PRIZES:
        rows.append([InlineKeyboardButton(f"{p['name']} ({p['cost']} نقاط)", callback_data=f"buy:{p['id']}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="back")])
    return InlineKeyboardMarkup(rows)

# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id)

    # قراءة ref من رابط start
    referrer_id = None
    if context.args:
        try:
            referrer_id = int(context.args[0])
        except:
            referrer_id = None

    msg = (
        "أهلاً بك 👋\n\n"
        "📌 هذا بوت إحالات ونقاط.\n"
        "لازم تشترك بالقنوات أولاً:\n"
        f"1) {CHANNELS[0]}\n"
        f"2) {CHANNELS[1]}\n\n"
        "بعد الاشتراك اضغط زر ✅ تحقق من الاشتراك\n"
    )

    # حفظ الإحالة (لكن نقاط ما تنحسب إلا بعد /check)
    if referrer_id and referrer_id != user.id and not referral_already_used(user.id):
        save_referral(user.id, referrer_id)

    await update.message.reply_text(msg, reply_markup=main_menu())

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await is_subscribed(user_id, context):
        await update.message.reply_text("❌ لسه ما اشتركت بالقنوات. اشترك ثم ارجع اضغط تحقق.", reply_markup=main_menu())
        return

    # إذا في إحالة محفوظة ولم يتم إعطاء نقاط من قبل
    conn = db()
    c = conn.cursor()
    c.execute("SELECT referrer_id FROM referrals WHERE new_user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()

    if row:
        referrer_id = row[0]
        # نعطي النقاط للراعي مرة وحدة فقط
        # (نحذف السطر من referrals حتى ما تتكرر)
        conn = db()
        c = conn.cursor()
        c.execute("DELETE FROM referrals WHERE new_user_id=?", (user_id,))
        conn.commit()
        conn.close()

        ensure_user(referrer_id)
        add_points(referrer_id, REF_POINTS)

    await update.message.reply_text("✅ تم التحقق! تقدر تستخدم البوت الآن.", reply_markup=main_menu())

# =========================
# CALLBACKS
# =========================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    ensure_user(user_id)

    data = query.data

    if data == "check":
        # نفس check_cmd لكن للزر
        if not await is_subscribed(user_id, context):
            await query.edit_message_text("❌ اشترك بالقنوات أولاً ثم اضغط تحقق.", reply_markup=main_menu())
            return

        conn = db()
        c = conn.cursor()
        c.execute("SELECT referrer_id FROM referrals WHERE new_user_id=?", (user_id,))
        row = c.fetchone()
        conn.close()

        if row:
            referrer_id = row[0]
            conn = db()
            c = conn.cursor()
            c.execute("DELETE FROM referrals WHERE new_user_id=?", (user_id,))
            conn.commit()
            conn.close()

            ensure_user(referrer_id)
            add_points(referrer_id, REF_POINTS)

        await query.edit_message_text("✅ تم التحقق! تقدر تستخدم البوت.", reply_markup=main_menu())
        return

    # منع الاستخدام بدون اشتراك
    if not await is_subscribed(user_id, context):
        await query.edit_message_text("❌ لازم تشترك بالقنوات أولاً.\nثم اضغط تحقق.", reply_markup=main_menu())
        return

    if data == "points":
        points = get_points(user_id)
        await query.edit_message_text(f"⭐ نقاطك: {points}", reply_markup=main_menu())

    elif data == "ref":
        bot_username = (await context.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start={user_id}"
        await query.edit_message_text(
            "🔗 رابط الإحالة الخاص فيك:\n"
            f"{link}\n\n"
            f"كل شخص يدخل من رابطك ويعمل تحقق = تاخذ {REF_POINTS} نقطة ✅",
            reply_markup=main_menu()
        )

    elif data == "prizes":
        await query.edit_message_text("🎁 اختر الجائزة:", reply_markup=prizes_menu())

    elif data == "back":
        await query.edit_message_text("القائمة الرئيسية:", reply_markup=main_menu())

    elif data.startswith("buy:"):
        prize_id = data.split("buy:")[1]
        prize = get_prize(prize_id)

        if not prize:
            await query.edit_message_text("❌ الجائزة غير موجودة.", reply_markup=main_menu())
            return

        cost = prize["cost"]
        points = get_points(user_id)

        if points < cost:
            await query.edit_message_text(
                f"❌ نقاطك غير كافية.\nنقاطك: {points}\nسعر الجائزة: {cost}",
                reply_markup=main_menu()
            )
            return

        # خصم النقاط
        ok = spend_points(user_id, cost)
        if not ok:
            await query.edit_message_text("❌ فشل الخصم. حاول مرة ثانية.", reply_markup=main_menu())
            return

        # تسليم تلقائي (كود)
        code = take_code(prize_id, user_id)

        if code:
            save_purchase(user_id, prize_id, prize["name"], cost, code)
            await query.edit_message_text(
                f"✅ تم شراء الجائزة بنجاح!\n\n"
                f"🎁 الجائزة: {prize['name']}\n"
                f"⭐ تم خصم: {cost} نقاط\n\n"
                f"🔑 كودك:\n`{code}`",
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )
        else:
            # إذا ما في أكواد
            save_purchase(user_id, prize_id, prize["name"], cost, None)
            await query.edit_message_text(
                f"✅ تم شراء الجائزة!\n"
                f"لكن حالياً لا يوجد أكواد متوفرة.\n"
                f"📩 تواصل مع الإدارة لاستلام الجائزة.",
                reply_markup=main_menu()
            )

# =========================
# RUN
# =========================
def run():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN missing. Add it in Render Environment Variables as BOT_TOKEN")

    init_db()
    seed_prizes_codes()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CallbackQueryHandler(on_button))

    app.run_polling()


if __name__ == "__main__":
    try:
        run()
    except Exception:
        print("CRASH TRACEBACK:")
        print(traceback.format_exc())
        raise
