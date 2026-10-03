"""Start/poll the requested private batch. Never put contacts in CI or Git."""
import hashlib
import hmac
import json
import os
import time
from urllib import request, error

from welcome_backfill import CAMPAIGN, CUTOFF, EXPECTED_TOTAL

API = 'https://mp-tenders-api.onrender.com/api/internal/welcome-backfill'


def main():
    secret = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not secret:
        raise RuntimeError('Bot configuration missing')
    deadline = time.monotonic() + 1200
    retried = False
    while time.monotonic() < deadline:
        raw = json.dumps({'campaign': CAMPAIGN, 'cutoff': CUTOFF, 'retry': retried}, separators=(',', ':')).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), b'mp-welcome-backfill\n'+stamp.encode()+b'\n'+raw, hashlib.sha256).hexdigest()
        req = request.Request(API, data=raw, headers={'Content-Type':'application/json',
                              'X-Alert-Timestamp':stamp, 'X-Alert-Signature':signature})
        try:
            with request.urlopen(req, timeout=60) as response:
                result = json.load(response)
        except error.HTTPError as exc:
            if exc.code == 404:
                raise RuntimeError('Backend deployment required before sending welcome buttons') from None
            if exc.code in (401, 403):
                raise RuntimeError('Backend job authentication was not accepted') from None
            raise RuntimeError('Private welcome batch is not ready: HTTP '+str(exc.code)) from None
        except (error.URLError, TimeoutError):
            time.sleep(10)
            continue
        if result.get('state') == 'complete':
            if result.get('sent') != EXPECTED_TOTAL or result.get('failed') or result.get('skipped'):
                raise RuntimeError('Not all requested welcome buttons have confirmed receipts')
            print('Telegram confirmed private welcome buttons for all '+str(EXPECTED_TOTAL)+' registered users.')
            return
        if result.get('state') == 'incomplete':
            if retried:
                raise RuntimeError('Welcome batch still has unconfirmed deliveries')
            time.sleep(60)
            retried = True
        else:
            time.sleep(10)
    raise RuntimeError('Welcome batch confirmation timed out')


if __name__ == '__main__':
    main()
