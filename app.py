import os
import requests
from flask import Flask, request
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import anthropic
from notion_client import Client as NotionClient

load_dotenv()

app = Flask(__name__)

TELEGRAM_TOKEN          = os.environ["TELEGRAM_TOKEN"]
NOTION_TOKEN            = os.environ["NOTION_TOKEN"]
NOTION_DB_ID            = os.environ["NOTION_DB_ID"]
NOTION_TODO_DB_ID       = os.environ["NOTION_TODO_DB_ID"]
NOTION_REMINDERS_DB_ID  = os.environ["NOTION_REMINDERS_DB_ID"]
CHAT_ID                 = os.environ["TELEGRAM_CHAT_ID"]
INJECT_SECRET           = os.environ["INJECT_SECRET"]


# ── Existing: Insert task to Notion ───────────────────────────────────────────

def insert_notion(title: str) -> str:
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Item": {"title": [{"text": {"content": title}}]}
        }
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    return "✅ Added to Notion." if r.status_code == 200 else f"❌ Notion error: {r.status_code} — {r.json().get('message', 'unknown')}"


# ── Fetch Notion tasks + reminders ────────────────────────────────────────────

def get_notion_tasks() -> list[str]:
    notion = NotionClient(auth=NOTION_TOKEN)
    results = []

    try:
        todos = notion.databases.query(
            database_id=NOTION_TODO_DB_ID,
            filter={"property": "Done", "checkbox": {"equals": False}}
        ).get("results", [])

        for page in todos:
            props = page["properties"]
            title = props.get("Item", {}).get("title", [])
            name = title[0]["plain_text"] if title else "Untitled"
            results.append(f"[To-Do] {name}")

    except Exception as e:
        results.append(f"[To-Do] Error: {e}")

    try:
        reminders = notion.databases.query(
            database_id=NOTION_REMINDERS_DB_ID,
            filter={"property": "Checkbox", "checkbox": {"equals": False}}
        ).get("results", [])

        for page in reminders:
            props = page["properties"]
            title = props.get("Name", {}).get("title", [])
            name = title[0]["plain_text"] if title else "Untitled"
            category = props.get("Category", {}).get("select", {})
            cat_label = f" [{category.get('name', '')}]" if category else ""
            due = props.get("Due Date", {}).get("date", {})
            due_label = f" · Due {due.get('start', '')}" if due else ""
            results.append(f"[Reminder]{cat_label} {name}{due_label}")

    except Exception as e:
        results.append(f"[Reminder] Error: {e}")

    return results


# ── Generate brief ────────────────────────────────────────────────────────────

def generate_brief_from_data(events: list, tasks: list, emails: list) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""You are a sharp personal assistant for a busy business owner in Brunei managing multiple companies (engineering, construction, padel sports, automotive, agency).

Generate a concise, punchy morning brief. No fluff.

📅 TODAY'S CALENDAR:
{chr(10).join(events) if events else "No events provided."}

✅ OPEN TO-DOS & REMINDERS:
{chr(10).join(tasks) if tasks else "No open items."}

📧 IMPORTANT EMAILS:
{chr(10).join(emails) if emails else "No emails provided."}

Format with exactly 3 sections using these headers:
📅 *Today's Schedule*
✅ *Open Items*
📧 *Emails to Action*

Use bullet points. Flag anything urgent. End with one sharp, motivational closing line."""

    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


# ── Telegram sender ───────────────────────────────────────────────────────────

def send_message(chat_id, text: str):
    for i in range(0, len(text), 4000):
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[i:i+4000],
                "parse_mode": "Markdown"
            }
        )


# ── Fallback scheduler: 06:00 BNT, fires with Notion data only ───────────────

def scheduled_brief_fallback():
    tasks = get_notion_tasks()
    brief = generate_brief_from_data([], tasks, [])
    send_message(CHAT_ID, f"☀️ *Morning Brief*\n\n{brief}")

scheduler = BackgroundScheduler(timezone="Asia/Brunei")
scheduler.add_job(scheduled_brief_fallback, "cron", hour=6, minute=0)
scheduler.start()


# ── Inject endpoint: Cowork POSTs calendar + email data here ─────────────────

@app.route("/inject-brief", methods=["POST"])
def inject_brief():
    data = request.json

    if not data or data.get("secret") != INJECT_SECRET:
        return "unauthorized", 401

    events = data.get("calendar", [])
    emails = data.get("emails", [])
    tasks  = get_notion_tasks()

    brief = generate_brief_from_data(events, tasks, emails)
    send_message(CHAT_ID, f"☀️ *Morning Brief*\n\n{brief}")
    return "ok", 200


# ── Webhook: Telegram message handler ────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    message = data.get("message", {})
    text = message.get("text", "").strip()
    chat_id = message.get("chat", {}).get("id")

    if not text or not chat_id:
        return "ok", 200

    if text == "/brief":
        send_message(chat_id, "⏳ Generating your brief...")
        tasks = get_notion_tasks()
        brief = generate_brief_from_data([], tasks, [])
        send_message(chat_id, f"☀️ *Your Morning Brief*\n\n{brief}")
        return "ok", 200

    if text.startswith("/"):
        send_message(chat_id, "Send text to add to Notion, or /brief for your morning summary.")
        return "ok", 200

    tasks = [line.strip() for line in text.splitlines() if line.strip()]
    results = [insert_notion(t) for t in tasks]
    success = sum(1 for r in results if r.startswith("✅"))
    fail = len(results) - success

    if fail == 0:
        send_message(chat_id, f"✅ {success} task(s) added to Notion.")
    else:
        send_message(chat_id, f"✅ {success} added, ❌ {fail} failed.")

    return "ok", 200


# ── Health check ──────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return "Bot is running.", 200


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
