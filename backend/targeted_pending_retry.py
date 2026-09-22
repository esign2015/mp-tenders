import csv, json
from pathlib import Path
from datetime import datetime, timezone

from existing_id_detail import rsp_style_extract_targets, clean

CSV = Path("all_tenders_org_detailed.csv")
SNAPSHOT = Path("organisation_tenders.csv")
STATUS = Path("data/existing_id_detail_status.json")
DETAIL_CSV = Path("tender_details.csv")

TARGET_ORGS = {
    "Directorate Urban Administration and Development",
    "MP Forest Department",
}

DETAIL_FIELDS = [
    "Tender ID","Organisation","Department","Division","Sub Division",
    "Tender Reference Number","Reference Number","Title","Tender Fee",
    "Processing Fee","EMD Fee","PAC Amount","Total Fee","Location","Pincode",
    "Work Description","Product Category","Sub Category","Contract Type",
    "Bid Validity","Pre Qualification Details","Bid Submission Start Date",
    "Bid Submission End Date","Bid Opening Date","Document Download Start Date",
    "Document Download End Date","Fee Payable To","Fee Payable At",
    "Published Date","Closing Date","Opening Date","Detail Extracted","Search Route"
]

def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

rows = read_rows(CSV)
snapshot = read_rows(SNAPSHOT)

pending_ids = []
pending_meta = {}
for s in snapshot:
    org = clean(s.get("Organisation Name"))
    tid = clean(s.get("Tender ID"))
    if org not in TARGET_ORGS or not tid:
        continue
    pending_meta[tid] = org
    current = next((r for r in rows if clean(r.get("Tender ID")) == tid), None)
    if not current or clean(current.get("Detail Extracted")).upper() != "YES":
        if tid not in pending_ids:
            pending_ids.append(tid)

print("TARGET ORGANISATIONS:", sorted(TARGET_ORGS), flush=True)
print("TARGET PENDING IDS:", pending_ids, flush=True)

by_id = {clean(r.get("Tender ID")): dict(r) for r in rows if clean(r.get("Tender ID"))}
success_ids = {
    clean(r.get("Tender ID"))
    for r in rows
    if clean(r.get("Tender ID")) and clean(r.get("Detail Extracted")).upper() == "YES"
}
errors = []

def save_csv():
    fields = list(rows[0].keys()) if rows else []
    for r in by_id.values():
        for k in r:
            if k not in fields:
                fields.append(k)
    with CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(by_id.values())

def save_detail_csv():
    detail_rows = [
        {k: row.get(k, "") for k in DETAIL_FIELDS}
        for row in by_id.values()
        if clean(row.get("Detail Extracted")).upper() == "YES"
    ]
    with DETAIL_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DETAIL_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(detail_rows)

def save_status(status, last_id=""):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({
        "status": status,
        "mode": "targeted_pending_retry",
        "target_organisations": sorted(TARGET_ORGS),
        "target_pending_ids": pending_ids,
        "success": len(success_ids),
        "failed": len(errors),
        "success_ids": sorted(success_ids),
        "failed_ids": sorted(e["Tender ID"] for e in errors),
        "pending_ids": sorted(set(pending_ids) - success_ids),
        "last_successful_tender_id": last_id,
        "errors": errors,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")

targets = []
for tid in pending_ids:
    base = by_id.get(tid, {"Tender ID": tid, "Organisation": pending_meta.get(tid, "")})
    targets.append(base)

save_status("running")

def checkpoint():
    save_csv()
    save_detail_csv()
    save_status("running", "")

def status(last_id=""):
    save_status("running", last_id)

if targets:
    failed = rsp_style_extract_targets(
        targets, by_id, status, save_csv, save_detail_csv, success_ids
    )[1]
    for base, exc in failed:
        errors.append({
            "Tender ID": clean(base.get("Tender ID")),
            "error": f"{type(exc).__name__}: {exc}",
        })
else:
    print("No pending rows found for target organisations.", flush=True)

save_csv()
save_detail_csv()
save_status("completed")
print(json.dumps({
    "target_pending": len(pending_ids),
    "success_in_run": len([x for x in pending_ids if x in success_ids]),
    "failed_in_run": len(errors),
    "remaining": [x for x in pending_ids if x not in success_ids],
}, indent=2), flush=True)
