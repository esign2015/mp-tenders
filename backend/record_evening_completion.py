"""Record the final scheduled detail batch, never a failed or running worker."""
import json,os
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    status=json.loads((ROOT/'data/existing_id_detail_status.json').read_text())
    run=os.environ['GITHUB_RUN_ID']
    if status.get('status')!='completed' or str(status.get('github_run_id'))!=run:
        raise RuntimeError('The evening detail worker did not complete this run.')
    path=ROOT/'data/evening_detail_completion.json'
    path.write_text(json.dumps({'run_id':run,'completed_at':datetime.now(timezone.utc).isoformat(),
        'pending_count':len(status.get('pending_ids',[])),'failed_count':len(status.get('failed_ids',[])),
        'detail_updated_at':status.get('updated_at')},indent=2))

if __name__=='__main__':main()
