"""Apply portal-verified corrections without dropping other workers' records."""
import csv
import json
import re
from pathlib import Path
from inventory_summary import detail_complete
from corrigendum_state import merge_state

def apply(root, repairs):
    root = Path(root)
    for row in repairs:
        if not detail_complete(row) or not re.fullmatch(r"\d{6}", row.get("Pincode", "")):
            raise ValueError("Verified correction must contain complete details and a six-digit pincode")
    for name in ("all_tenders_org_detailed.csv", "tender_details.csv"):
        path = root / name
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = list(reader.fieldnames or [])
            rows = {row["Tender ID"]: row for row in reader}
        for repair in repairs:
            tid = repair["Tender ID"]
            merged = dict(rows.get(tid, {}))
            merged.update({key: value for key, value in repair.items() if str(value or "").strip()})
            merge_state(merged, rows.get(tid, {}), repair)
            rows[tid] = merged
            fields = list(dict.fromkeys(fields + list(merged)))
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows.values())
        temporary.replace(path)

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    apply(root, json.loads((root / "data/verified_detail_repairs.json").read_text()))
