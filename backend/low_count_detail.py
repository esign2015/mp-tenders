import json
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from scraper import (
    parse_organisation_rows, open_organisation_page_from_home,
    browser_get_all_tender_rows, open_tender_detail_by_search, parse_detail,
    clean, read_existing, write_csv
)

OUT = Path("all_tenders_org_detailed.csv")
STATUS = Path("data/low_count_detail_status.json")
# Sequential production plan: finish each count range before moving to the next.
RANGES = [(0,20,"0-20"), (21,60,"21-60"), (61,250,"61-250"), (251,500,"251-500"), (501,10**9,"501-plus")]

def save_status(data):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(data, indent=2), encoding="utf-8")

def now():
    return datetime.now(timezone.utc).isoformat()

def merge_detail(tid, tender, detail, by_id, order):
    detail.pop("URL", None)
    old = by_id.get(tid, {})
    for key in ("Organisation", "Department", "Division", "Sub Division"):
        if not clean(detail.get(key)) and clean(old.get(key)):
            detail[key] = old[key]
    old_levels = [clean(old.get(k)) for k in ("Organisation","Department","Division","Sub Division")]
    new_levels = [clean(detail.get(k)) for k in ("Organisation","Department","Division","Sub Division")]
    if sum(bool(x) for x in old_levels) > sum(bool(x) for x in new_levels):
        for key in ("Organisation", "Department", "Division", "Sub Division"):
            if clean(old.get(key)):
                detail[key] = old[key]
    detail["Detail Extracted"] = "Yes"
    detail["Organisation Tender Count Range"] = tender.get("range", "")
    if tid not in by_id:
        order.append(tid)
    by_id[tid] = {**old, **detail}

def main():
    existing = read_existing(OUT)
    by_id = {clean(r.get("Tender ID")): dict(r) for r in existing if clean(r.get("Tender ID"))}
    order = list(by_id)
    state = {
        "started_at": now(),
        "ranges": [label for _,_,label in RANGES],
        "current_range": "",
        "current_organisation": "",
        "organisations": [],
        "tenders_found": 0,
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "updated_at": now(),
    }
    save_status(state)

    with sync_playwright() as p:
        for lo, hi, label in RANGES:
            print(f"RANGE {label}: starting", flush=True)
            state["current_range"] = label  # live sequential checkpoint
            state["updated_at"] = now()
            save_status(state)

            browser = p.chromium.launch(headless=True)
            context = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width":1366,"height":900})
            page = context.new_page()
            try:
                org_soup = open_organisation_page_from_home(page)
                orgs = parse_organisation_rows(org_soup, page.url)
                targets = [o for o in orgs if lo <= int(o.get("count") or 0) <= hi]
            finally:
                context.close()
                browser.close()

            print(f"RANGE {label}: {len(targets)} organisations", flush=True)

            for idx, org in enumerate(targets, 1):
                state["current_organisation"] = org["name"]
                state["organisations"] = [
                    {"name": o["name"], "count": o["count"], "range": label} for o in targets
                ]
                state["updated_at"] = now()
                save_status(state)
                print(f"ORG {idx}/{len(targets)} [{label}]: {org['name']} ({org['count']})", flush=True)

                # Fetch and validate this organisation's complete tender-list pages first.
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width":1366,"height":900})
                page = context.new_page()
                try:
                    rows, pages = browser_get_all_tender_rows(page, org, int(org["count"]))
                except Exception as exc:
                    err=f"{org['name']} [{label}]: {type(exc).__name__}: {exc}"
                    print("  LIST FAIL:", err, flush=True)
                    state["failed"] += 1
                    state["errors"].append(err)
                    state["updated_at"] = now()
                    save_status(state)
                    context.close()
                    browser.close()
                    continue
                finally:
                    try: context.close()
                    except Exception: pass
                    try: browser.close()
                    except Exception: pass

                print(f"  LIST OK: {len(rows)}/{org['count']} across {pages} page(s)", flush=True)
                state["tenders_found"] += len(rows)
                state["updated_at"] = now()
                save_status(state)

                # Immediately detail each Tender ID from this organisation.
                # Each tender gets a fresh browser/context and is saved before
                # the next Tender ID starts, so GitHub Pages can receive it live.
                for pos, row in enumerate(rows, 1):
                    tid = clean(row.get("tender_id"))
                    if not tid:
                        continue
                    old = by_id.get(tid, {})
                    if clean(old.get("Detail Extracted")).casefold() in {"yes","true","1","ok"}:
                        state["skipped"] += 1
                        print(f"  SKIP {pos}/{len(rows)} {tid} already complete", flush=True)
                        continue

                    tender = {
                        "tender_id": tid,
                        "title": row.get("title",""),
                        "reference": row.get("reference",""),
                        "organisation": org["name"],
                        "range": label,
                    }
                    browser = context = None
                    try:
                        print(f"  DETAIL {pos}/{len(rows)} START {tid} — NEW BROWSER", flush=True)
                        browser = p.chromium.launch(headless=True)
                        context = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width":1366,"height":900})
                        page = context.new_page()
                        soup = open_tender_detail_by_search(page, tender)
                        detail = parse_detail(soup, page.url)
                        merge_detail(tid, tender, detail, by_id, order)
                        write_csv(OUT, [by_id[x] for x in order])
                        state["success"] += 1
                        state["updated_at"] = now()
                        save_status(state)
                        print(f"  DETAIL OK {tid} — SAVED IMMEDIATELY; NEXT TENDER", flush=True)
                    except Exception as exc:
                        state["failed"] += 1
                        state["errors"].append(f"{tid}: {type(exc).__name__}: {exc}")
                        state["updated_at"] = now()
                        save_status(state)
                        print(f"  DETAIL FAIL {tid}: {type(exc).__name__}: {exc}", flush=True)
                    finally:
                        if context:
                            try: context.close()
                            except Exception: pass
                        if browser:
                            try: browser.close()
                            except Exception: pass

            print(f"RANGE {label}: COMPLETE; moving to next range", flush=True)

    state["current_range"] = "complete"
    state["current_organisation"] = ""
    state["finished_at"] = now()
    state["updated_at"] = state["finished_at"]
    save_status(state)
    print(json.dumps({
        "tenders_found": state["tenders_found"],
        "success": state["success"],
        "skipped": state["skipped"],
        "failed": state["failed"]
    }, indent=2))

if __name__ == "__main__":
    main()
