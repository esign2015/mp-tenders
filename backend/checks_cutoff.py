"""IST cutoff for standalone Home/corrigendum checks only."""
import os
import signal
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
class ChecksClosed(Exception):
    pass

def seconds_left(now=None):
    now = (now or datetime.now(IST)).astimezone(IST)
    end = datetime.combine(now.date(), time(18, 30), tzinfo=IST)
    return max(0.0, (end - now).total_seconds())

def ensure_open():
    if seconds_left() <= 0:
        raise ChecksClosed("Standalone checks stop at 18:30 IST")

def main():
    remaining = seconds_left()
    if remaining <= 0:
        print("Skipped: after 18:30 IST.", flush=True)
        return 0
    proc = subprocess.Popen([sys.executable, *sys.argv[1:]], start_new_session=True)
    try:
        return proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        print("18:30 IST cutoff: stopping standalone check.", flush=True)
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        return 0

if __name__ == "__main__":
    sys.exit(main())
