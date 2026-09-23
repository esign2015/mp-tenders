#!/usr/bin/env python3
from __future__ import annotations
import csv, json, re, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))
RSP_URL = "https://raw.githubusercontent.com/rsparvat/RSPTender/main/data/tenders.json"
TENDER_FIELDS = ["S.No.","Organisation Name","Portal Tender Count","Copied Tender Count","Count Status","Tender ID","Title","Reference Number","Published Date","Closing Date","Opening Date","Tender URL","Raw Row"]
TID_RE = re.compile(r"\b20\d{2}_[A-Z0-9_./-]+?_\d+\b", re.I)

def clean(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def key(v): return re.sub(r"[^a-z0-9]+"," ",clean(v).casefold()).strip()
def read_csv(p):
    if not p.exists(): return []
    with p.open(encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))
def write_csv(p,rows):
    with p.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=TENDER_FIELDS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
def count(v):
    m=re.search(r"\d[\d,]*",clean(v)); return int(m.group(0).replace(",","")) if m else 0
def tid(v):
    m=TID_RE.findall(clean(v)); return m[-1] if m else ""

def main():
    now=datetime.now(IST)
    orgs=read_csv(ROOT/"organisations.csv")
    current=read_csv(ROOT/"organisation_tenders.csv")
    closing=read_csv(ROOT/"data/closing_date_recovery.csv")
    if not orgs: raise RuntimeError("organisations.csv missing/empty")
    by_tid={clean(r.get("Tender ID")):dict(r) for r in current if clean(r.get("Tender ID"))}

    def org_ids(name):
        k=key(name); return {t for t,r in by_tid.items() if key(r.get("Organisation Name"))==k}

    recovery_log=[]
    # Source 1: MPTenders Closing within 14 days.
    for org in orgs:
        name=clean(org.get("Organisation Name")); expected=count(org.get("Tender Count"))
        if len(org_ids(name))==expected: continue
        for src in closing:
            if len(org_ids(name))>=expected: break
            stid=tid(src.get("Tender ID"))
            if not stid or stid in by_tid: continue
            if key(src.get("Organisation Name"))!=key(name): continue
            by_tid[stid]={"Organisation Name":name,"Tender ID":stid,"Title":clean(src.get("Title")),"Reference Number":clean(src.get("Reference Number")),"Published Date":clean(src.get("E-Published Date")),"Closing Date":clean(src.get("Closing Date")),"Opening Date":clean(src.get("Opening Date")),"Raw Row":clean(src.get("Organisation Chain")),"Count Status":"MPTENDERS 14-DAY RECOVERY"}
            recovery_log.append({"Tender ID":stid,"Organisation Name":name,"Source":"MPTenders Closing within 14 days"})

    unresolved=[]
    for org in orgs:
        name=clean(org.get("Organisation Name")); expected=count(org.get("Tender Count")); have=len(org_ids(name))
        if have!=expected: unresolved.append((name,expected,have))

    # Source 2: RSP only supplies candidate Tender IDs.
    if unresolved:
        req=urllib.request.Request(RSP_URL,headers={"User-Agent":"mp-tenders-recovery/1.0"})
        with urllib.request.urlopen(req,timeout=180) as f: payload=json.load(f)
        rsp_by_org={}
        for r in payload.get("tenders",[]):
            if clean(r.get("source")).upper()!="MP": continue
            stid=clean(r.get("tender_id")); ro=clean(r.get("organisation"))
            if stid and ro: rsp_by_org.setdefault(key(ro),set()).add(stid)
        for name,expected,_ in unresolved:
            for stid in sorted(rsp_by_org.get(key(name),set())):
                if len(org_ids(name))>=expected: break
                if stid in by_tid: continue
                by_tid[stid]={"Organisation Name":name,"Tender ID":stid,"Count Status":"RSP RECOVERY - DETAIL VERIFICATION","Raw Row":"RSP Tender ID recovery only"}
                recovery_log.append({"Tender ID":stid,"Organisation Name":name,"Source":"RSP Tender ID recovery only"})

    mismatches=[]
    output=[]
    for org in orgs:
        name=clean(org.get("Organisation Name")); expected=count(org.get("Tender Count"))
        ids=[t for t,r in by_tid.items() if key(r.get("Organisation Name"))==key(name)]
        copied=len(ids)
        if copied!=expected: mismatches.append({"Organisation Name":name,"Portal Count":expected,"Copied Count":copied,"Difference":expected-copied})
        for stid in ids:
            r=dict(by_tid[stid]); r.update({"S.No.":len(output)+1,"Organisation Name":name,"Portal Tender Count":expected,"Copied Tender Count":copied,"Count Status":"MATCH" if copied==expected else "MISMATCH","Tender ID":stid})
            output.append(r)
    write_csv(ROOT/"organisation_tenders.csv",output)
    portal_total=sum(count(o.get("Tender Count")) for o in orgs)
    copied_total=len({clean(r.get("Tender ID")) for r in output if clean(r.get("Tender ID"))})
    report={"status":"verified" if not mismatches else "mismatch","snapshot_at":now.isoformat(),"portal_organisation_count":len(orgs),"portal_tender_count":portal_total,"copied_unique_tender_ids":copied_total,"organisation_mismatches":mismatches,"mismatch_count":len(mismatches),"recovered_from_mptenders_closing_14_days":sum(x["Source"].startswith("MPTenders") for x in recovery_log),"recovered_from_rsp_id_only":sum(x["Source"].startswith("RSP") for x in recovery_log),"recovery_log":recovery_log}
    (ROOT/"data").mkdir(exist_ok=True)
    (ROOT/"data"/"run_snapshot.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    with (ROOT/"data"/"run_history.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps(report,ensure_ascii=False)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if mismatches: raise SystemExit("FINAL SNAPSHOT MISMATCH")

if __name__=="__main__": main()
