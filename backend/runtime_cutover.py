"""Briefly bridge checkpoints from workers started before the data cutover.

The bridge expires. Only public runtime files can move, and newer details win.
"""
import csv,hashlib,io,json,subprocess,time
from pathlib import Path
from nightly_cleanup import parse_dt
from data_branch import runtime_path

def sync():
    state_path=Path('data/runtime_cutover.json')
    try:state=json.loads(state_path.read_text())
    except (OSError,ValueError):return
    if time.time()>state.get('expires_at',0):return
    subprocess.run(['git','fetch','--depth=1','origin','main'],check=True,capture_output=True)
    raw_tree=subprocess.check_output(['git','ls-tree','-r','origin/main'],text=True)
    current={line.split('\t',1)[1]:line.split()[2] for line in raw_tree.splitlines() if '\t' in line}
    from publish_data_checkpoint import merge_details,publish
    changed=[]
    for path,sha in current.items():
        if not runtime_path(path) or path in {'data/runtime_cutover.json','data/tender_changes.json'} or 'telegram' in path or path.startswith('data/admin_') or path.startswith('data/welcome_'):continue
        if sha==state.get('main_blobs',{}).get(path):continue
        state.setdefault('main_blobs',{})[path]=sha
        source=subprocess.check_output(['git','show','origin/main:'+path]);target=Path(path);existing=target.read_bytes() if target.exists() else b''
        if path in {'all_tenders_org_detailed.csv','tender_details.csv'}:source=merge_details(existing,source)
        elif path in {'organisation_tenders.csv','organisations.csv'}:
            def latest(raw):
                rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))));return max((str(row.get('Retrieved At','')) for row in rows),default='')
            if latest(existing)>latest(source):continue
        elif path.endswith('.json'):
            try:
                old=json.loads(existing or '{}');new=json.loads(source)
                def latest(item):
                    values=[parse_dt(item.get(key)) for key in ('snapshot_at','updated_at','finished_at','checked_at','cleaned_at')]
                    return max((value.timestamp() for value in values if value),default=0)
                if isinstance(old,dict) and isinstance(new,dict) and latest(old)>latest(new):continue
            except (ValueError,TypeError):continue
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source);changed.append(path)
    if changed:
        state_path.write_text(json.dumps(state));publish(changed+['data/runtime_cutover.json']);print('Preserved pre-cutover worker checkpoints:',len(changed))
