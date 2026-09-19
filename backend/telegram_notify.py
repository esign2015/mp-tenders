import csv
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request, parse

IST = timezone(timedelta(hours=5, minutes=30))
SITE_URL = "https://tenders.codinglms.xyz/"
CSV_PATH = Path("organisation_tenders.csv")


def clean(value):
    return str(value or "").strip()


def published_today(value):
    text = clean(value)
    if not text:
        return False

    formats = [
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %I:%M %p",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.date() == datetime.now(IST).date()
        except ValueError:
            pass

    # Last fallback: look for a DD/MM/YYYY date inside the value.
    match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if match:
        try:
            return datetime(
                int(match.group(3)),
                int(match.group(2)),
                int(match.group(1)),
            ).date() == datetime.now(IST).date()
        except ValueError:
            return False

    return False


def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "false",
    }).encode("utf-8")

    with request.urlopen(request.Request(url, data=data), timeout=30) as response:
        body = response.read().decode("utf-8")
        if '"ok":true' not in body:
            raise RuntimeError(f"Telegram sendMessage failed: {body}")


def main():
    token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = clean(os.getenv("TELEGRAM_CHAT_ID"))

    if not token or not chat_id:
        print("Telegram secrets are not configured; notification skipped.")
        return 0

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"{CSV_PATH} not found")

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    today_rows = [
        row for row in rows
        if published_today(row.get("Published Date", ""))
    ]

    today = datetime.now(IST).strftime("%d/%m/%Y")

    message = (
        "🔔 MP Tender Daily Update\n\n"
        f"📅 Date: {today}\n"
        f"🆕 New tenders published today: {len(today_rows)}\n"
        f"📋 Total tender-list records: {len(rows)}\n\n"
        f"🌐 Dashboard: {SITE_URL}\n👤 Contact Admin: https://t.me/rdgyan\n\n"
        "⚠️ This dashboard is an assistance tool only. "
        "Always verify the final tender notice, corrigendum, eligibility, "
        "fees and deadline on the official tender portal."
    )

    send_message(token, chat_id, message)
    print(f"Telegram notification sent successfully. New today: {len(today_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
