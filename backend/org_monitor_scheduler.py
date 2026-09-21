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
    if slots[-1] != end_min:
        slots.append(end_min)
    return slots


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    now = datetime.now(IST)
    today = now.date()
    monitor = cfg["organisation_monitor"]
    start = hm(cfg["morning_telegram_ist"]) - int(monitor["start_before_morning_minutes"])
    end = hm(monitor["end_ist"])
    lo = int(monitor["random_interval_min_minutes"])
    hi = int(monitor["random_interval_max_minutes"])
    tick = int(monitor.get("scheduler_tick_minutes", 5))
    grace = int(monitor.get("max_lateness_minutes", max(10, tick * 2)))

    slots = build_slots(today, start, end, lo, hi)
    current_min = now.hour * 60 + now.minute

    # Pick the latest unexecuted slot that is still reasonably close.
    # This protects against a delayed GitHub tick without running an old check
    # hours late.
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    candidates = []
    for slot in slots:
        if slot <= current_min <= slot + grace:
            key = f"{today.isoformat()}:{slot}"
            if state.get("last_executed") != key:
                candidates.append((slot, key))

    if not candidates:
        print("ORG_SCHEDULE: no random check due now.")
        return 0

    slot, key = candidates[-1]
    print("run=true")
    print(f"slot={slot // 60:02d}:{slot % 60:02d}")
    print(f"key={key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
