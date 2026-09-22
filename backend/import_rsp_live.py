#!/usr/bin/env python3
from __future__ import annotations
import csv, json, os, re, urllib.request
from datetime import datetime, timezone

RSP_URL = "https://raw.githubusercontent.com/rsparvat/RSPTender/main/data/tenders.json"
CSV_PATH = "all_tenders_org_detailed.csv"
STATUS_PATH = "data/rsp_import_status.json"
FIELDS = ["Tender ID","Published Date","Closing Date","Opening Date","Title","Reference Number","Organisation","Department","Division","Sub Division","PAC Amount","EMD Fee","Tender Fee","Processing Fee","Total Fee","Location","Pincode","Work Description","Product Category","Sub Category","Contract Type","Bid Validity","Pre Qualification Details","Bid Submission Start Date","Bid Submission End Date","Bid Opening Date","Document Download Start Date","Document Download End Date","Fee Payable To","Fee Payable At","Status","Detail Extracted","Corrigendum","Corrigendum Last Checked","Corrigendum Detected At","Corrigendum Type","Corrigendum 15m Checked","Corrigendum 5m Checked","URL","Search Route","Search Result Title","Tested At"]

def clean(v):
    return "" if v is None else str(v).strip()

def money(v):
    s=clean(v)
    m=re.search(r"-?\d[\d,]*(?:\.\d+)?",s)
    return m.group(0).replace(",","") if m else ""

def pf(r,*names):
    p=r.get("portal_fields") if isinstance(r.get("portal_fields"),dict) else {}
    wanted={x.casefold() for x in names}
    for k,v in p.items():
        if clean(k).casefold() in wanted:
            return clean(v)
    return ""

def map_row(r, now):
    fee=money(r.get("tender_fee")); proc=money(r.get("processing_fee")); total=money(r.get("total_fee"))
    if not total and (fee or proc):
        total=str(int(float(fee or 0)+float(proc or 0)))
    work=clean(r.get("work_en"))
    return {
      "Tender ID":clean(r.get("tender_id")),"Published Date":clean(r.get("published_date")),
      "Closing Date":clean(r.get("bid_end")),"Opening Date":pf(r,"Bid Opening Date","Opening Date"),
      "Title":pf(r,"Title","Tender Title") or work,"Reference Number":clean(r.get("nit_ref")),
      "Organisation":clean(r.get("organisation")),"Department":clean(r.get("org_unit")),
      "Division":"","Sub Division":"","PAC Amount":money(r.get("pac")),"EMD Fee":money(r.get("emd")),
      "Tender Fee":fee,"Processing Fee":proc,"Total Fee":total,"Location":clean(r.get("location")),
      "Pincode":clean(r.get("pincode")),"Work Description":work,
      "Product Category":pf(r,"Product Category"),"Sub Category":pf(r,"Sub Category"),
      "Contract Type":pf(r,"Contract Type"),"Bid Validity":pf(r,"Bid Validity"),
      "Pre Qualification Details":pf(r,"Pre Qualification Details","NDA/Pre Qualification"),
      "Bid Submission Start Date":pf(r,"Bid Submission Start Date"),
      "Bid Submission End Date":clean(r.get("bid_end")),"Bid Opening Date":pf(r,"Bid Opening Date"),
      "Document Download Start Date":pf(r,"Document Download Start Date"),
      "Document Download End Date":pf(r,"Document Download End Date"),
      "Fee Payable To":pf(r,"Fee Payable To"),"Fee Payable At":pf(r,"Fee Payable At"),
      "Status":clean(r.get("status")) or "Open","Detail Extracted":"YES",
      "Corrigendum":clean(r.get("corrigendum_latest")),"Corrigendum Last Checked":"",
      "Corrigendum Detected At":"","Corrigendum Type":"","Corrigendum 15m Checked":"",
      "Corrigendum 5m Checked":"","URL":clean(r.get("detail_url")),
      "Search Route":"RSP -> live dataset import","Search Result Title":"","Tested At":now}

def main():
    req=urllib.request.Request(RSP_URL,headers={"User-Agent":"mp-tenders-rsp-import/1.0"})
    with urllib.request.urlopen(req,timeout=180) as resp: payload=json.load(resp)
    all_rows=payload.get("tenders",[])
    mp=[r for r in all_rows if clean(r.get("source")).upper()=="MP" and clean(r.get("tender_id"))]
    if not mp: raise RuntimeError("RSP returned zero MP tenders; refusing to overwrite.")
    existing={}
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH,encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                tid=clean(row.get("Tender ID"))
                if tid: existing[tid]={k:clean(row.get(k)) for k in FIELDS}
    now=datetime.now(timezone.utc).isoformat()
    for r in mp:
        tid=clean(r.get("tender_id")); mapped=map_row(r,now)
        base=existing.get(tid,{k:"" for k in FIELDS})
        for k,v in mapped.items():
            if v!="": base[k]=v
        # RSP's exact fee fields are authoritative for this imported record.
        base["Tender Fee"]=mapped["Tender Fee"]; base["Processing Fee"]=mapped["Processing Fee"]; base["Total Fee"]=mapped["Total Fee"]; base["Detail Extracted"]="YES"
        existing[tid]=base
    with open(CSV_PATH,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction="ignore"); w.writeheader(); w.writerows(existing.values())
    os.makedirs("data",exist_ok=True)
    status={"status":"completed","source":RSP_URL,"rsp_total_rows":len(all_rows),"rsp_mp_rows":len(mp),"imported_or_refreshed":len(mp),"dashboard_csv_rows":len(existing),"updated_at":now}
    with open(STATUS_PATH,"w",encoding="utf-8") as f: json.dump(status,f,ensure_ascii=False,indent=2)
    print(json.dumps(status,ensure_ascii=False))
if __name__=="__main__": main()
