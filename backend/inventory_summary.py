# Verified missing portal fees are tracked separately from extraction errors.
from portal_fee_exceptions import verified_fee_omission
"""Count progress against the published organisation snapshot only."""
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from nightly_cleanup import parse_dt, IST

def verify_live_counts(root, orgs, listed, details, now=None):
    now = now or datetime.now(IST)
    try:
        snapshot = json.loads((Path(root) / 'data/live_snapshot.json').read_text())
    except (OSError, ValueError):
        snapshot = {}
    ids = set(snapshot.get('tender_ids') or []) - {''}
    copied = {clean(row.get('Tender ID')) for row in listed} - {''}
    merged = dict(details)
    for row in listed:
        tid = clean(row.get('Tender ID'))
        if tid:
            merged[tid] = {**details.get(tid, {}), **{key: value for key, value in row.items() if clean(value)}}
    future = {tid for tid, row in merged.items() if clean(row.get('Status')).lower() != 'cancelled'
              and (closing := parse_dt(row.get('Closing Date'))) and closing > now}
    missing = ids - set(merged)
    unknown_dates = {tid for tid in ids if not parse_dt(merged.get(tid, {}).get('Closing Date'))}
    portal = sum(int(re.sub(r'\D', '', clean(row.get('Tender Count'))) or 0) for row in orgs)
    errors = []
    if snapshot.get('verified') is not True: errors.append('Awaiting a verified portal inventory')
    if ids != copied: errors.append('Verified inventory and copied Tender IDs differ')
    if len(ids) != portal: errors.append('Portal total and unique Tender IDs differ')
    if missing: errors.append('Verified Tender IDs are missing from dashboard data')
    if unknown_dates: errors.append('Verified Tender IDs have missing or invalid closing dates')
    for org in orgs:
        name = clean(org.get('Organisation Name')).casefold()
        org_ids = {clean(row.get('Tender ID')) for row in listed if clean(row.get('Organisation Name')).casefold() == name} - {''}
        expected = int(re.sub(r'\D', '', clean(org.get('Tender Count'))) or 0)
        if len(org_ids) != expected:
            errors.append('Organisation count mismatch: ' + clean(org.get('Organisation Name')))
    return {'status': 'mismatch' if errors else 'verified', 'checked_at': now.isoformat(),
            'snapshot_at': snapshot.get('snapshot_at', ''), 'portal_tender_count': portal,
            'verified_unique_ids': len(ids), 'dashboard_live_count': len(future & ids),
            'closed_or_cancelled_count': len(ids - future - unknown_dates),
            'excluded_master_only_live_count': len(future - ids),
            'excluded_master_only_live_ids': sorted(future - ids),
            'missing_ids': sorted(missing), 'invalid_closing_ids': sorted(unknown_dates),
            'errors': errors, 'counts_match': not errors,
            'overnight_policy': 'Keep verified inventory from 19:00 through 09:00; replace only with a new verified copy'}

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
        "live_verification": verify_live_counts(root, orgs, listed, details),
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
    audit = root / 'data/live_count_verification.json'
    temporary = audit.with_suffix('.tmp')
    temporary.write_text(json.dumps(result['live_verification'], ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(audit)
    return result

if __name__ == "__main__":
    print(json.dumps(write_summary(Path(__file__).resolve().parents[1]), indent=2))
