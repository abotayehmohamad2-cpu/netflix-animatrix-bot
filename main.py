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
APP_URL = os.getenv("APP_URL")  # مثال: https://v7ty4ob6m6.onrender.com
ADMIN_ID = os.getenv("ADMIN_ID")  # اختياري: رقم تيليجرام تبعك

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing. Add it in Render Env Vars")
if not APP_URL:
    raise ValueError("APP_URL missing. Add it in Render Env Vars (must be full https://xxxx.onrender.com)")

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
    # /start
    await update.message.reply_text(
        "أهلًا 👋\n"
        "هذا بوت تجريبي شغال على Render عبر Webhook ✅\n\n"
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
            "✅ البوت شغال تمام.\n"
            "إذا بدك نكمل نضيف نظام جوائز/أكواد خبرني.",
            reply_markup=prizes_menu(),
        )

    elif query.data == "help":
        await query.edit_message_text(
            "ℹ️ المساعدة:\n"
            "- البوت شغال Webhook على Render.\n"
            "- افتح تيليجرام وابعت /start.\n\n"
            "ملاحظة: فتح رابط Render بالمتصفح قد يعطي 404 وهذا طبيعي.",
            reply_markup=main_menu(),
        )

    elif query.data == "back":
        await query.edit_message_text(
            "اختار من القائمة:",
            reply_markup=main_menu(),
        )

# =========================
# MAIN (WEBHOOK)
# =========================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CallbackQueryHandler(on_button))

    port = int(os.environ.get("PORT", "10000"))

    # webhook path = BOT_TOKEN (لأمان أكثر)
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()
