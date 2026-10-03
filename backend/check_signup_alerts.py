"""Confirm the deployed private signup integration without disclosing user data."""
import hashlib
import hmac
import json
import os
import time
from urllib import request, error

API = 'https://mp-tenders-api.onrender.com/api/internal/signup-alert-status'


def main():
    secret = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not secret:
        raise RuntimeError('Bot configuration is missing')
    deadline = time.monotonic() + 360
    last_status = 'not_reachable'
    while time.monotonic() < deadline:
        raw = b'{}'
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), b'mp-signup-alert-status\n' + stamp.encode() + b'\n' + raw, hashlib.sha256).hexdigest()
        req = request.Request(API, data=raw, headers={'Content-Type': 'application/json',
                              'X-Alert-Timestamp': stamp, 'X-Alert-Signature': signature})
        try:
            with request.urlopen(req, timeout=30) as response:
                result = json.load(response)
            if result.get('ok') and result.get('private_chat_verified') and result.get('bot_username') == 'mptenders_bot':
                print('Live signup alerts confirmed: @mptenders_bot, verified private admin chat, WhatsApp welcome link.')
                return
            last_status = 'not_ready'
        except error.HTTPError as exc:
            last_status = 'HTTP ' + str(exc.code)
            if exc.code == 401:
                raise RuntimeError('Backend and job bot configuration do not match') from None
            if exc.code == 503:
                try:
                    status = json.load(exc).get('status', '')
                    if status in ('disabled', 'bot_or_private_chat_not_ready'):
                        last_status = status
                except (ValueError, AttributeError):
                    pass
        except (error.URLError, TimeoutError, ValueError):
            last_status = 'not_reachable'
        time.sleep(10)
    raise RuntimeError('Live signup alert check pending: ' + last_status)


if __name__ == '__main__':
    main()
