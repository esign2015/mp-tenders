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
        if current > end_min:
            break
        slots.append(current)
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

    # The caller performs the actual monitor. State is written only after the
    # decision so a failed monitor can be retried on the next scheduler tick.
    STATE.write_text(json.dumps({
        "date": today.isoformat(),
        "last_executed": key,
        "slot_ist": f"{slot // 60:02d}:{slot % 60:02d}",
        "generated_from_morning_alert": cfg["morning_telegram_ist"]
    }, indent=2), encoding="utf-8")
    print(f"ORG_SCHEDULE: RUN at {slot // 60:02d}:{slot % 60:02d} IST")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
