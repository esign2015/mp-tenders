import csv, json, re, time
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from scraper import (
    PORTAL, parse_organisation_rows, open_organisation_page_from_home,
    browser_get_all_tender_rows, open_tender_detail_by_search, parse_detail,
    clean, read_existing, write_csv
)

OUT = Path("all_tenders_org_detailed.csv")
STATUS = Path("data/low_count_detail_status.json")
RANGES = [(0,20,"0-20"), (21,60,"21-60"), (61,250,"61-250"), (251,500,"251-500"), (501,10**9,"501-plus")]

def save_status(data):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(data, indent=2), encoding="utf-8")

def main():
    existing = read_existing(OUT)
    by_id = {clean(r.get("Tender ID")): dict(r) for r in existing if clean(r.get("Tender ID"))}
    order = list(by_id)
    state = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ranges": [label for _,_,label in RANGES],
        "organisations": [],
        "tenders_found": 0,
        "success": 0,
        "failed": 0,
        "errors": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width":1366,"height":900})
        page = context.new_page()
        try:
            org_soup = open_organisation_page_from_home(page)
            orgs = parse_organisation_rows(org_soup, page.url)
            tender_map = {}
            for lo, hi, label in RANGES:
                org_soup = open_organisation_page_from_home(page)
                orgs = parse_organisation_rows(org_soup, page.url)
                targets = [o for o in orgs if lo <= int(o.get("count") or 0) <= hi]
                print(f"RANGE {label}: {len(targets)} organisations", flush=True)
                state["current_range"] = label
                state["organisations"] = [{"name":o["name"],"count":o["count"],"range":label} for o in targets]
                save_status(state)
                for idx, org in enumerate(targets, 1):
                    print(f"ORG {idx}/{len(targets)} [{label}]: {org['name']} ({org['count']})", flush=True)
                    try:
                        rows, pages = browser_get_all_tender_rows(page, org, int(org["count"]))
                        for row in rows:
                            tid = clean(row.get("tender_id"))
                            if tid:
                                tender_map[tid] = {"tender_id":tid, "title":row.get("title",""), "reference":row.get("reference",""), "organisation":org["name"], "range":label}
                        print(f"  LIST OK: {len(rows)}/{org['count']} across {pages} page(s)", flush=True)
                    except Exception as exc:
                        err=f"{org['name']} [{label}]: {type(exc).__name__}: {exc}"
                        print("  LIST FAIL:", err, flush=True)
                        state["errors"].append(err)
                state["tenders_found"] = len(tender_map)
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_status(state)
        finally:
            context.close()
            browser.close()

        items = list(tender_map.values())
        for n, tender in enumerate(items, 1):
            tid = tender["tender_id"]
            if tid in by_id and clean(by_id[tid].get("Detail Extracted")).casefold() in {"yes","true","1","ok"}:
                print(f"SKIP {n}/{len(items)} {tid} already complete", flush=True)
                continue

            browser = None
            context = None
            try:
                print(f"DETAIL {n}/{len(items)} START {tid} — NEW BROWSER", flush=True)
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width":1366,"height":900})
                page = context.new_page()
                soup = open_tender_detail_by_search(page, tender)
                detail = parse_detail(soup, page.url)
                detail.pop("URL", None)

                # Preserve the already-known organisation hierarchy when the
                # fresh detail response is partial. Some portal views can
                # return only the top organisation even though the CSV already
                # contains the complete Department / Division / Sub Division.
                old = by_id.get(tid, {})
                for key in ("Organisation", "Department", "Division", "Sub Division"):
                    if not clean(detail.get(key)) and clean(old.get(key)):
                        detail[key] = old[key]

                # Never replace a complete hierarchy with a partial one.
                old_levels = [clean(old.get(k)) for k in ("Organisation","Department","Division","Sub Division")]
                new_levels = [clean(detail.get(k)) for k in ("Organisation","Department","Division","Sub Division")]
                if sum(bool(x) for x in old_levels) > sum(bool(x) for x in new_levels):
                    for key in ("Organisation", "Department", "Division", "Sub Division"):
                        if clean(old.get(key)):
                            detail[key] = old[key]

                detail["Detail Extracted"] = "Yes"
                detail["Organisation Tender Count Range"] = tender.get("range","")
                if tid not in by_id:
                    order.append(tid)
                by_id[tid] = {**old, **detail}
                write_csv(OUT, [by_id[x] for x in order])
                state["success"] += 1
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_status(state)
                print(f"DETAIL OK {tid} — SAVED IMMEDIATELY; BROWSER WILL CLOSE", flush=True)
            except Exception as exc:
                state["failed"] += 1
                state["errors"].append(f"{tid}: {type(exc).__name__}: {exc}")
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_status(state)
                print(f"DETAIL FAIL {tid}: {type(exc).__name__}: {exc}", flush=True)
            finally:
                if context:
                    context.close()
                if browser:
                    browser.close()

    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    state["updated_at"] = state["finished_at"]
    save_status(state)
    print(json.dumps({"organisations":len(state["organisations"]),"tenders_found":state["tenders_found"],"success":state["success"],"failed":state["failed"]}, indent=2))
    
if __name__ == "__main__":
    main()
