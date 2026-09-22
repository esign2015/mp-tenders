import csv, re, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
IST=timezone(timedelta(hours=5,minutes=30))
MASTER=ROOT/"all_tenders_org_detailed.csv"
SNAPSHOT=ROOT/"organisation_tenders.csv"

def clean(v): return re.sub(r"\s+"," ",str(v or "")).strip()

def parse_dt(v):
    s=clean(v)
    fmts=("%d/%b/%Y %I:%M %p","%d-%b-%Y %I:%M %p","%d/%m/%Y %I:%M %p","%d-%m-%Y %I:%M %p",
          "%d/%b/%Y %H:%M","%d-%b-%Y %H:%M","%d/%m/%Y %H:%M","%d-%m-%Y %H:%M")
    for f in fmts:
        try: return datetime.strptime(s,f).replace(tzinfo=IST)
        except ValueError: pass
    return None

if not MASTER.exists():
    raise SystemExit("master CSV missing; refusing cleanup")

with MASTER.open(encoding="utf-8-sig",newline="") as f:
    rows=list(csv.DictReader(f))
with SNAPSHOT.open(encoding="utf-8-sig",newline="") as f:
    live_ids={clean(r.get("Tender ID")) for r in csv.DictReader(f) if clean(r.get("Tender ID"))}

now=datetime.now(IST)
kept=[]
removed=[]
for r in rows:
    tid=clean(r.get("Tender ID"))
    close=parse_dt(r.get("Closing Date"))
    age_hours=((now-close).total_seconds()/3600) if close else None
    # Never remove a tender that is still present on the current portal snapshot.
    # Remove only records whose closing time is more than 48h old and which are
    # no longer listed by the portal.
    if tid and tid not in live_ids and age_hours is not None and age_hours > 48:
        removed.append(tid)
    else:
        kept.append(r)

if len(kept) < 1:
    raise SystemExit("cleanup safety stop: would leave an empty master CSV")

fields=list(rows[0].keys()) if rows else []
with MASTER.open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
    w.writeheader(); w.writerows(kept)

# Keep tender_details.csv aligned when it contains Tender ID.
td=ROOT/"tender_details.csv"
td_removed=0
if td.exists():
    try:
        with td.open(encoding="utf-8-sig",newline="") as f: td_rows=list(csv.DictReader(f))
        if td_rows and "Tender ID" in td_rows[0]:
            td_keep=[r for r in td_rows if clean(r.get("Tender ID")) not in set(removed)]
            td_removed=len(td_rows)-len(td_keep)
            with td.open("w",encoding="utf-8-sig",newline="") as f:
                w=csv.DictWriter(f,fieldnames=list(td_rows[0].keys()),extrasaction="ignore")
                w.writeheader(); w.writerows(td_keep)
    except Exception as exc:
        print("tender_details cleanup skipped:",exc)

report={
  "status":"completed",
  "cleaned_at":now.isoformat(),
  "retention_hours":48,
  "before":len(rows),
  "removed":len(removed),
  "after":len(kept),
  "tender_details_removed":td_removed,
  "sample_removed_ids":removed[:100]
}
(ROOT/"data/cleanup_status.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
