import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "schedule_config.json"
STATE = ROOT / "data" / "telegram_schedule.json"


def hm(text):
    h, m = [int(x) for x in text.split(":")]
    return h * 60 + m


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    now = datetime.now(IST)
    today = now.date()
    current = now.hour * 60 + now.minute
    max_delay = int(cfg.get("telegram_max_delay_minutes", 45))

    candidates = [
        ("morning", hm(cfg["morning_telegram_ist"])),
        ("evening_new", hm(cfg["evening_new_telegram_ist"])),
        ("evening_total", hm(cfg["evening_total_telegram_ist"])),
    ]

    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    due = []
    for mode, target in candidates:
        if target <= current <= target + max_delay:
            key = f"{today.isoformat()}:{mode}"
            if state.get(mode) != key:
                due.append((target, mode, key))

    if not due:
        print("TELEGRAM_SCHEDULE: no alert due now.")
        return 0

    # Only one Telegram message family is sent per scheduler tick.
    # If two become due together, the earlier target wins; the next 5-minute
    # tick can send the other one. This avoids simultaneous Telegram sends.
    due.sort()
    target, mode, key = due[0]
    print("run=true")
    print(f"mode={mode}")
    print(f"key={key}")
    print(f"target={target // 60:02d}:{target % 60:02d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
