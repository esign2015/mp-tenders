#!/usr/bin/env python3
"""Reconcile one MP Tender run to the portal organisation counts.

Rules:
- organisations.csv is the authoritative portal count snapshot for this run.
- organisation_tenders.csv is the primary Tender-ID inventory.
- closing_date_recovery.csv is the first recovery source.
- RSP is a last-resort Tender-ID source only for organisations still short.
- RSP fields are never copied into the dashboard; only Tender IDs are added.
- A run is published as the live snapshot only when every organisation has
  exactly its portal count and the total unique Tender-ID count equals the
  portal total.
"""
import csv
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RSP_URL = "https://raw.githubusercontent.com/rsparvat/RSPTender/main/data/tenders.json"
ORG_FILE = ROOT / "organisations.csv"
LIST_FILE = ROOT / "organisation_tenders.csv"
RECOVERY_FILE = ROOT / "data" / "closing_date_recovery.csv"
SNAPSHOT_FILE = ROOT / "data" / "live_snapshot.json"
RECOVERY_STATUS = ROOT / "data" / "snapshot_recovery_status.json"

def clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def key(v):
    return clean(v).casefold()

def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def count_value(v):
    m = re.search(r"\d+", clean(v))
    return int(m.group(0)) if m else 0

def tid(v):
    m = re.search(r"\b20\d{2}_[A-Za-z0-9]+_\d+_\d+\b", clean(v))
    return m.group(0) if m else ""

def org_counts():
    rows = read_csv(ORG_FILE)
    return {key(r.get("Organisation Name")): {
        "name": clean(r.get("Organisation Name")),
        "count": count_value(r.get("Tender Count")),
    } for r in rows if clean(r.get("Organisation Name"))}

def rows_by_org(rows):
    out = {}
    for r in rows:
        org = key(r.get("Organisation Name"))
        tender = tid(r.get("Tender ID"))
        if org and tender:
            out.setdefault(org, []).append(r)
    return out

def append_rows(new_rows):
    existing = read_csv(LIST_FILE)
    fields = list(existing[0].keys()) if existing else [
        "S.No.","Organisation Name","Portal Tender Count","Copied Tender Count",
        "Count Status","Tender ID","Title","Reference Number","Published Date",
        "Closing Date","Opening Date","Tender URL","Raw Row"
    ]
    by_id = {}
    order = []
    for r in existing:
        t = tid(r.get("Tender ID"))
        if t and t not in by_id:
            by_id[t] = dict(r)
            order.append(t)
    for r in new_rows:
        t = tid(r.get("Tender ID"))
        if not t or t in by_id:
            continue
        row = {f: "" for f in fields}
        row.update(r)
        by_id[t] = row
        order.append(t)
    # Recalculate copied counts only for the rows we know belong to each org.
    groups = {}
    for t in order:
        o = key(by_id[t].get("Organisation Name"))
        groups[o] = groups.get(o, 0) + 1
    for i,t in enumerate(order,1):
        r = by_id[t]
        o = key(r.get("Organisation Name"))
        r["S.No."] = str(i)
        r["Copied Tender Count"] = str(groups.get(o,0))
        r["Count Status"] = "MATCH" if clean(r.get("Portal Tender Count")) and count_value(r.get("Portal Tender Count")) == groups.get(o,0) else "MISMATCH"
    with LIST_FILE.open("w",encoding="utf-8-sig",newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(by_id[t] for t in order)

def load_rsp():
    with urllib.request.urlopen(RSP_URL, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [r for r in payload.get("tenders",[]) if r.get("source") == "MP" and tid(r.get("tender_id"))]

def main():
    counts = org_counts()
    if not counts:
        raise SystemExit("organisations.csv has no portal organisation counts")

    current = read_csv(LIST_FILE)
    groups = rows_by_org(current)
    missing = []
    for org, meta in counts.items():
        have = {tid(r.get("Tender ID")) for r in groups.get(org,[])}
        have.discard("")
        if len(have) < meta["count"]:
            missing.append((org, meta["name"], meta["count"] - len(have), have))

    sources = []
    candidates = []

    # Recovery source 1: MPTenders Closing-within-14-days, no CAPTCHA.
    recovery = rows_by_org(read_csv(RECOVERY_FILE))
    for org, name, need, have in missing:
        seen = set(have)
        for r in recovery.get(org,[]):
            t = tid(r.get("Tender ID"))
            if t and t not in seen:
                candidates.append({
                    "S.No.":"",
                    "Organisation Name":name,
                    "Portal Tender Count":str(counts[org]["count"]),
                    "Copied Tender Count":"",
                    "Count Status":"PENDING_RECOVERY",
                    "Tender ID":t,
                    "Title":"",
                    "Reference Number":"",
                    "Published Date":"",
                    "Closing Date":clean(r.get("Closing Date")),
                    "Opening Date":clean(r.get("Opening Date")),
                    "Tender URL":"",
                    "Raw Row":"Closing within 14 days"
                })
                seen.add(t)
                need -= 1
                if need <= 0: break
        if need <= 0:
            sources.append({"organisation":name,"source":"Closing within 14 days","recovered":counts[org]["count"]-len(have)})
    
    # Re-read simulated additions per organisation so RSP is used only for the
    # organisations still short after the no-CAPTCHA source.
    proposed_by_org = rows_by_org(current + candidates)
    still_missing = []
    for org, meta in counts.items():
        have = {tid(r.get("Tender ID")) for r in proposed_by_org.get(org,[])}
        have.discard("")
        if len(have) < meta["count"]:
            still_missing.append((org, meta["name"], meta["count"]-len(have), have))

    if still_missing:
        rsp = load_rsp()
        rsp_by_org = {}
        for r in rsp:
            o = key(r.get("organisation"))
            if o:
                rsp_by_org.setdefault(o,[]).append(r)
        for org, name, need, have in still_missing:
            seen = set(have)
            for r in rsp_by_org.get(org,[]):
                t = tid(r.get("tender_id"))
                if t and t not in seen:
                    # Only the permanent Tender ID and the portal organisation
                    # count are copied. RSP title/fees/dates are deliberately ignored.
                    candidates.append({
                        "S.No.":"",
                        "Organisation Name":name,
                        "Portal Tender Count":str(counts[org]["count"]),
                        "Copied Tender Count":"",
                        "Count Status":"RSP_ID_RECOVERY",
                        "Tender ID":t,
                        "Title":"",
                        "Reference Number":"",
                        "Published Date":"",
                        "Closing Date":"",
                        "Opening Date":"",
                        "Tender URL":"",
                        "Raw Row":"RSP Tender ID recovery"
                    })
                    seen.add(t)
                    need -= 1
                    if need <= 0: break

    # Add only unique candidates, never more than an organisation's portal gap.
    before_ids = {tid(r.get("Tender ID")) for r in current if tid(r.get("Tender ID"))}
    unique = []
    used_by_org = {}
    for r in candidates:
        t = tid(r.get("Tender ID"))
        o = key(r.get("Organisation Name"))
        if not t or t in before_ids:
            continue
        used_by_org[o] = used_by_org.get(o,0) + 1
        if used_by_org[o] <= max(0, counts.get(o,{}).get("count",0) - len(groups.get(o,[]))):
            unique.append(r)
            before_ids.add(t)

    if unique:
        append_rows(unique)
        current = read_csv(LIST_FILE)

    groups = rows_by_org(current)
    mismatches = []
    snapshot_ids = []
    for org, meta in counts.items():
        ids = {tid(r.get("Tender ID")) for r in groups.get(org,[])}
        ids.discard("")
        if len(ids) != meta["count"]:
            mismatches.append({
                "Organisation Name":meta["name"],
                "Portal Count":meta["count"],
                "Copied Count":len(ids),
                "Difference":meta["count"]-len(ids)
            })
        snapshot_ids.extend(sorted(ids))

    snapshot_ids = sorted(set(snapshot_ids))
    portal_total = sum(x["count"] for x in counts.values())
    exact = not mismatches and len(snapshot_ids) == portal_total

    now = datetime.now(timezone.utc).isoformat()
    status = {
        "run_at_utc": now,
        "portal_organisation_count": len(counts),
        "portal_tender_count": portal_total,
        "copied_unique_tender_count": len(snapshot_ids),
        "organisation_mismatches": mismatches,
        "recovery_candidates_added": len(unique),
        "recovery_sources": ["MPTenders Closing within 14 days", "RSP Tender ID recovery"],
        "exact_match": exact,
    }
    RECOVERY_STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8")

    if exact:
        SNAPSHOT_FILE.write_text(json.dumps({
            "snapshot_at_utc":now,
            "portal_tender_count":portal_total,
            "portal_organisation_count":len(counts),
            "tender_ids":snapshot_ids,
            "source":"MPTenders Tenders-by-Organisation + Closing Date recovery + RSP ID recovery",
        },ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"EXACT SNAPSHOT: {portal_total} portal tenders = {len(snapshot_ids)} unique Tender IDs")
        return 0

    print(f"MISMATCH: portal={portal_total}, copied={len(snapshot_ids)}, organisations={len(mismatches)}")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
