"""Refresh private admin counts without needing an open admin browser."""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request, error

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent.parent
RECEIPT = ROOT / 'data/admin_stats_schedule.json'
TRIGGER = ROOT / 'data/admin_stats.trigger'
API = 'https://mp-tenders-api.onrender.com/api/internal/admin-stats-refresh'


def selected_slot(now, event, cron=''):
    if event == 'schedule':
        return {'30 1 * * *': '07:00', '0 12 * * *': '17:30'}[cron]
    if event == 'push':
        trigger = json.loads(TRIGGER.read_text())
        if trigger['date_ist'] != now.date().isoformat():
            raise ValueError('Stats trigger is not for today.')
        return trigger['slot']
    return '17:30' if now.strftime('%H:%M') >= '17:30' else '07:00'


def signed_request(secret, payload, timestamp):
    raw = json.dumps(payload, separators=(',', ':')).encode()
    signature = hmac.new(secret.encode(), b'mp-admin-stats-refresh\n' + str(timestamp).encode() + b'\n' + raw, hashlib.sha256).hexdigest()
    return request.Request(API, data=raw, method='POST', headers={
        'Content-Type': 'application/json', 'X-Stats-Timestamp': str(timestamp), 'X-Stats-Signature': signature})


def main():
    now = datetime.now(IST)
    day = now.date().isoformat()
    slot = selected_slot(now, os.getenv('STATS_EVENT', 'workflow_dispatch'), os.getenv('STATS_CRON', ''))
    if slot not in ('07:00', '17:30') or slot > now.strftime('%H:%M'):
        raise ValueError('Invalid or future stats slot.')
    ledger = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {'completed': {}}
    ledger['completed'] = {key: value for key, value in ledger.get('completed', {}).items() if key.startswith(day + '@')}
    key = day + '@' + slot
    if key in ledger['completed']:
        print('Admin counts already updated for this slot.')
        return
    secret = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not secret:
        raise RuntimeError('Scheduled stats secret is not configured.')
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            with request.urlopen(signed_request(secret, {'date_ist': day, 'slot': slot}, int(time.time())), timeout=30) as response:
                result = json.load(response)
        except error.HTTPError as exc:
            # Never echo credentials or a private server response to CI logs.
            if exc.code < 500:
                raise RuntimeError('Admin stats request rejected: HTTP ' + str(exc.code)) from None
            time.sleep(5)
            continue
        except (error.URLError, TimeoutError):
            time.sleep(5)
            continue
        if result.get('ok') and result.get('completed'):
            ledger['completed'][key] = {'updated_at': result['updated_at']}
            RECEIPT.parent.mkdir(parents=True, exist_ok=True)
            RECEIPT.write_text(json.dumps(ledger, indent=2) + '\n')
            print('Admin counts update confirmed for ' + key + ' IST.')
            return
        if not result.get('ok') or not result.get('pending'):
            raise RuntimeError('Admin counts update was not confirmed.')
        time.sleep(3)
    raise RuntimeError('Admin counts update did not finish in time.')


if __name__ == '__main__':
    main()
