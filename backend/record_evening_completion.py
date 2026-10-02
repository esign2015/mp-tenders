"""Record a terminal evening run, including failed/skipped detail work."""
import os
from datetime import datetime, timezone
from telegram_scheduler import ROOT, IST, read_json
from scheduled_telegram_delivery import write_json


def main():
    now = datetime.now(IST)
    # Every actual organisation-copy run ending in the evening qualifies.
    # No dependency on the exact scheduled cron identity.
    if now.hour < 19:
        print('Not an evening organisation run; no evening marker changed.')
        return
    run = os.environ['GITHUB_RUN_ID']
    status = read_json(ROOT/'data/existing_id_detail_status.json')
    detail_result = os.getenv('DETAIL_JOB_RESULT', 'unknown')
    copy_result = os.getenv('COPY_JOB_RESULT', 'unknown')
    result = 'success' if detail_result == copy_result == 'success' else 'failure'
    write_json(ROOT/'data/evening_detail_completion.json', {
        'run_id': run, 'completed_at': now.astimezone(timezone.utc).isoformat(),
        'result': result, 'copy_result': copy_result, 'detail_result': detail_result,
        'pending_count': len(status.get('pending_ids', [])),
        'failed_count': len(status.get('failed_ids', [])),
        'detail_updated_at': status.get('updated_at'),
    })


if __name__ == '__main__':
    main()
