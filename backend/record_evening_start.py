"""Mark the 7 PM organisation run before extraction starts."""
import os
from datetime import datetime, timezone
from telegram_scheduler import ROOT, IST
from scheduled_telegram_delivery import write_json


def main():
    now = datetime.now(IST)
    schedule = os.getenv('SCRAPE_SCHEDULE', '')
    event = os.getenv('SCRAPE_EVENT', '')
    if schedule != '35 13 * * *' and not (now.hour >= 19 and event in {'workflow_dispatch', 'push'}):
        return
    write_json(ROOT / 'data/evening_run_start.json', {
        'run_id': os.environ['GITHUB_RUN_ID'], 'report_date': now.date().isoformat(),
        'started_at': now.astimezone(timezone.utc).isoformat(),
    })


if __name__ == '__main__':
    main()
