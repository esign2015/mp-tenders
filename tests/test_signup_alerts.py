import copy
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import signup_alerts as alerts
import server


class Records:
    def __init__(self):
        self.record = {'user_id': 'test-user', 'revision': 1, 'name': 'Bidder नाम', 'mobile': '+919876543210',
                       'district': 'Dewas', 'tehsil': 'Kannod', 'signup_at': '2026-10-03T05:00:00+00:00',
                       'password_hash': 'private-password-hash', 'sessions': [{'hash': 'private-session'}],
                       'signup_alert': {'state': 'pending'}}

    def get(self, **kwargs):
        return copy.deepcopy(self.record)

    def update(self, record, revision):
        if revision != self.record['revision']:
            return False
        record['revision'] = revision + 1
        self.record = copy.deepcopy(record)
        return True


class SignupAlertsTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'TELEGRAM_ADMIN_CHAT_ID': '', 'SIGNUP_ALERTS_ENABLED': '1'})
        self.env.start()
        self.records = Records()
        self.calls = []

    def tearDown(self):
        self.env.stop()

    def telegram(self, method, **params):
        self.calls.append((method, params))
        if method == 'getMe':
            return {'is_bot': True, 'username': 'mptenders_bot'}
        if method == 'getChatAdministrators':
            return [{'user': {'id': 123, 'username': 'rdgyan', 'is_bot': False}}]
        if method == 'getChat':
            return {'id': 123, 'type': 'private', 'username': 'rdgyan'}
        if method == 'sendMessage':
            return {'message_id': 456, 'chat': {'id': 123, 'type': 'private'}}
        raise AssertionError(method)

    def test_welcome_button_roundtrips_complete_approved_hindi_message(self):
        record = self.records.record
        url = urlsplit(alerts.whatsapp_link(record))
        self.assertEqual(url.scheme, 'https')
        self.assertEqual(url.netloc, 'wa.me')
        self.assertEqual(url.path, '/919876543210')
        draft = parse_qs(url.query)['text'][0]
        self.assertEqual(draft, alerts.welcome_message(record['name'], record['mobile']))
        for value in ('नमस्कार Bidder नाम जी', 'MP Tender Live Dashboard', 'https://tenders.codinglms.xyz/',
                      'https://t.me/mptendersalert', 'https://chat.whatsapp.com/BuKI6bxZGHVBIHA6KZt7Vy', 'https://t.me/rdgyan'):
            self.assertIn(value, draft)
        self.assertIn('एक्टिव टेंडर', draft)
        self.assertNotIn('चालू टेंडर', draft)
        self.assertIn('मोबाइल नंबर *' + record['mobile'] + '* से पंजीकरण मिला है', draft)
        self.assertIn('यदि आपने यह पंजीकरण नहीं किया है या आपके नाम में कोई गलती है', draft)
        self.assertIn('पंजीकरण रिकॉर्ड हटा सकें', draft)
        self.assertNotIn('private-password', draft)
        for number in ('9876543210', '+91foo', '-100123', '@mptendersalert'):
            with self.assertRaises(ValueError):
                alerts.whatsapp_link({**record, 'mobile': number})

    def test_sends_only_private_owner_with_whitelisted_details_and_draft_button(self):
        self.assertEqual(alerts.send_signup_alert(self.records.record, self.telegram), 456)
        sends = [params for method, params in self.calls if method == 'sendMessage']
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]['chat_id'], 123)
        self.assertIn('समय: 03/10/2026 10:30:00 AM IST', sends[0]['text'])
        self.assertIn('तहसील: Kannod', sends[0]['text'])
        button = sends[0]['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['url'], alerts.whatsapp_link(self.records.record))
        self.assertEqual(button['text'], '🟢 WhatsApp पर स्वागत भेजें')
        payload = json.dumps(sends[0], ensure_ascii=False)
        for private in ('private-password-hash', 'private-session', 'password_hash', 'sessions'):
            self.assertNotIn(private, payload)

    def test_rejects_wrong_bot_channel_destination_and_wrong_owner(self):
        for target, replacement in [('getMe', {'is_bot': True, 'username': 'other_bot'}),
                                    ('getChat', {'id': 123, 'type': 'channel', 'username': 'rdgyan'}),
                                    ('getChat', {'id': 123, 'type': 'private', 'username': 'someone_else'})]:
            self.calls.clear()
            def call(method, **params):
                return replacement if method == target else self.telegram(method, **params)
            with self.subTest(target=target, replacement=replacement), self.assertRaises(ValueError):
                alerts.send_signup_alert(self.records.record, call)
            self.assertFalse(any(method == 'sendMessage' for method, _ in self.calls))
        with self.assertRaises(ValueError):
            alerts.private_destination(self.telegram, '-100123')

    def test_sent_outbox_prevents_repeat_and_preserves_concurrent_profile_changes(self):
        sends = []
        def sender(record):
            sends.append(record['user_id'])
            # Simulate another device saving a profile while Telegram sends.
            self.records.record.update(district='Bhopal', revision=self.records.record['revision']+1)
            return 456
        alerts.deliver(self.records, 'test-user', sender=sender)
        alerts.deliver(self.records, 'test-user', sender=sender)
        self.assertEqual(sends, ['test-user'])
        self.assertEqual(self.records.record['district'], 'Bhopal')
        self.assertEqual(self.records.record['signup_alert']['state'], 'sent')
        self.assertEqual(self.records.record['signup_alert']['message_id'], 456)
        self.assertEqual(self.records.record['password_hash'], 'private-password-hash')

    def test_retry_and_failure_leave_recoverable_outbox(self):
        attempts = []
        def sender(record):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError('offline')
            return 456
        alerts.deliver(self.records, 'test-user', sender=sender, pause=lambda _: None)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.records.record['signup_alert']['state'], 'sent')
        self.records.record['signup_alert'] = {'state': 'pending'}
        with patch.object(alerts, 'send_signup_alert'):
            def failure(record):raise RuntimeError('offline')
            alerts.deliver(self.records, 'test-user', sender=failure, pause=lambda _: None)
        self.assertEqual(self.records.record['signup_alert']['state'], 'failed')
        self.assertGreater(self.records.record['signup_alert']['retry_at'], time.time())
        alerts.deliver(self.records, 'test-user', sender=lambda _: self.fail('Backoff ignored'))
        self.records.record['signup_alert']['retry_at'] = 0
        alerts.deliver(self.records, 'test-user', sender=lambda _: 457)
        self.assertEqual(self.records.record['signup_alert']['message_id'], 457)

    def test_active_lease_and_legacy_accounts_do_not_send(self):
        for notice in (None, {'state': 'sending', 'lease_until': time.time()+900}):
            self.records.record['signup_alert'] = notice
            alerts.deliver(self.records, 'test-user', sender=lambda _: self.fail('Unexpected send'))

    def test_missing_private_receipt_is_not_success(self):
        def telegram(method, **params):
            return {} if method == 'sendMessage' else self.telegram(method, **params)
        with self.assertRaises(RuntimeError):
            alerts.send_signup_alert(self.records.record, telegram)

    def test_live_status_requires_signed_auth_and_hides_recipient_id(self):
        client = server.app.test_client()
        self.assertEqual(client.post('/api/internal/signup-alert-status', json={}).status_code, 401)
        raw = b'{}';stamp = str(int(time.time()));secret = 'test-only-bot-token'
        sig = hmac.new(secret.encode(), b'mp-signup-alert-status\n'+stamp.encode()+b'\n'+raw, hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': secret}), patch.object(alerts, 'private_destination', return_value=123):
            response = client.post('/api/internal/signup-alert-status', data=raw, content_type='application/json',
                                   headers={'X-Alert-Timestamp': stamp, 'X-Alert-Signature': sig})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json['private_chat_verified'])
            self.assertNotIn('123', response.get_data(as_text=True))
            self.assertNotIn(secret, response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
