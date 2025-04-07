# bot.py
from fastapi import APIRouter
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL") + WEBHOOK_PATH

application = Application.builder().token(TOKEN).build()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚗 Welcome to the Parking Bot!")

application.add_handler(CommandHandler("start", start))

# Webhook setup


async def set_webhook():
    bot = Bot(token=TOKEN)
    await bot.set_webhook(url=WEBHOOK_URL)

# FastAPI webhook route
router = APIRouter()


@router.post(WEBHOOK_PATH)
async def telegram_webhook(update: dict):
    update_obj = Update.de_json(update, bot=application.bot)

    # 🔧 THE FIX: Ensure app is initialized
    await application.initialize()

    # Process the update
    await application.process_update(update_obj)

bot_app = router
