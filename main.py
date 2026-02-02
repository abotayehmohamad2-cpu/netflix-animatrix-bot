import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# Render أحيانًا يعطيك رابط جاهز بهذا المتغير، بنستخدمه إذا موجود
EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")  # مثال: https://xxxx.onrender.com

# وإذا مش موجود، استخدم APP_URL اللي بتحطه إنت
APP_URL = os.getenv("APP_URL")

ADMIN_ID = os.getenv("ADMIN_ID")  # اختياري (قيمة رقمك)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars")

BASE_URL = (EXTERNAL_URL or APP_URL or "").strip().rstrip("/")

if not BASE_URL.startswith("https://"):
    raise ValueError(
        "APP_URL missing or invalid. Put full URL like: https://xxxx.onrender.com "
        "(NOT t.me link)"
    )

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# =========================
# UI HELPERS
# =========================
def main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🎁 الجوائز", callback_data="prizes")],
        [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

def prizes_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("✅ تحقق", callback_data="check")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)

# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا 👋\n"
        "البوت شغال على Render ✅\n\n"
        "اختار من القائمة:",
        reply_markup=main_menu(),
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ أوامر البوت:\n"
        "/start - تشغيل البوت\n"
        "/help - مساعدة\n",
        reply_markup=main_menu(),
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "prizes":
        await query.edit_message_text(
            "🎁 قائمة الجوائز (مثال)\n"
            "اضغط تحقق:",
            reply_markup=prizes_menu(),
        )

    elif query.data == "check":
        await query.edit_message_text(
            "✅ البوت شغال تمام.",
            reply_markup=prizes_menu(),
        )

    elif query.data == "help":
        await query.edit_message_text(
            "ℹ️ المساعدة:\n"
            "- البوت شغال Webhook على Render.\n"
            "- ابعت /start.\n\n"
            "ملاحظة: فتح رابط Render بالمتصفح ممكن يعطي 404 وهذا طبيعي.",
            reply_markup=main_menu(),
        )

    elif query.data == "back":
        await query.edit_message_text("اختار من القائمة:", reply_markup=main_menu())

# =========================
# MAIN (WEBHOOK)
# =========================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(on_button))

    port = int(os.environ.get("PORT", "10000"))

    # نخلي مسار الويبهوك = التوكن (سري)
    url_path = BOT_TOKEN
    webhook_url = f"{BASE_URL}/{url_path}"

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=url_path,
        webhook_url=webhook_url,
    )

if __name__ == "__main__":
    main()
