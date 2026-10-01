from portal_fee_exceptions import verified_fee_omission
"""Count progress against the published organisation snapshot only."""
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_FIELDS = (
    "Tender ID", "Tender Fee", "Processing Fee", "EMD Fee", "Total Fee",
    "Location", "Pincode", "Work Description", "Product Category",
    "Contract Type", "Bid Validity",
)

def clean(value):
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()

def valid_pincode(value):
    return bool(re.fullmatch(r"[1-9]\d{5}", clean(value)))

def detail_complete(row):
    if clean(row.get("Search Route")) == "RSP -> live dataset import":
        return False  # Historical imports must be verified on the official portal.
    if clean(row.get("Detail Extracted")).upper() != "YES":
        return False
    if not all(clean(row.get(field)) for field in REQUIRED_FIELDS):
        return False
    if not valid_pincode(row.get("Pincode")):
        return False
    fee = re.search(r"-?\d+(?:\.\d+)?", clean(row.get("Processing Fee")).replace(",", ""))
    return verified_fee_omission(row) or bool(fee and float(fee.group()) > 0)

def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))

def build_summary(root):
    root = Path(root)
    orgs = read_csv(root / "organisations.csv")
    listed = read_csv(root / "organisation_tenders.csv")
    ids = {clean(row.get("Tender ID")) for row in listed} - {""}
    details = {clean(row.get("Tender ID")): row
               for row in read_csv(root / "all_tenders_org_detailed.csv")}
    success = {tid for tid in ids if detail_complete(details.get(tid, {}))}
    try:
        status = json.loads((root / "data/existing_id_detail_status.json").read_text())
    except (OSError, ValueError):
        status = {}
    failed = (set(status.get("failed_ids") or []) & ids) - success
    skipped = (set(status.get("skipped_ids") or []) & ids) - success - failed
    pending = ids - success - failed - skipped
    missing_pins = sorted(tid for tid in ids if not valid_pincode(details.get(tid, {}).get("Pincode")))
    missing_districts = sorted(tid for tid in ids if not clean(details.get(tid, {}).get("District")))
    legacy = sorted(tid for tid in pending if clean(details.get(tid, {}).get("Search Route")) == "RSP -> live dataset import")
    organisation_rows = []
    for org in orgs:
        name = clean(org.get("Organisation Name"))
        org_ids = {clean(row.get("Tender ID")) for row in listed
                   if clean(row.get("Organisation Name")).casefold() == name.casefold()} - {""}
        organisation_rows.append({
            "organisation": name, "serial": org.get("S.No.", ""),
            "snapshot_at": org.get("Retrieved At", ""),
            "portal_tender_count": int(re.sub(r"\D", "", clean(org.get("Tender Count"))) or 0),
            "copied_tenders": len(org_ids), "detail_complete": len(org_ids & success),
            "detail_failed": len(org_ids & failed), "detail_skipped": len(org_ids & skipped),
            "detail_pending": len(org_ids & pending),
        })
    portal = sum(int(re.sub(r"\D", "", clean(row.get("Tender Count"))) or 0) for row in orgs)
    return {
        "policy_version": "official-portal-v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_at": orgs[0].get("Retrieved At", "") if orgs else "",
        "organisation_count": len(orgs), "portal_tender_count": portal,
        "copied_tenders": len(ids), "detail_complete": len(success),
        "detail_failed": len(failed), "detail_skipped": len(skipped),
        "detail_pending": len(pending),
        "organisations": organisation_rows,
        "organisation_count_mismatches": sum(row["portal_tender_count"] != row["copied_tenders"] for row in organisation_rows),
        "legacy_verification_pending": len(legacy),
        "missing_district_count": len(missing_districts),
        "missing_district_ids": missing_districts,
        "known_pincode_count": len({clean(details.get(tid, {}).get("Pincode")) for tid in ids if valid_pincode(details.get(tid, {}).get("Pincode"))}),
        "department_count": len({clean(details.get(tid, {}).get("Department")) for tid in ids} - {""}),
        "missing_pincode_count": len(missing_pins),
        "missing_pincode_ids": missing_pins,
    }

def write_summary(root):
    root = Path(root)
    result = build_summary(root)
    # Existing workers may still write the old summary with a previous policy.
    # The shared consumer file is only produced by this policy's publisher.
    for filename in ("inventory_summary.json", "inventory_counts.json"):
        destination = root / "data" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)
    return result

if __name__ == "__main__":
    print(json.dumps(write_summary(Path(__file__).resolve().parents[1]), indent=2))
