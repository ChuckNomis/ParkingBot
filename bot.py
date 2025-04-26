# bot.py – Telegram Parking‑Yard Bot
# -------------------------------------------------
# A FastAPI + python‑telegram‑bot application that lets
# users reserve parking slots in one of several yards, share
# their phone number, and (optionally) get reminders when
# charging‑only slots are occupied too long.
# -------------------------------------------------


# ── Standard Library ────────────────────────────────────────────────────────────
import json
import os
import tempfile
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock


# ── 3rd‑party ──────────────────────────────────────────────────────────────────
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import APIRouter
from pytz import timezone
from telegram import (
    Bot,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ── Environment / Globals ──────────────────────────────────────────────────────
load_dotenv()                                          # read .env file

TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_HOST: str = os.getenv("WEBHOOK_URL", "")  # without /webhook suffix
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = WEBHOOK_HOST + WEBHOOK_PATH
_JSON_LOCK = Lock()  # protect concurrent writes
PHONES_FILE = "user_phones.json"   # persisted phone numbers
ALLOW_FILE = "allowed_phones.json"
# Telegram user‑IDs allowed to run /reset_all and /addphone <number>
ADMIN_IDS = {1997945569}

# ── Yard / slot configuration ──────────────────────────────────────────────────
PARKING_YARDS: dict[str, dict] = {
    "Hamasger50": {
        "slots": {},                                 # runtime: {slot:int: info_dict}
        "blocks": {                                 # which slots block which others
            1: [], 2: [1], 3: [], 4: [3], 5: [], 6: [5], 7: [], 8: [7],
            9: [], 10: [9], 11: [10, 9], 12: [], 13: [12], 14: [], 15: [],
            16: [], 17: [], 18: [], 19: [], 20: [], 21: [], 22: [23, 24],
            23: [24], 24: [], 25: [26], 26: [], 27: [28], 28: [], 29: [30],
            30: [], 31: [],
        },
        "charging_slots": [],                        # specify charging‑only slots
    },
    "BeitNip": {
        "slots": {},
        "blocks": {1: [], 2: []},
        "charging_slots": [1, 2],
    },
}

# These dicts are populated at runtime
USER_PHONES: dict[int, str] = {}   # telegram_id -> phone
USER_YARD: dict[int, str] = {}     # telegram_id -> chosen yard name
ALLOWED_PHONES: set[str] = set()  # phone strings loaded from JSON

# ── Telegram Application ───────────────────────────────────────────────────────
application = (
    Application.builder()
    .token(TOKEN)
    # make sure job_queue knows about the Application instance
    .post_init(lambda app: app.job_queue.set_application(app))
    .build()
)

# ── JSON helpers (atomic write) ─────────────────────────────────────────────


def _read_json(path: str | Path, default):
    """
    Read JSON from *path* (str or Path).
    Returns *default* on FileNotFoundError or JSONDecodeError.
    """
    path = Path(path)             # accept either a str or Path
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _atomic_write(path: str | Path, data):
    """
    Atomically write *data* as JSON to *path* (str or Path).
    Uses a temp file + replace so readers never see a half‐written file.
    """
    path = Path(path)             # accept either a str or Path
    with _JSON_LOCK:              # prevent concurrent writers
        tmp_dir = path.parent
        with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=tmp_dir, encoding="utf-8"
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
        Path(tmp.name).replace(path)

# ── Persistent load / save ─────────────────────────────────────────────────


def load_persistent():
    global USER_PHONES, ALLOWED_PHONES
    USER_PHONES = {int(k): v for k, v in _read_json(PHONES_FILE, {}).items()}
    ALLOWED_PHONES = set(_read_json(ALLOW_FILE, []))
    print("✅ phones", USER_PHONES)
    print("✅ allow-list", ALLOWED_PHONES)


def save_phones():
    _atomic_write(PHONES_FILE, USER_PHONES)


def save_allow():
    _atomic_write(ALLOW_FILE, list(ALLOWED_PHONES))

# ── HELPERS : AUTHORISATION & MENUS ─────────────────────────────────────────


def main_menu(user_id: int | None = None) -> ReplyKeyboardMarkup:
    if user_id not in USER_PHONES:                         # must share first
        keyboard = [[KeyboardButton("📱 Share Phone", request_contact=True)]]
    elif USER_PHONES[user_id] not in ALLOWED_PHONES:       # shared but not allowed
        keyboard = [[]]   # empty keyboard
    elif user_id is None or user_id not in USER_YARD:
        keyboard = [["🏢 Choose Yard"]]
    else:                                                  # fully authorised
        keyboard = [["🅿️ Park", "🚶 Leave"], ["📋 Status"], ["🏢 Choose Yard"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def authorised(update: Update) -> bool:
    u = update.effective_user
    phone = USER_PHONES.get(u.id)
    if u.id in ADMIN_IDS or (phone and phone in ALLOWED_PHONES):
        return True
    await update.message.reply_text("⛔ Private bot – ask admin for access.")
    return False


async def ensure_yard(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Ask user to pick a yard if they haven’t yet; return yard name or None."""
    uid = update.effective_user.id
    if uid not in USER_YARD:
        await update.message.reply_text("⚠️ Please choose a yard first.", reply_markup=main_menu(uid))
        return None
    return USER_YARD[uid]

# ─────────────────────────────── Handlers ─────────────────────────────────────


# ADMIN COMMANDS
async def _normalise(raw: str) -> str:
    """Israel local digits→ +972… ; keep international numbers unchanged."""
    return raw if raw.startswith("+") else f"+972{raw.lstrip('0')}"


async def add_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("Usage: /addphone <digits>")
        return
    phone = await _normalise(context.args[0])
    if phone in ALLOWED_PHONES:
        await update.message.reply_text("ℹ️ Already in allow‑list.")
        return
    ALLOWED_PHONES.add(phone)
    save_allow()
    await update.message.reply_text(f"✅ {phone} added.")


async def del_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("Usage: /delphone <digits>")
        return
    phone = await _normalise(context.args[0])
    if phone not in ALLOWED_PHONES:
        await update.message.reply_text("ℹ️ Not found in allow‑list.")
        return
    ALLOWED_PHONES.remove(phone)
    save_allow()
    await update.message.reply_text(f"🗑️ {phone} removed from allow‑list.")


async def list_phones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if ALLOWED_PHONES:
        text = "\n".join(sorted(ALLOWED_PHONES))
    else:
        text = "(empty)"
    await update.message.reply_text(f"📄 Allowed phones:\n{text}")


async def reset_all_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    for yard in PARKING_YARDS.values():
        yard["slots"].clear()
    await update.message.reply_text("🧹 All yards reset.")

# register admin handlers
action_admins = [
    ("addphone", add_phone),
    ("delphone", del_phone),
    ("listphones", list_phones),
    ("reset_all_slots", reset_all_slots),
]
for cmd, fn in action_admins:
    application.add_handler(CommandHandler(cmd, fn))


# /start -------------------------------------------------------------------


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Choose an option below:", reply_markup=main_menu(update.effective_user.id))

application.add_handler(CommandHandler("start", start))

# Yard‑selection conversation -------------------------------------------------------------------
SELECT_YARD = 3


async def choose_yard(update: Update, _ctx):
    yards = [[y] for y in PARKING_YARDS] + [["❌ Cancel"]]
    await update.message.reply_text(
        "🏢 Choose a parking yard:",
        reply_markup=ReplyKeyboardMarkup(
            yards,
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    return SELECT_YARD


async def set_yard(update: Update, _ctx):
    uid = update.effective_user.id
    chosen = update.message.text.strip()
    if chosen in PARKING_YARDS:
        USER_YARD[uid] = chosen
        await update.message.reply_text(f"✅ You’re now using *{chosen}*.", parse_mode="Markdown", reply_markup=main_menu(uid))
    else:
        await update.message.reply_text("❌ Invalid yard.", reply_markup=main_menu(uid))
    return ConversationHandler.END

application.add_handler(
    ConversationHandler(
        entry_points=[
            MessageHandler(         # user taps the button
                choose_yard,
            )
        ],
        states={
            SELECT_YARD: [
                MessageHandler(~filters.COMMAND, set_yard)
            ]
        },
        fallbacks=[
            MessageHandler(filters.Regex(r"^❌ Cancel$"), set_yard)
        ],
    )
)


# /Status command -------------------------------------------------------------------


async def status(update: Update, ctx):
    uid = update.effective_user.id
    yard_name = await ensure_yard(update, ctx)
    if yard_name is None:
        return

    yard = PARKING_YARDS[yard_name]
    total_slots = sorted(yard["blocks"])
    taken_slots = sorted(yard["slots"])
    free_slots = [s for s in total_slots if s not in taken_slots]

    now = datetime.now()
    lines: list[str] = []
    for s in taken_slots:
        info = yard["slots"][s]
        prefix = "⚡ " if s in yard["charging_slots"] else ""
        t_str = ""
        if s in yard["charging_slots"]:
            minutes = int(
                (now - datetime.fromisoformat(info["time"])).total_seconds() // 60)
            t_str = f" ({minutes//60}h {minutes % 60}m)"
        lines.append(f"{prefix}{s} - {info['name']}{t_str}")

    taken_txt = "\n".join(lines) or "None"
    free_txt = ", ".join(map(str, free_slots)) or "None"

    msg = (f"📋 *{yard_name} Parking Status:*\n\n"
           f"🟢 Available slots: {free_txt}\n\n"
           f"🔴 Taken slots:\n{taken_txt}")
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_menu(uid))

application.add_handler(CommandHandler("status", status))
application.add_handler(MessageHandler(filters.Regex("^📋 Status$"), status))

# Phone‑sharing conversation -------------------------------------------------------------------
SHARE_PHONE = 2


async def ask_for_phone(update: Update, _ctx):
    uid = update.effective_user.id
    if uid in USER_PHONES:
        await update.message.reply_text("✅ Your phone number is already saved!", reply_markup=main_menu(uid))
        return ConversationHandler.END
    kb = [[KeyboardButton("📱 Share my phone", request_contact=True)], [
        "❌ Cancel"]]
    await update.message.reply_text("📱 Tap the button to share your phone:", reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True))
    return SHARE_PHONE


async def receive_phone(update: Update, _ctx):
    if not update.message.contact:
        return ConversationHandler.END
    uid = update.message.contact.user_id
    USER_PHONES[uid] = update.message.contact.phone_number
    save_phones()
    await update.message.reply_text("✅ Phone saved!", reply_markup=main_menu(uid))
    return ConversationHandler.END

application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("sharephone", ask_for_phone), MessageHandler(
        filters.Regex("^📱 Share Phone$"), ask_for_phone)],
    states={SHARE_PHONE: [MessageHandler(filters.CONTACT, receive_phone)]},
    fallbacks=[MessageHandler(filters.Regex("^❌ Cancel$"), receive_phone)],
))

# Parking workflow ----------------------------------------------------------------
PARKING_INPUT = 1


async def ask_parking_slot(update: Update, ctx):
    if await ensure_yard(update, ctx) is None:
        return
    await update.message.reply_text("📝 Enter parking slot #:", reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], one_time_keyboard=True))
    return PARKING_INPUT


async def send_charging_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    """Job‑queue callback: remind user only if still occupying the slot."""
    data = ctx.job.data  # {'user_id', 'slot', 'yard'}
    yard = PARKING_YARDS.get(data["yard"])
    current = yard and yard["slots"].get(data["slot"])
    if not current or current["user_id"] != data["user_id"]:
        return  # user moved / slot is free – do nothing
    await ctx.bot.send_message(data["user_id"],
                               f"⚡ Reminder: You've been in charging slot {data['slot']} ({data['yard']}) for 1.5 h. Please free it if you're done.")


async def handle_parking_slot(update: Update, ctx):
    uid = update.effective_user.id
    yard_name = await ensure_yard(update, ctx)
    if yard_name is None:
        return

    txt = update.message.text.strip()
    if txt == "❌ Cancel":
        await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu(uid))
        return ConversationHandler.END
    if not txt.isdigit():
        await update.message.reply_text("❌ Please enter a number.")
        return PARKING_INPUT

    slot = int(txt)
    yard = PARKING_YARDS[yard_name]
    if slot not in yard["blocks"]:
        await update.message.reply_text("❌ Invalid slot for this yard.")
        return PARKING_INPUT
    if slot in yard["slots"]:
        await update.message.reply_text("❌ Slot taken, choose another.")
        return PARKING_INPUT

    # park user
    yard["slots"][slot] = {
        "user_id": uid,
        "name": update.effective_user.full_name,
        "phone": USER_PHONES.get(uid, "unknown"),
        "time": datetime.now().isoformat(),
    }
    await update.message.reply_text(f"✅ Parked in slot {slot}.", reply_markup=main_menu(uid))

    # charging reminder
    if slot in yard["charging_slots"]:
        ctx.job_queue.run_once(send_charging_reminder, when=datetime.now(
        ) + timedelta(hours=1, minutes=30), data={"user_id": uid, "slot": slot, "yard": yard_name})

    # notify blocked slots
    for blocked in yard["blocks"].get(slot, []):
        info = yard["slots"].get(blocked)
        if info:
            with suppress(Exception):
                await ctx.bot.send_message(info["user_id"], f"🚧 You're blocked by {update.effective_user.full_name} (slot {slot}).")

    return ConversationHandler.END

application.add_handler(ConversationHandler(
    entry_points=[MessageHandler(
        filters.Regex("^🅿️ Park$"), ask_parking_slot)],
    states={PARKING_INPUT: [MessageHandler(
        ~filters.COMMAND, handle_parking_slot)]},
    fallbacks=[MessageHandler(filters.Regex(
        "^❌ Cancel$"), handle_parking_slot)],
))

# 6. /Leave -------------------------------------------------------------------


async def leave(update: Update, ctx):
    uid = update.effective_user.id
    yard_name = await ensure_yard(update, ctx)
    if yard_name is None:
        return
    yard = PARKING_YARDS[yard_name]
    for slot, info in list(yard["slots"].items()):
        if info["user_id"] == uid:
            del yard["slots"][slot]
            await update.message.reply_text(f"👋 You left slot {slot}.", reply_markup=main_menu(uid))
            # inform people who were blocked by that slot
            for b in yard["blocks"].get(slot, []):
                blk_info = yard["slots"].get(b)
                if blk_info:
                    with suppress(Exception):
                        await ctx.bot.send_message(blk_info["user_id"], f"🚧 Slot {slot} is now free.")
            return
    await update.message.reply_text("❌ You are not parked.")

application.add_handler(CommandHandler("leave", leave))
application.add_handler(MessageHandler(filters.Regex("^🚶 Leave$"), leave))
application.add_handler(
    MessageHandler(filters.CONTACT, receive_phone)
)
# Fallback -------------------------------------------------------------------


async def fallback(update: Update, _):
    await update.message.reply_text("❓ I didn't understand. Use the menu.", reply_markup=main_menu(update.effective_user.id))

application.add_handler(MessageHandler(~filters.COMMAND, fallback))

# ── Scheduled reset at midnight ───────────────────────────────────────────────


def reset_parking():
    for yard in PARKING_YARDS.values():
        yard["slots"].clear()
    USER_YARD.clear()
    print("🧹 Daily reset complete")

# ── Webhook setup & FastAPI bridge ────────────────────────────────────────────


async def set_webhook():
    load_persistent()             # reload phones & allow-list
    bot = Bot(token=TOKEN)
    await application.initialize()
    await bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook set to: {WEBHOOK_URL}")
    await application.job_queue.start()
    # Daily midnight reset
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        reset_parking,
        CronTrigger(hour=0, minute=0, timezone=timezone("Asia/Jerusalem"))
    )
    scheduler.start()
router = APIRouter()


@router.post(WEBHOOK_PATH)
async def telegram_webhook(update: dict):
    await application.process_update(Update.de_json(update, bot=application.bot))

bot_app = router
