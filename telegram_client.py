import os
from telethon import TelegramClient
from config import Config

BASE_DIR = getattr(Config, 'BASE_DIR', os.path.dirname(os.path.abspath(__file__)))

if not Config.TELEGRAM_API_ID or not Config.TELEGRAM_API_HASH:
    raise ValueError("❌ TELEGRAM_API_ID e TELEGRAM_API_HASH precisam estar definidos no ficheiro .env!")

client = TelegramClient(
    os.path.join(BASE_DIR, "jellybot_session"),
    Config.TELEGRAM_API_ID,
    Config.TELEGRAM_API_HASH
)