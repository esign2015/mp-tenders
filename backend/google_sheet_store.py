"""Private Google Sheet storage through an authenticated Apps Script web app.

The Sheet ID is public configuration; credentials stay exclusively on servers.
Never silently fall back to local storage after a configured remote save fails.
"""
import hashlib
import hmac
import json
import os
import re
import time
import uuid

import requests

SHEET_ID = '1VHILTCBB-CR0srqOTmaxf0b17wWJCpaOuMpVp_KphKw'


class SheetStoreError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def enabled():
    # Partial configuration is an error, not permission to lose data in SQLite.
    return bool(os.getenv('GOOGLE_SHEETS_WEBAPP_URL') or os.getenv('GOOGLE_SHEETS_SHARED_SECRET'))


def call(action, **fields):
    url = os.getenv('GOOGLE_SHEETS_WEBAPP_URL', '').strip()
    secret = os.getenv('GOOGLE_SHEETS_SHARED_SECRET', '').strip()
    if not re.fullmatch(r'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec', url) or len(secret) < 32:
        raise SheetStoreError('Google Sheet connection is not configured correctly.')
    payload = json.dumps({'action': action, 'spreadsheet_id': SHEET_ID,
                          'request_id': str(uuid.uuid4()), **fields}, separators=(',', ':'), ensure_ascii=False)
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), (timestamp+'\n'+payload).encode(), hashlib.sha256).hexdigest()
    envelope = {'timestamp': timestamp, 'payload': payload, 'signature': signature}
    for attempt in range(2):
        try:
            response = requests.post(url, json=envelope, timeout=(5, 20))
            if not response.ok:
                raise SheetStoreError('Google Sheet service is unavailable. Please retry.')
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('Invalid response')
            if not result.get('ok'):
                status = result.get('status', 503)
                raise SheetStoreError(result.get('message', 'Google Sheet save failed.'), status if status in (400, 401, 404, 409, 503) else 503)
            return result
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 0:
                continue  # same request_id: remote visit/save is idempotent
            raise SheetStoreError('Google Sheet did not confirm the save. Please retry.') from None
        except (ValueError, requests.RequestException):
            raise SheetStoreError('Google Sheet returned an invalid response. Please retry.') from None

