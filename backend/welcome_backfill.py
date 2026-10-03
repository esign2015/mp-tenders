"""One requested welcome-button batch; contacts and receipts stay private."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import signup_alerts

CAMPAIGN = 'welcome-2026-10-03'
CUTOFF = '2026-10-03T11:37:07+05:30'
OUTBOX = 'welcome_buttons_2026_10_03'
EXPECTED_TOTAL = 33
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='welcome-batch')
_lock = threading.Lock()
_job = None
_status = {}


def before_cutoff(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=signup_alerts.IST)
        return stamp <= datetime.fromisoformat(CUTOFF)
    except (ValueError, TypeError):
        return False


def send_records(accounts, mobiles, progress, sender=None, pause=time.sleep):
    sender = sender or (lambda record: signup_alerts.send_signup_alert(record, welcome_batch=True))
    counts = {'total': len(mobiles), 'sent': 0, 'failed': 0, 'skipped': 0}
    progress(counts)
    for mobile in mobiles:
        try:
            record = accounts.get(mobile=mobile)
            if not record or not before_cutoff(record.get('signup_at')):
                counts['skipped'] += 1
                progress(counts)
                continue
            for _ in range(4):
                if record.get(OUTBOX):
                    break
                revision = record['revision']
                record[OUTBOX] = {'state': 'pending'}
                if accounts.update(record, revision):
                    break
                record = accounts.get(user_id=record['user_id'])
            if (record.get(OUTBOX) or {}).get('state') != 'sent':
                signup_alerts.deliver(accounts, record['user_id'], sender=sender, pause=pause, outbox=OUTBOX)
                pause(1.1)  # One private chat; respect its message rate limit.
            receipt = accounts.get(user_id=record['user_id']).get(OUTBOX) or {}
            counts['sent' if receipt.get('state') == 'sent' and receipt.get('message_id') else 'failed'] += 1
        except Exception:
            counts['failed'] += 1
        progress(counts)
    return counts


def run(server):
    def progress(counts):
        with _lock:
            _status.update(counts)
    try:
        if server.sheet_store.enabled():
            rows = server.sheet_store.call('list_visitors')['visitors']
        else:
            conn = server.visitor_db()
            try:
                rows = [dict(row) for row in conn.execute('SELECT mobile,signup_at FROM visitor_registrations').fetchall()]
            finally:
                conn.close()
        mobiles = sorted({server.normalise_mobile(row.get('mobile')) for row in rows
                          if before_cutoff(row.get('signup_at'))} - {''})
        if len(mobiles) != EXPECTED_TOTAL:
            raise ValueError('Registered contact count does not match the approved snapshot')
        counts = send_records(server.account_service, mobiles, progress)
        with _lock:
            _status['state'] = 'complete' if counts['failed'] == 0 and counts['skipped'] == 0 else 'incomplete'
    except Exception as exc:
        with _lock:
            _status.update(state='incomplete', error_type=type(exc).__name__)


def start(server, retry=False):
    global _job
    with _lock:
        if _job is None or (_job.done() and retry and _status.get('state') != 'complete'):
            _status.clear()
            _status.update(state='running', total=0, sent=0, failed=0, skipped=0)
            _job = _pool.submit(run, server)
        return dict(_status)
