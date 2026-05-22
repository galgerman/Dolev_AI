"""Fetch your chat_id by reading the latest update from the bot."""
import os, httpx
from dotenv import load_dotenv

load_dotenv()
token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Check for webhook (webhooks block getUpdates)
wh = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=10).json()
webhook_url = wh.get("result", {}).get("url", "")
if webhook_url:
    print(f"Webhook is set: {webhook_url}")
    print("Clearing it so getUpdates works...")
    httpx.post(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
    print("Webhook cleared.")

# Now fetch updates
r = httpx.get(
    f"https://api.telegram.org/bot{token}/getUpdates",
    timeout=10,
)
data = r.json()
updates = data.get("result", [])

if not updates:
    print("No messages found. Send ANY message to your bot in Telegram first, then re-run this.")
else:
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat:
            print(f"\nChat ID : {chat['id']}")
            print(f"Type    : {chat['type']}")
            print(f"Name    : {chat.get('first_name','') or chat.get('title','')}")
            print(f"\nAdd this to your .env:")
            print(f"TELEGRAM_CHAT_ID={chat['id']}")
            break
