"""Daily alerts: extraction completion first, with same-day catch-up retries."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'data/schedule_config.json'
STATE = ROOT / 'data/telegram_schedule.json'


def hm(text):
    h, m = [int(x) for x in text.split(':')]
    return h * 60 + m


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def due_alert(cfg, state, now, extraction_result='', completion=None):
    now = now.astimezone(IST)
    current = now.hour * 60 + now.minute
    due = []
    for mode, field in (('morning','morning_telegram_ist'), ('evening_new','evening_new_telegram_ist'), ('evening_total','evening_total_telegram_ist')):
        delay=cfg.get(mode+'_after_detail_minutes')
        if mode != 'morning' and delay is not None:
            try:
                stamp=datetime.fromisoformat((completion or {})['completed_at']).astimezone(IST)
            except (KeyError,ValueError,TypeError):
                continue
            if stamp.date()!=now.date() or not (completion or {}).get('run_id'):
                continue
            deadline=stamp+timedelta(minutes=int(delay))
            if now<deadline:continue
            target=deadline.hour*60+deadline.minute
            key=f'{stamp.date().isoformat()}:{mode}'
            if state.get(mode)!=key:due.append((target,mode,key))
            continue
        if not cfg.get(field):
            continue
        target = hm(cfg[field])
        # The first morning snapshot sends immediately on success OR failure.
        if mode == 'morning' and extraction_result and cfg.get('morning_send_after_extraction',True):
            target = hm(cfg.get('morning_snapshot_ist', '08:55'))
        key = f'{now.date().isoformat()}:{mode}'
        # A delayed Actions run must not lose today's notification.
        if current >= target and state.get(mode) != key:
            due.append((target, mode, key))
    return min(due) if due else None


def main():
    result = due_alert(read_json(CONFIG), read_json(STATE), datetime.now(IST), os.getenv('MORNING_EXTRACTION_RESULT', ''),read_json(ROOT/'data/evening_detail_completion.json'))
    if not result:
        print('TELEGRAM_SCHEDULE: no alert due now.')
        return 0
    target, mode, key = result
    print(f'run=true\nmode={mode}\nkey={key}\ntarget={target // 60:02d}:{target % 60:02d}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
