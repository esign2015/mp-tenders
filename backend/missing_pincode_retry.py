"""Recheck missing/invalid PINs separately from the large detail backfill."""
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from inventory_summary import clean, read_csv, valid_pincode, write_summary
from scraper import parse_portal_datetime
from district_mapping import resolve_district, mapping

ROOT = Path(__file__).resolve().parents[1]

def missing_live_pin_ids(root, now=None, include_district=False):
    now = now or datetime.now(timezone.utc)
    listed = {clean(row.get("Tender ID")): row
              for row in read_csv(Path(root) / "organisation_tenders.csv")}
    details = {clean(row.get("Tender ID")): row
               for row in read_csv(Path(root) / "all_tenders_org_detailed.csv")}
    missing = []
    for tid, listing in listed.items():
        row = details.get(tid, {})
        closing = parse_portal_datetime(listing.get("Closing Date") or row.get("Closing Date"))
        if not tid or clean(row.get("Status")).lower() == "cancelled" or (closing and closing <= now):
            continue
        if not valid_pincode(row.get("Pincode")) or (include_district and (not resolve_district(row) or clean(row.get("Pincode")) not in mapping()[1])):
            missing.append(tid)
    return sorted(missing)

def write_pin_status(root, status, initial_ids):
    remaining = missing_live_pin_ids(root)
    unresolved = missing_live_pin_ids(root, include_district=True)
    result = {"updated_at": datetime.now(timezone.utc).isoformat(),
              "status": status, "initial_missing_ids": initial_ids,
              "missing_pincode_count": len(remaining), "missing_pincode_ids": remaining,
              "unresolved_location_count": len(unresolved), "unresolved_location_ids": unresolved,
              "source": "Official MP Tender detail pages"}
    path = Path(root) / "data/missing_pincode_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    write_summary(root)
    return result

def repair_districts(root):
    for filename in ("all_tenders_org_detailed.csv", "tender_details.csv"):
        path = Path(root) / filename
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            rows, fields = list(reader), list(reader.fieldnames or [])
        changed = False
        for row in rows:
            district = resolve_district(row)
            if district and row.get("District") != district:
                row["District"] = district
                changed = True
        if changed:
            fields = list(dict.fromkeys(fields + ["District"]))
            temporary = path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            temporary.replace(path)

def main():
    os.chdir(ROOT)
    repair_districts(ROOT)
    ids = missing_live_pin_ids(ROOT, include_district=True)
    write_pin_status(ROOT, "running" if ids else "complete", ids)
    print(f"Missing/invalid live PINs: {len(ids)}", flush=True)
    if not ids:
        return
    os.environ.update({"PRIORITY_TENDER_IDS": ",".join(ids), "EXTRACT_ALL_INVENTORY": "1",
                       "PRIMARY_ID_SEARCH": "1", "RSP_FAST": "0",
                       "REPAIR_COMPLETED_DETAILS": "1", "EXISTING_ID_BATCH_SIZE": "0",
                       "DETAIL_SEARCH_WORKERS": "2"})
    from existing_id_detail import main as extract
    try:
        extract()
    finally:
        repair_districts(ROOT)
        result = write_pin_status(ROOT, "complete" if not missing_live_pin_ids(ROOT, include_district=True) else "retry_needed", ids)
        print(json.dumps(result), flush=True)

if __name__ == "__main__":
    import sys
    if "--districts-only" in sys.argv:
        repair_districts(ROOT)
        write_pin_status(ROOT, "retry_needed" if missing_live_pin_ids(ROOT, include_district=True) else "complete", [])
    else:
        main()
