# bot.py
from datetime import datetime
from pytz import timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter
from telegram import Update, Bot, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, ConversationHandler, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os
import json


load_dotenv()


PHONES_FILE = "user_phones.json"
SAVE_FILE = "parking_data.json"
ADMIN_IDS = {1997945569}  # Replace with your Telegram user ID(s)
PARKING_YARDS = {
    "Hamasger50": {
        "slots": {},
        "blocks": {

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
        },
        "charging_slots": [],

    },
    "BeitNip": {
        "slots": {},
        "blocks": {
            1: [], 2: [],
        },
        "charging_slots": [1, 2],
    }
}
USER_PHONES = {}
USER_YARD = {}  # key = user_id, value = yard name
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL") + WEBHOOK_PATH
application = Application.builder() \
    .token(TOKEN) \
    .post_init(lambda app: app.job_queue.set_application(app)) \
    .build()


def load_phones():
    global USER_PHONES
    try:
        with open(PHONES_FILE, "r") as f:
            loaded = json.load(f)
            # Convert keys back to int!
            USER_PHONES = {int(k): v for k, v in loaded.items()}
        print("✅ USER_PHONES loaded:", USER_PHONES)
    except (FileNotFoundError, json.JSONDecodeError):
        USER_PHONES = {}
        print("⚠️ No phones loaded (file not found or bad format)")


def save_phones():
    with open(PHONES_FILE, "w") as f:
        json.dump(USER_PHONES, f)


# Admin-only command to reset all yards


async def admin_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ This command is restricted.")
        return

    for yard in PARKING_YARDS.values():
        yard["slots"].clear()
    await update.message.reply_text("✅ All parking yards have been reset.")
application.add_handler(CommandHandler("reset_all", admin_reset))
# Get manu func


def get_main_menu(user_id=None):
    if user_id is None or user_id not in USER_YARD:
        keyboard = [["🏢 Choose Yard"]]
    else:
        keyboard = [
            ["🅿️ Park", "🚶 Leave"],
            ["📋 Status", "📱 Share Phone"],
            ["🏢 Choose Yard"]
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
# /start func


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reply_markup = get_main_menu(user_id)
    await update.message.reply_text("👋 Welcome! Choose an option below:", reply_markup=reply_markup)
application.add_handler(CommandHandler("start", start))


SELECT_YARD = 3

# Yard chooser flow


async def choose_yard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[yard] for yard in PARKING_YARDS.keys()] + [["❌ Cancel"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("🏢 Choose a parking yard:", reply_markup=reply_markup)
    return SELECT_YARD


async def set_yard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected = update.message.text.strip()
    user_id = update.effective_user.id
    if selected in PARKING_YARDS:
        USER_YARD[user_id] = selected
        await update.message.reply_text(f"✅ You’re now using *{selected}*.", parse_mode="Markdown", reply_markup=get_main_menu(user_id))
    else:
        await update.message.reply_text("❌ Invalid yard selection.", reply_markup=get_main_menu(user_id))
    return ConversationHandler.END

# Helper to enforce yard selection before using features


async def ensure_yard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in USER_YARD:
        await update.message.reply_text(
            "⚠️ Please choose a parking yard first.",
            reply_markup=get_main_menu(user_id)
        )
        return None
    return USER_YARD[user_id]

# Register yard chooser handler


application.add_handler(ConversationHandler(
    entry_points=[
        MessageHandler(filters.TEXT & filters.Regex(
            "^🏢 Choose Yard$"), choose_yard)
    ],
    states={
        SELECT_YARD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, set_yard)
        ]
    },
    fallbacks=[
        MessageHandler(filters.TEXT & filters.Regex("^❌ Cancel$"), set_yard)
    ]
))

# /status func


...


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    yard_name = await ensure_yard(update, context)
    if yard_name is None:
        return
    yard = PARKING_YARDS[yard_name]
    taken = sorted(yard["slots"].keys())
    total_slots = sorted(yard["blocks"].keys())
    free = [slot for slot in total_slots if slot not in taken]

    # Time now for calculating durations
    now = datetime.now()
    taken_lines = []
    for slot in taken:
        parked = yard["slots"][slot]
        name = parked["name"]
        icon = "⚡ " if slot in yard.get("charging_slots", []) else ""
        if slot in yard.get("charging_slots", []):
            parked_time = datetime.fromisoformat(parked["time"])
            duration = now - parked_time
            minutes = int(duration.total_seconds() // 60)
            time_str = f" ({minutes // 60}h {minutes % 60}m)"
        else:
            time_str = ""
        taken_lines.append(f"{icon}{slot} - {name}{time_str}")
    taken_text = '\n'.join(taken_lines) or "None"
    free_list = ', '.join(str(s) for s in free) or "None"

    msg = f"📋 *{yard_name} Parking Status:*\n\n"
    msg += f"🟢 Available slots: {free_list}\n\n"
    msg += f"🔴 Taken slots:\n{taken_text}"

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_menu(user_id))


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
            reply_markup=get_main_menu(user_id)
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
        phone = contact.phone_number

        # ✅ Don't save duplicate
        if USER_PHONES.get(user_id) == phone:
            await update.message.reply_text(
                "📱 Your phone number is already saved!",
                reply_markup=get_main_menu(user_id)
            )
            return ConversationHandler.END

        # Save if new or changed
        USER_PHONES[user_id] = phone
        save_phones()
        await update.message.reply_text("✅ Phone number saved!", reply_markup=get_main_menu(user_id))
        return ConversationHandler.END


async def cancel_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("❌ Phone sharing cancelled.", reply_markup=get_main_menu(user_id))
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

# park flow
PARKING_INPUT = 1  # State for parking input


async def send_charging_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    print("🔔 Reminder fired for:", data)  # ✅ Log
    await context.bot.send_message(
        chat_id=data["user_id"],
        text=f"⚡ Reminder: You've been in charging slot {data['slot']} (Yard: {data['yard']}) for 1.5 hours. Please free it if you're done charging. 🔌"
    )


# Step 1: User taps 🅿️ Park
async def ask_parking_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    yard_name = await ensure_yard(update, context)
    if yard_name is None:
        return
    keyboard = [["❌ Cancel"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, one_time_keyboard=True, resize_keyboard=True
    )
    await update.message.reply_text(
        "📝 Please enter your parking slot number:",
        reply_markup=reply_markup
    )
    return PARKING_INPUT
# Step 2: User provides slot number


async def handle_parking_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    name = user.full_name
    phone = USER_PHONES.get(user_id, "No phone number shared")
    yard_name = await ensure_yard(update, context)
    if yard_name is None:
        return

    yard = PARKING_YARDS[yard_name]
    slots = yard["slots"]
    blocks = yard["blocks"]

    text = update.message.text.strip()
    if text == "❌ Cancel":
        await update.message.reply_text("❌ Parking cancelled.", reply_markup=get_main_menu(user_id))
        return ConversationHandler.END

    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a valid number.", reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], one_time_keyboard=True, resize_keyboard=True))
        return 1

    slot = int(text)

    if slot not in blocks:
        await update.message.reply_text("❌ Invalid slot number for this yard.", reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], one_time_keyboard=True, resize_keyboard=True))
        return 1

    for s, info in slots.items():
        if info["user_id"] == user_id:
            await update.message.reply_text(f"❌ You are already parked in slot {s}. Please /leave first.", reply_markup=get_main_menu(user_id))
            return ConversationHandler.END

    if slot in slots:
        await update.message.reply_text("❌ That slot is already taken. Try another.", reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], one_time_keyboard=True, resize_keyboard=True))
        return 1

    slots[slot] = {
        "user_id": user_id,
        "name": name,
        "phone": phone,
        "time": datetime.now().isoformat(),
    }
    # ✅ Only schedule if it's a charging slot
    if slot in yard.get("charging_slots", []):
        reminder_time = datetime.now() + timedelta(hours=1, minutes=30)
        context.job_queue.run_once(
            send_charging_reminder,
            when=reminder_time,
            data={
                "user_id": user_id,
                "slot": slot,
                "yard": yard_name
            },
            name=f"reminder_{user_id}_{yard_name}"
        )

    await update.message.reply_text(f"✅ {name}, you parked in slot {slot} of {yard_name}.", reply_markup=get_main_menu(user_id))

    for blocked_slot in blocks.get(slot, []):
        blocked_info = slots.get(blocked_slot)
        if blocked_info:
            try:
                await context.bot.send_message(
                    chat_id=blocked_info["user_id"],
                    text=f"🚧 You're blocked by {name} in slot {slot}.\n📱 Phone: {phone}"
                )
                await update.message.reply_text(f"⚠️ You are blocking {blocked_info['name']} in slot {blocked_slot}.\n📱 Phone: {blocked_info.get('phone', 'No phone shared')}")
            except Exception as e:
                print(f"Could not notify {blocked_info['name']}: {e}")

    return ConversationHandler.END


# /leave func


async def leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.full_name
    yard_name = await ensure_yard(update, context)
    if yard_name is None:
        return

    yard = PARKING_YARDS[yard_name]
    slots = yard["slots"]
    blocks = yard["blocks"]

    for slot, info in list(slots.items()):
        if info["user_id"] == user_id:
            del slots[slot]
            await update.message.reply_text(f"👋 {name}, you’ve left slot {slot} in {yard_name}. It is now available.")
            for blocked_slot in blocks.get(slot, []):
                blocked_info = slots.get(blocked_slot)
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

application.add_handler(ConversationHandler(
    entry_points=[MessageHandler(
        filters.TEXT & filters.Regex("^🅿️ Park$"), ask_parking_slot)],
    states={
        1: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_parking_slot)]},
    fallbacks=[MessageHandler(
        filters.TEXT & filters.Regex("^❌ Cancel$"), leave)]
))
# Fallback handler for unrecognized commands


async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in USER_YARD:
        await update.message.reply_text(
            "⚠️ Please choose a yard first:",
            reply_markup=get_main_menu(user_id)
        )
        return

    # If they have a yard, show normal fallback
    await update.message.reply_text(
        "❓ I didn't understand that. Please choose an option from the menu.",
        reply_markup=get_main_menu(user_id)
    )

# Reset function


def reset_parking():
    for yard in PARKING_YARDS.values():
        yard["slots"].clear()
    USER_YARD.clear()
    print("🧹 Parking slots and yards have been reset.")


async def set_webhook():
    load_phones()
    bot = Bot(token=TOKEN)
    await bot.set_webhook(url=WEBHOOK_URL)
    await application.job_queue.start()
    # Setup daily job
    scheduler = AsyncIOScheduler()
    scheduler.add_job(reset_parking, CronTrigger(
        # Every day at 00:00
        hour=0, minute=0, timezone=timezone("Asia/Jerusalem")))
    scheduler.start()


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
