"""Quick one-shot test: send a message via Telegram to verify token + chat_id."""
import os, httpx
from dotenv import load_dotenv

load_dotenv()

token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

if not token or not chat_id:
    print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing from .env")
else:
    print(f"Token  : ...{token[-6:]}")
    print(f"Chat ID: {chat_id}")
    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "👋 Dolev AI bot is alive and connected!"},
        timeout=10,
    )
    print(f"HTTP {r.status_code}")
    print(r.json())
