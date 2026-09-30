"""Apply the audited evening inventory without losing newer worker details."""
import csv
import json
from pathlib import Path
from nightly_cleanup import cleanup, IST
from datetime import datetime
from inventory_summary import write_summary

root = Path(__file__).resolve().parents[1]
audit = json.loads((root/'data/evening_count_audit.json').read_text())
now = datetime.now(IST)
# This repair is valid only for its audited day, never for tomorrow's tenders.
if datetime.fromisoformat(audit['checked_at']).date() == now.date():
    removed = {r['Tender ID'] for r in audit['extra_tenders'] if r['Reason']=='Removed from current UAD list since 19:51'}
    path = root/'organisation_tenders.csv'
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream);fields=reader.fieldnames;rows=list(reader)
    rows=[r for r in rows if r['Tender ID'] not in removed]
    ids={r['Tender ID'] for r in rows}
    if len(ids) != audit['portal_count']:
        raise RuntimeError('Portal inventory has changed; refusing to apply old count repair')
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    path=root/'organisations.csv'
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream);fields=reader.fieldnames;orgs=list(reader)
    for org in orgs:
        org['Tender Count']=str(sum(r['Organisation Name']==org['Organisation Name'] for r in rows))
        org['Retrieved At']=audit['checked_at']
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(orgs)
    snapshot={'snapshot_at':audit['checked_at'],'portal_tender_count':len(ids),
              'copied_unique_tender_ids':len(ids),'verified':True,'tender_ids':sorted(ids)}
    (root/'data/live_snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
    # List dates are the current portal inventory, while old detail checkpoints
    # may still carry the deadline from before a corrigendum extension.
    by_id={r['Tender ID']:r for r in rows}
    for name in ('all_tenders_org_detailed.csv','tender_details.csv'):
        path=root/name
        with path.open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream);fields=reader.fieldnames;details=list(reader)
        present={r['Tender ID'] for r in details}
        for tid,listed in by_id.items():
            if tid not in present:
                recovered={key:listed.get(key,'') for key in fields}
                recovered['Organisation']=listed.get('Organisation Name','')
                details.append(recovered)
        for detail in details:
            listed=by_id.get(detail['Tender ID'])
            if listed:
                detail['Closing Date']=listed['Closing Date']
                detail['Bid Submission End Date']=listed['Closing Date']
                detail['Tested At']=now.isoformat()
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(details)
    cleanup(root,now=now)
    write_summary(root)
