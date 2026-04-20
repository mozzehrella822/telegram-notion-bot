import os
import requests
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NOTION_TOKEN   = os.environ["NOTION_TOKEN"]
NOTION_DB_ID   = os.environ["NOTION_DB_ID"]


# ── Notion ────────────────────────────────────────────────────────────────────

def insert_notion(title: str) -> str:
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Item": {
                "title": [{"text": {"content": title}}]
            }
        }
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    if r.status_code == 200:
        return "✅ Added to Notion."
    else:
        return f"❌ Notion error: {r.status_code} — {r.json().get('message', 'unknown error')}"


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_message(chat_id: int, text: str):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text}
    )


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    # Ignore anything that isn't a plain text message
    message = data.get("message", {})
    text = message.get("text", "").strip()
    chat_id = message.get("chat", {}).get("id")

    if not text or not chat_id:
        return "ok", 200

    # Ignore bot commands
    if text.startswith("/"):
        send_message(chat_id, "Send me any text and I'll add it to Notion.")
        return "ok", 200

    result = insert_notion(text)
    send_message(chat_id, result)
    return "ok", 200


# ── Health check (Railway pings this) ─────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return "Bot is running.", 200


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
