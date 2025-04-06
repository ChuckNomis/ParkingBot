from fastapi import FastAPI, Request
from bot import bot_app, set_webhook
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

app = FastAPI()

# Mount the Telegram bot FastAPI app
app.mount("/webhook", bot_app)


@app.on_event("startup")
async def startup():
    await set_webhook()


@app.get("/")
def read_root():
    return {"message": "Bot is running 🚗"}
