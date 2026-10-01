"""Send daily alerts with Telegram receipts and resumable PDF delivery."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from telegram_scheduler import ROOT, IST, read_json, due_alert


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def deliver(root, mode, key, extraction_result='', alert_module=None):
    if alert_module is None:
        import telegram_alerts as alert_module
    root = Path(root)
    ledger_path = root / 'data/telegram_delivery' / (key.replace(':', '_') + '.json')
    ledger = read_json(ledger_path)
    operations = ledger.setdefault('operations', {})
    ledger.update(mode=mode, key=key, extraction_result=extraction_result or 'scheduled-catch-up')
    counters = {'text': 0, 'document': 0}

    def wrapped(kind, sender):
        def send(*args, **kwargs):
            counters[kind] += 1
            operation = f'{kind}:{counters[kind]}'
            if operation in operations:
                return operations[operation]
            result = sender(*args, **kwargs)
            message = result.get('result', {}) if isinstance(result, dict) else {}
            if not message.get('message_id'):
                raise RuntimeError('Telegram delivery receipt missing')
            operations[operation] = {'message_id': message['message_id'], 'sent_at': datetime.now(timezone.utc).isoformat(), 'kind': kind}
            write_json(ledger_path, ledger)
            print(f'Telegram confirmed {mode} {operation}: message_id={message["message_id"]}', flush=True)
            return result
        return send

    attempt = {'attempted_at': datetime.now(timezone.utc).isoformat(), 'mode': mode, 'key': key, 'extraction_result': ledger['extraction_result']}
    try:
        with patch.dict(os.environ, {'NOTIFY_MODE': mode, 'MORNING_EXTRACTION_RESULT': extraction_result}), \
             patch.object(alert_module, 'telegram_message', wrapped('text', alert_module.telegram_message)), \
             patch.object(alert_module, 'telegram_document', wrapped('document', alert_module.telegram_document)):
            result = alert_module.main()
            if result not in (None, 0):
                raise RuntimeError('Telegram alert did not finish')
        if not operations:
            raise RuntimeError('No Telegram delivery receipt')
        ledger['completed_at'] = datetime.now(timezone.utc).isoformat()
        write_json(ledger_path, ledger)
        state_path = root / 'data/telegram_schedule.json'
        state = read_json(state_path)
        state[mode] = key
        write_json(state_path, state)
        attempt['send_outcome'] = 'success'
        attempt['message_ids'] = [value['message_id'] for value in operations.values()]
    except Exception as exc:
        attempt['send_outcome'] = 'failure'
        attempt['error_type'] = type(exc).__name__
        raise
    finally:
        write_json(root / 'data/telegram_last_attempt.json', attempt)


def main():
    root = ROOT
    extraction_result = os.getenv('MORNING_EXTRACTION_RESULT', '')
    due = due_alert(read_json(root / 'data/schedule_config.json'), read_json(root / 'data/telegram_schedule.json'), datetime.now(IST), extraction_result)
    if not due:
        print('No unsent daily Telegram alert is due.')
        return 0
    _, mode, key = due
    if mode == 'morning' and not extraction_result:
        summary = read_json(root / 'data/inventory_counts.json')
        try:
            stamp = datetime.fromisoformat(summary.get('snapshot_at', '')).astimezone(IST)
            extraction_result = 'success' if stamp.date() == datetime.now(IST).date() else 'not-refreshed'
        except (ValueError, TypeError):
            extraction_result = 'not-refreshed'
    deliver(root, mode, key, extraction_result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
