# bot.py
import asyncio
from fastapi import APIRouter
from telegram import Update, Bot, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, ConversationHandler, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv
from datetime import datetime
import os

load_dotenv()

PARKING_BLOCKS = {
    1: [],
    2: [1],
    3: [],
    4: [3],
    5: [],
    6: [5],
    7: [],
    8: [7],
    9: [],
    10: [9],
    11: [10, 9],
    12: [],
    13: [12],
    14: [],
    15: [],
    16: [],
    17: [],
    18: [],
    19: [],
    20: [],
    21: [],
    22: [23, 24],
    23: [24],
    24: [],
    25: [26],
    26: [],
    27: [28],
    28: [],
    29: [30],
    30: [],
    31: [],

}
PARKED_SLOTS = {}
USER_PHONES = {}


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL") + WEBHOOK_PATH

application = Application.builder().token(TOKEN).build()

# Get manu func


def get_main_menu():
    keyboard = [
        ["🅿️ Park", "🚶 Leave"],
        ["📋 Status", "📱 Share Phone"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# /start func


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["🅿️ Park", "🚶 Leave"],
        ["📋 Status", "📱 Share Phone"],
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Select an option ⬇️"
    )
    await update.message.reply_text("👋 Welcome! Choose an option below:", reply_markup=reply_markup)
application.add_handler(CommandHandler("start", start))

# /status func


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    taken = sorted(PARKED_SLOTS.keys())
    total_slots = sorted(PARKING_BLOCKS.keys())
    free = [slot for slot in total_slots if slot not in taken]

    taken_list = ', '.join(str(s) for s in taken) or "None"
    free_list = ', '.join(str(s) for s in free) or "None"

    msg = f"📋 *Parking Status:*\n\n"
    msg += f"🟢 Free slots: {free_list}\n"
    msg += f"🔴 Taken slots: {taken_list}"

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_menu())
application.add_handler(CommandHandler("status", status))
application.add_handler(MessageHandler(
    filters.TEXT & filters.Regex("^📋 Status$"), status))

# Phone share flow
SHARE_PHONE = 2


async def ask_for_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if phone already saved
    user_id = update.effective_user.id
    if user_id in USER_PHONES:
        await update.message.reply_text(
            "✅ Your phone number is already saved!",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    keyboard = [[KeyboardButton("📱 Share my phone", request_contact=True)], [
        "❌ Cancel"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("📱 Tap the button to share your phone number:", reply_markup=reply_markup)
    return SHARE_PHONE


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact:
        user_id = contact.user_id
        USER_PHONES[user_id] = contact.phone_number
        await update.message.reply_text("✅ Phone number saved!", reply_markup=get_main_menu())
        return ConversationHandler.END


async def cancel_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Phone sharing cancelled.", reply_markup=get_main_menu())
    return ConversationHandler.END
application.add_handler(ConversationHandler(
    entry_points=[
        CommandHandler("sharephone", ask_for_phone),
        MessageHandler(filters.TEXT & filters.Regex(
            "^📱 Share Phone$"), ask_for_phone)
    ],
    states={
        SHARE_PHONE: [
            MessageHandler(filters.CONTACT, receive_phone),
            # Catch any non-contact reply
            MessageHandler(filters.TEXT & ~filters.COMMAND, cancel_phone)
        ]
    },
    fallbacks=[
        CommandHandler("cancel", cancel_phone),
        MessageHandler(filters.TEXT & filters.Regex(
            "^❌ Cancel$"), cancel_phone)
    ]
))

# /park flow
PARKING_INPUT = 1  # State for parking input

# Step 1: User taps 🅿️ Park


async def ask_parking_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["❌ Cancel"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, one_time_keyboard=True, resize_keyboard=True
    )
    await update.message.reply_text(
        "📝 Please enter your parking slot number (1–31):",
        reply_markup=reply_markup
    )
    return PARKING_INPUT
# Step 2: User provides slot number


async def handle_parking_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.full_name
    user_id = user.id
    phone = USER_PHONES.get(user_id, "No phone number shared")

    text = update.message.text.strip()

    # Allow cancel fallback
    if text == "❌ Cancel":
        return await cancel_parking(update, context)

    # Validate input
    if not text.isdigit():
        keyboard = [["❌ Cancel"]]
        reply_markup = ReplyKeyboardMarkup(
            keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("❌ Please enter a valid number (1–31):", reply_markup=reply_markup)
        return PARKING_INPUT

    slot = int(text)

    # 1. Check range
    if slot not in PARKING_BLOCKS:
        keyboard = [["❌ Cancel"]]
        reply_markup = ReplyKeyboardMarkup(
            keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("❌ Invalid slot. Choose a number between 1 and 31:", reply_markup=reply_markup)
        return PARKING_INPUT

    # 2. Already parked
    for s, info in PARKED_SLOTS.items():
        if info["user_id"] == user_id:
            await update.message.reply_text(f"❌ You are already parked in slot {s}. Please /leave first.", reply_markup=get_main_menu())
            return ConversationHandler.END

    # 3. Slot taken
    if slot in PARKED_SLOTS:
        keyboard = [["❌ Cancel"]]
        reply_markup = ReplyKeyboardMarkup(
            keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("❌ That slot is already taken. Try another (1–31):", reply_markup=reply_markup)
        return PARKING_INPUT

    # 4. Save the slot
    PARKED_SLOTS[slot] = {
        "user_id": user_id,
        "name": name,
        "phone": phone,
        "time": datetime.now()
    }

    await update.message.reply_text(f"✅ {name}, you parked in slot {slot}.", reply_markup=get_main_menu())

    # 5. Notify any blocked users
    for blocked_slot in PARKING_BLOCKS.get(slot, []):
        blocked_info = PARKED_SLOTS.get(blocked_slot)
        if blocked_info:
            try:
                await context.bot.send_message(
                    chat_id=blocked_info["user_id"],
                    text=f"🚧 You're blocked by {name} in slot {slot}.\n📱 Phone: {phone}"
                )
                # Notify the parker about who they are blocking
                blocked_name = blocked_info["name"]
                blocked_phone = blocked_info.get("phone", "No phone shared")
                await update.message.reply_text(f"⚠️ You are blocking {blocked_name} in slot {blocked_slot}.\n📱 Phone: {blocked_phone}")
            except Exception as e:
                print(f"Could not notify {blocked_info['name']}: {e}")

    return ConversationHandler.END
# Cancel handler


async def cancel_parking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Parking cancelled.", reply_markup=get_main_menu())
    return ConversationHandler.END

# Register handler
application.add_handler(ConversationHandler(
    entry_points=[
        MessageHandler(filters.TEXT & filters.Regex(
            "^🅿️ Park$"), ask_parking_slot)
    ],
    states={
        PARKING_INPUT: [
            MessageHandler(filters.TEXT & filters.Regex(
                "^❌ Cancel$"), cancel_parking),
            MessageHandler(filters.TEXT & ~filters.COMMAND,
                           handle_parking_slot)
        ]
    },
    fallbacks=[
        MessageHandler(filters.TEXT & filters.Regex(
            "^❌ Cancel$"), cancel_parking),
        CommandHandler("cancel", cancel_parking)
    ]
))

# /leave func


async def leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.full_name
    # Find the user's parking slot
    for slot, info in list(PARKED_SLOTS.items()):
        if info["user_id"] == user_id:
            del PARKED_SLOTS[slot]
            await update.message.reply_text(f"👋 {name}, you’ve left slot {slot}. It is now available.")
            for blocled_slot in PARKING_BLOCKS.get(slot, []):
                blocked_info = PARKED_SLOTS.get(blocled_slot)
                if blocked_info:
                    await context.bot.send_message(
                        chat_id=blocked_info["user_id"],
                        text=f"🚧 Slot {slot} is now available."
                    )
            return
    await update.message.reply_text("❌ You’re not parked in any slot.")
application.add_handler(MessageHandler(
    filters.TEXT & filters.Regex("^🚶 Leave$"), leave))
application.add_handler(CommandHandler("leave", leave))


async def set_webhook():
    bot = Bot(token=TOKEN)
    await bot.set_webhook(url=WEBHOOK_URL)
# FastAPI webhook route
router = APIRouter()


@router.post(WEBHOOK_PATH)
async def telegram_webhook(update: dict):
    update_obj = Update.de_json(update, bot=application.bot)

    print("📩 Webhook triggered")

    # 🔧 THE FIX: Ensure app is initialized
    await application.initialize()

    # Process the update
    await application.process_update(update_obj)

bot_app = router
