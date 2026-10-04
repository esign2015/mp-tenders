"""Keep verified changes for currently active tenders only."""
import csv,io,json,time
from datetime import datetime,timezone
from nightly_cleanup import parse_dt
FIELDS={'Closing Date':'अंतिम तिथि व समय','EMD Fee':'EMD शुल्क','Tender Fee':'टेंडर शुल्क','Processing Fee':'पोर्टल शुल्क','Corrigendum':'शुद्धिपत्र'}
def changes(remote,local,previous,now=None):
    now=time.time() if now is None else now
    def rows(raw):return {row.get('Tender ID',''):row for row in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))) if row.get('Tender ID')}
    before=rows(remote);after=rows(local)
    try:history=json.loads(previous).get('tenders',{})
    except (ValueError,TypeError):history={}
    result={}
    for tid,row in after.items():
        closing=parse_dt(row.get('Closing Date'));old=before.get(tid)
        if not closing or closing.timestamp()<=now or str(row.get('Status','')).lower()=='cancelled':continue
        events=list(history.get(tid,[]))[-20:]
        # A change is recorded only after a newer detail check, not a guess.
        if old and row.get('Tested At') and str(row['Tested At'])>str(old.get('Tested At','')):
            for field,label in FIELDS.items():
                a=str(old.get(field,'')).strip();b=str(row.get(field,'')).strip()
                if a and b and a!=b:events.append({'at':now,'verified_at':row['Tested At'],'field':label,'key':field,'before':a,'after':b})
        if events:result[tid]=events[-20:]
    return json.dumps({'updated_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'tenders':result},ensure_ascii=False).encode()
