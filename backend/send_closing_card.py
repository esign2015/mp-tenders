"""Send only today's card-view PDF when explicitly requested by the admin."""
import json
import os
from pathlib import Path
import telegram_alerts as alerts
from telegram_card_pdf import make_card_pdf
from scheduled_telegram_delivery import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    token = alerts.clean(os.getenv('TELEGRAM_BOT_TOKEN'))
    chat = alerts.clean(os.getenv('TELEGRAM_CHAT_ID'))
    if not token or not chat:
        raise RuntimeError('Telegram credentials missing')
    receipt_path = ROOT / 'data/telegram_card_manual_last_send.json'
    previous = read_json(receipt_path)
    run_id = os.getenv('GITHUB_RUN_ID', '')
    if run_id and previous.get('run_id') == run_id and previous.get('message_id'):
        print('This requested card PDF was already confirmed by Telegram.')
        return 0
    now = alerts.datetime.now(alerts.IST)
    rows = alerts.morning_inventory_rows(alerts.live_rows(alerts.load_report_rows()), alerts.CSV_PATH.parent)
    selected = sorted([row for row in rows if alerts.is_on_date(row.get('Closing Date'), now.date())], key=alerts.closing_sort_key)
    date = now.strftime('%d-%m-%Y')
    if selected:
        pdf = make_card_pdf(selected, f'Closing Today {date} Card View.pdf', f'Closing Today {date}',
                            total_available=len(rows), filter_detail='Closing Today', filter_live=False)
        response = alerts.telegram_document(token, chat, pdf,
                    f'📎 आज Closing वाले {len(selected)} टेंडर | Card View + SAR Services | {date}')
    else:
        response = alerts.telegram_message(token, chat, 'ℹ️ आज Closing Today में कोई live tender नहीं है, इसलिए card-view PDF नहीं भेजी गई।')
    message_id = response.get('result', {}).get('message_id')
    if not message_id:
        raise RuntimeError('Telegram delivery confirmation missing')
    write_json(receipt_path, {'sent_at':alerts.datetime.now(alerts.IST).isoformat(), 'date':now.date().isoformat(),
               'report':'closing_today_card', 'count':len(selected), 'total_available':len(rows),
               'message_id':message_id, 'run_id':run_id, 'status':'success'})
    print(f'Telegram confirmed card-view PDF: {len(selected)} tenders, message_id={message_id}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
