from fastapi import FastAPI
from bot import bot_app, set_webhook
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await set_webhook()  # runs on startup
    yield
    # Optional: cleanup logic here

app = FastAPI(lifespan=lifespan)
app.mount("/webhook", bot_app)


@app.get("/")
def read_root():
    return {"message": "Bot is running 🚗"}


@app.get("/health")
async def health_check():
    return {"ok": True}
