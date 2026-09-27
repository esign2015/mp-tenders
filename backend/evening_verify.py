import csv, json, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))

def clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def parse_dt(v):
    s=clean(v)
    for fmt in ("%d/%b/%Y %I:%M %p","%d-%b-%Y %I:%M %p","%d/%m/%Y %I:%M %p",
                "%d-%m-%Y %I:%M %p","%d/%b/%Y %H:%M","%d-%b-%Y %H:%M",
                "%d/%m/%Y %H:%M","%d-%m-%Y %H:%M","%d/%b/%Y","%d-%b-%Y",
                "%d/%m/%Y","%d-%m-%Y"):
        try:
            return datetime.strptime(s,fmt).replace(tzinfo=IST)
        except ValueError:
            pass
    return None

orgs=read_csv(ROOT/"organisations.csv")
snapshot=read_csv(ROOT/"organisation_tenders.csv")
master=read_csv(ROOT/"all_tenders_org_detailed.csv")
detail_status={}
try:
    detail_status=json.loads((ROOT/"data/existing_id_detail_status.json").read_text(encoding="utf-8"))
except Exception:
    detail_status={}

portal_ids={clean(r.get("Tender ID")) for r in snapshot if clean(r.get("Tender ID"))}
master_by_id={clean(r.get("Tender ID")):r for r in master if clean(r.get("Tender ID"))}
master_ids=set(master_by_id)
present=portal_ids & master_ids
missing=sorted(portal_ids-master_ids)

now=datetime.now(IST)
reopened=[]
archive_candidates=[]
for tid,row in master_by_id.items():
    closing=parse_dt(row.get("Closing Date"))
    if tid in portal_ids and closing and closing <= now:
        reopened.append(tid)
    elif tid not in portal_ids and closing:
        age=(now-closing).total_seconds()/86400
        if 0 <= age <= 10:
            archive_candidates.append(tid)

success={tid for tid in portal_ids if clean(master_by_id.get(tid,{}).get("Detail Extracted")).upper()=="YES"}
failed=set(clean(x) for x in detail_status.get("failed_ids",[]) if clean(x)) & portal_ids
skipped=set(clean(x) for x in detail_status.get("skipped_ids",[]) if clean(x)) & portal_ids
pending=portal_ids-success-failed-skipped

org_mismatch=[]
for org in orgs:
    name=clean(org.get("Organisation Name"))
    count=int(re.sub(r"\D","",clean(org.get("Tender Count"))) or 0)
    copied=sum(1 for r in snapshot if clean(r.get("Organisation Name")).casefold()==name.casefold() and clean(r.get("Tender ID")))
    if count != copied:
        org_mismatch.append({"Organisation Name":name,"Portal Count":count,"Copied Count":copied,"Difference":count-copied})

report={
    "status":"verified",
    "verified_at":now.isoformat(),
    "portal_organisation_count":len(orgs),
    "portal_tender_count":sum(int(re.sub(r"\D","",clean(o.get("Tender Count"))) or 0) for o in orgs),
    "copied_unique_tender_ids":len(portal_ids),
    "master_total_tenders":len(master_ids),
    "portal_ids_present_in_master":len(present),
    "portal_ids_missing_in_master":len(missing),
    "missing_tender_ids":missing[:500],
    "live_dashboard_equivalent_count":len(present),
    "portal_listed_with_past_closing_reactivated":len(reopened),
    "reactivated_tender_ids":sorted(reopened)[:500],
    "archive_candidates_not_in_portal":len(archive_candidates),
    "detail_success":len(success),
    "detail_failed":len(failed),
    "detail_skipped":len(skipped),
    "detail_pending":len(pending),
    "organisation_count_mismatches":len(org_mismatch),
    "organisation_mismatches":org_mismatch,
    "counts_match": len(org_mismatch)==0 and len(missing)==0 and len(portal_ids)==sum(int(re.sub(r"\D","",clean(o.get("Tender Count"))) or 0) for o in orgs),
}
out=ROOT/"data/evening_verification.json"
out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
