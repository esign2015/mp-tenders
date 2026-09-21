import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "schedule_config.json"
STATE = ROOT / "data" / "org_monitor_schedule.json"


def hm(text):
    h, m = [int(x) for x in text.split(":")]
    return h * 60 + m


def build_slots(day, start_min, end_min, lo, hi):
    rng = random.Random(f"mp-org-monitor-{day.isoformat()}")
    slots = [start_min]
    current = start_min
    while True:
        current += rng.randint(lo, hi)
        if current >= end_min:
            break
        slots.append(current)
    # Always perform a final check at the requested 18:58 IST cutoff.
    if slots[-1] != end_min:
        slots.append(end_min)
    return slots


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    now = datetime.now(IST)
    today = now.date()
    morning = hm(cfg["morning_telegram_ist"])
    monitor = cfg["organisation_monitor"]
    start = morning - int(monitor["start_before_morning_minutes"])
    end = hm(monitor["end_ist"])
    lo = int(monitor["random_interval_min_minutes"])
    hi = int(monitor["random_interval_max_minutes"])
    tick = int(monitor.get("scheduler_tick_minutes", 5))

    # Never schedule before the requested 30-minute pre-alert start.
    slots = build_slots(today, start, end, lo, hi)
    current_min = now.hour * 60 + now.minute

    # A GitHub Actions tick can be delayed. Accept the most recent slot only
    # inside one scheduler interval, preventing duplicate execution.
    due = [s for s in slots if s <= current_min < s + tick]
    if not due:
        print("ORG_SCHEDULE: no random check due now.")
        return 0

    slot = due[-1]
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    key = f"{today.isoformat()}:{slot}"
    if state.get("last_executed") == key:
        print(f"ORG_SCHEDULE: slot {slot} already executed.")
        return 0

    # Only decide here. The workflow records the slot AFTER the monitor succeeds.
    # This means a failed run can be retried on the next scheduler tick.
    print(f"run=true")
    print(f"slot={slot // 60:02d}:{slot % 60:02d}")
    print(f"key={key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
