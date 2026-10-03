"""Private signup notifications with a saved outbox and a WhatsApp draft link."""
import hashlib
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from admin_mismatch_alert import resolve_private_admin, telegram_call

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='signup-alert')
_slots = threading.BoundedSemaphore(16)
_lock = threading.Lock()
_active = set()
_destination = {}


def enabled():
    return os.getenv('SIGNUP_ALERTS_ENABLED', '1') != '0' and bool(os.getenv('TELEGRAM_BOT_TOKEN', '').strip())


def welcome_message(name):
    name = ' '.join(str(name or '').split())
    return f'''👋 नमस्कार {name} जी,

🎉 *MP Tender Live Dashboard में आपका स्वागत है!*
आपका रजिस्ट्रेशन सफलतापूर्वक पूरा हो गया है।

यहाँ आप चालू टेंडर, अंतिम तिथि व समय, PAC, EMD और अन्य शुल्क देख सकते हैं। जिला, विभाग और अंतिम तिथि के अनुसार टेंडर खोजकर PDF भी डाउनलोड कर सकते हैं।

🌐 *डैशबोर्ड खोलें*
https://tenders.codinglms.xyz/

📢 *टेंडर की PDF और नियमित अपडेट के लिए Telegram चैनल जॉइन करें*
https://t.me/mptendersalert

🟢 *हमारे WhatsApp ग्रुप से जुड़ें*
https://chat.whatsapp.com/BuKI6bxZGHVBIHA6KZt7Vy

💬 *सहायता या सुझाव के लिए संपर्क करें*
https://t.me/rdgyan

⚠️ अंतिम टेंडर सूचना, शुद्धिपत्र, पात्रता, शुल्क और अंतिम तिथि व समय की पुष्टि आधिकारिक टेंडर पोर्टल से अवश्य करें।

🙏 *SAR Digital Services, Kannod*'''


def whatsapp_link(record):
    mobile = str(record.get('mobile', ''))
    if not re.fullmatch(r'\+91[6-9][0-9]{9}', mobile):
        raise ValueError('A valid signup mobile is required')
    return 'https://wa.me/' + mobile[1:] + '?text=' + quote(welcome_message(record.get('name')), safe='')


def private_destination(call, configured_id='', use_cache=False):
    """Require the named project bot and the exact private owner identity."""
    cache_key = hashlib.sha256((os.getenv('TELEGRAM_BOT_TOKEN', '') + configured_id).encode()).hexdigest()
    with _lock:
        cached = _destination.get(cache_key)
    if use_cache and cached and cached[1] > time.monotonic():
        return cached[0]
    bot = call('getMe') or {}
    if not bot.get('is_bot') or str(bot.get('username', '')).casefold() != 'mptenders_bot':
        raise ValueError('Signup alerts require @mptenders_bot')
    chat_id = resolve_private_admin(call, configured_id, username='rdgyan')
    if configured_id:
        chat = call('getChat', chat_id=chat_id)
        if str(chat.get('username', '')).casefold() != 'rdgyan':
            raise ValueError('Signup alert owner must be @rdgyan')
    with _lock:
        _destination[cache_key] = (chat_id, time.monotonic() + 600)
    return chat_id


def configured_call(method, **params):
    return telegram_call(os.getenv('TELEGRAM_BOT_TOKEN', '').strip(), method, **params)


def send_signup_alert(record, call=configured_call, welcome_batch=False):
    chat_id = private_destination(call, os.getenv('TELEGRAM_ADMIN_CHAT_ID', '').strip(), use_cache=call is configured_call)
    try:
        stamp = datetime.fromisoformat(record['signup_at']).astimezone(IST).strftime('%d/%m/%Y %I:%M:%S %p IST')
    except (KeyError, ValueError):
        stamp = 'उपलब्ध नहीं'
    heading = '👤 पहले पंजीकृत user — स्वागत संदेश भेजें' if welcome_batch else '👤 नया signup — MP Tender Live Dashboard'
    text = (heading + '\n\n'
            f"नाम: {record.get('name', '—')}\nमोबाइल: {record.get('mobile', '—')}\n"
            f"जिला: {record.get('district', '—')}\nतहसील: {record.get('tehsil', '—')}\n"
            f'समय: {stamp}\n\n'
            'नीचे बटन दबाएँ। WhatsApp में स्वागत संदेश तैयार होगा; Send आपको दबाना है।')
    result = call('sendMessage', chat_id=chat_id, text=text, disable_web_page_preview=True,
                  reply_markup={'inline_keyboard': [[{'text': '🟢 WhatsApp पर स्वागत भेजें', 'url': whatsapp_link(record)}]]}) or {}
    if not result.get('message_id') or result.get('chat', {}).get('type') != 'private' or result.get('chat', {}).get('id') != chat_id:
        raise RuntimeError('Private signup alert delivery receipt missing')
    return result['message_id']


def _claim(accounts, user_id, lease, outbox='signup_alert'):
    for _ in range(4):
        record = accounts.get(user_id=user_id)
        notice = (record or {}).get(outbox) or {}
        if not notice or notice.get('state') == 'sent' or notice.get('retry_at', 0) > time.time():
            return None
        if notice.get('state') == 'sending' and notice.get('lease_until', 0) > time.time():
            return None
        revision = record['revision']
        record[outbox] = {**notice, 'state': 'sending', 'lease': lease, 'lease_until': time.time() + 900}
        if accounts.update(record, revision):
            return record
    return None


def _settle(accounts, user_id, lease, result, outbox='signup_alert'):
    # Read again so concurrent profile/session updates are preserved.
    for _ in range(4):
        record = accounts.get(user_id=user_id)
        notice = (record or {}).get(outbox) or {}
        if notice.get('lease') != lease:
            return
        revision = record['revision']
        record[outbox] = result
        if accounts.update(record, revision):
            return
    raise RuntimeError('Signup alert receipt save conflict')


def deliver(accounts, user_id, sender=send_signup_alert, pause=time.sleep, outbox='signup_alert'):
    lease = str(uuid.uuid4())
    record = _claim(accounts, user_id, lease, outbox)
    if record is None:
        return
    error_type = ''
    for attempt in range(3):
        try:
            message_id = sender(record)
        except Exception as exc:
            error_type = type(exc).__name__
            if attempt < 2:
                pause((2, 5)[attempt])
            continue
        # Never resend an acknowledged Telegram message if saving its receipt fails.
        _settle(accounts, user_id, lease, {'state': 'sent', 'message_id': message_id,
                                         'sent_at': datetime.now(IST).isoformat()}, outbox)
        return
    _settle(accounts, user_id, lease, {'state': 'failed', 'error_type': error_type, 'retry_at': time.time() + 60}, outbox)


def queue(accounts, user_id):
    if not enabled():
        return False
    with _lock:
        if user_id in _active or not _slots.acquire(blocking=False):
            return False
        _active.add(user_id)
    def run():
        try:
            deliver(accounts, user_id)
        except Exception as exc:
            # Tokens, personal details and welcome URLs never appear in logs.
            logger.warning('Private signup alert pending: %s', type(exc).__name__)
        finally:
            with _lock:
                _active.discard(user_id)
            _slots.release()
    try:
        _pool.submit(run)
    except Exception:
        with _lock:
            _active.discard(user_id)
        _slots.release()
        return False
    return True
