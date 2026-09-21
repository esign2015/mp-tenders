# DAILY_DETAIL_WORKER_VERSION = 2
# Fresh bootstrap trigger: [fresh-bootstrap]
# Retry after bootstrap writer fix
import csv, json, re, time
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from scraper import parse_detail, parse_tender_rows, PORTAL

CSV = Path("all_tenders_org_detailed.csv")
STATUS = Path("data/existing_id_detail_status.json")
BATCH_SIZE = int(__import__("os").environ.get("EXISTING_ID_BATCH_SIZE", "0"))
SNAPSHOT = Path("organisation_tenders.csv")
DETAIL_CSV = Path("tender_details.csv")
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

def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def visible_text_inputs(scope):
    return [x for x in scope.locator("input").all()
            if x.is_visible() and (x.get_attribute("type") or "text").lower() in ("text","search")]

def find_search_area(page):
    heading = page.get_by_text("Search with ID/Title/Reference no", exact=False).first
    if heading.count() == 0:
        return page
    for level in range(1, 7):
        try:
            node = heading.locator("xpath=" + "/.." * level)
            if visible_text_inputs(node):
                return node
        except Exception:
            pass
    return page

def do_search(page, tender_id):
    page.goto(PORTAL, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)
    area = find_search_area(page)
    inputs = visible_text_inputs(area)
    if not inputs:
        inputs = [x for x in page.locator("input").all()
                  if x.is_visible() and (x.get_attribute("type") or "text").lower() in ("text","search")]
    if not inputs:
        raise RuntimeError("Search Tender input not found")
    chosen = None
    for inp in inputs:
        meta = " ".join(clean(inp.get_attribute(a)) for a in ("id","name","placeholder","title","class"))
        if re.search(r"search|tender|reference|id", meta, re.I):
            chosen = inp
            break
    chosen = chosen or inputs[-1]
    chosen.fill(tender_id)

    buttons = [b for b in area.locator("input,button,a").all() if b.is_visible()]
    go = None
    for b in buttons:
        try:
            tag = b.evaluate("(e)=>e.tagName")
            txt = clean(b.inner_text() if tag in ("BUTTON","A")
                        else b.get_attribute("value") or b.get_attribute("title") or b.get_attribute("alt"))
        except Exception:
            txt = ""
        meta = " ".join(clean(b.get_attribute(a)) for a in ("id","name","value","title","alt","class"))
        if re.fullmatch(r"go|search", txt, re.I) or re.search(r"\b(go|search)\b", meta, re.I):
            go = b
            break
    if not go:
        form = chosen.locator("xpath=ancestor::form[1]")
        if form.count():
            go = form.locator("input[type='submit'],button").first
    if not go or go.count() == 0:
        raise RuntimeError("GO/Search button not found")

    go.click()
    page.wait_for_load_state("domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)

    result_soup = BeautifulSoup(page.content(), "html.parser")
    result_rows = parse_tender_rows(result_soup, page.url)
    match = next((r for r in result_rows if clean(r.get("tender_id")).casefold() == tender_id.casefold()), None)
    if not match:
        raise RuntimeError(f"Search returned no matching Tender row for {tender_id}")
    title = clean(match.get("title"))
    if not title:
        raise RuntimeError(f"Tender Title missing in search result for {tender_id}")

    title_link = None
    anchors = page.locator("a")
    for i in range(anchors.count()):
        a = anchors.nth(i)
        if not a.is_visible():
            continue
        txt = clean(a.inner_text())
        if title.casefold() in txt.casefold() or txt.casefold() == title.casefold():
            title_link = a
            break
    if title_link is None:
        raise RuntimeError(f"Actual Tender Title link not found after GO for {tender_id}")

    title_link.click()
    page.wait_for_load_state("domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)
    soup = BeautifulSoup(page.content(), "html.parser")
    detail = parse_detail(soup, PORTAL)

    if clean(detail.get("Tender ID")) != tender_id:
        body = clean(soup.get_text(" ", strip=True))
        if tender_id.casefold() not in body.casefold():
            raise RuntimeError(f"Detail page does not contain requested Tender ID; got={detail.get('Tender ID','')}")
        detail["Tender ID"] = tender_id

    detail.pop("URL", None)
    detail["Detail Extracted"] = "YES"
    detail["Search Route"] = "Home -> Tender ID -> GO -> Tender Title -> Detail"
    return detail

def main():
    if not CSV.exists():
        raise RuntimeError("all_tenders_org_detailed.csv not found")

    with CSV.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    # Only current portal IDs are eligible. Existing detailed records are skipped.
    current_ids = []
    if SNAPSHOT.exists():
        with SNAPSHOT.open(encoding="utf-8-sig", newline="") as sf:
            for sr in csv.DictReader(sf):
                tid = clean(sr.get("Tender ID"))
                if tid and tid not in current_ids:
                    current_ids.append(tid)
    current_set = set(current_ids)

    incomplete = [
        row for row in rows
        if clean(row.get("Tender ID")) in current_set
        and clean(row.get("Detail Extracted")).upper() != "YES"
    ]
    targets = incomplete[:BATCH_SIZE] if BATCH_SIZE > 0 else incomplete

    print(
        f"CSV IDs: {len(rows)} | current portal IDs: {len(current_ids)} | "
        f"new/incomplete current IDs: {len(incomplete)} | targets: {len(targets)}",
        flush=True
    )

    by_id = {clean(r.get("Tender ID")): r for r in rows if clean(r.get("Tender ID"))}
    errors = []
    failed_bases = []
    success = 0

    def save_csv():
        nonlocal fields
        out_fields = list(fields)
        for merged in by_id.values():
            for k in merged:
                if k not in out_fields:
                    out_fields.append(k)
        with CSV.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(by_id.values())
        fields = out_fields

    def save_detail_csv():
        # Separate enrichment dataset: only successfully extracted detail pages.
        detail_rows = [
            {k: row.get(k, "") for k in DETAIL_FIELDS}
            for row in by_id.values()
            if clean(row.get("Detail Extracted")).upper() == "YES"
        ]
        tmp = DETAIL_CSV.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=DETAIL_FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(detail_rows)
        tmp.replace(DETAIL_CSV)

    def save_status(status, last_id=""):
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps({
            "status": status,
            "batch_size": BATCH_SIZE,
            "total_csv_ids": len(rows),
            "current_portal_ids": len(current_ids),
            "initial_incomplete": len(incomplete),
            "batch_targets": len(targets),
            "success": success,
            "failed": len(errors),
            "last_successful_tender_id": last_id,
            "errors": errors,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, indent=2), encoding="utf-8")

    def process_targets(p, target_rows, is_retry=False):
        nonlocal success
        for base in target_rows:
            tid = clean(base.get("Tender ID"))
            browser = context = None
            try:
                print(
                    f"DETAIL {'RETRY ' if is_retry else ''}START {tid}",
                    flush=True
                )
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    locale="en-IN",
                    timezone_id="Asia/Kolkata",
                    viewport={"width": 1366, "height": 900}
                )
                page = context.new_page()
                detail = do_search(page, tid)

                for key in ("Organisation","Department","Division","Sub Division"):
                    if clean(base.get(key)) and len(clean(base.get(key))) >= len(clean(detail.get(key))):
                        detail[key] = base.get(key, "")
                for key in ("Title","Reference Number","Published Date","Closing Date","Opening Date"):
                    if not clean(detail.get(key)):
                        detail[key] = base.get(key, "")

                merged = dict(base)
                merged.update(detail)
                merged.pop("URL", None)
                by_id[tid] = merged
                success += 1

                # Persist immediately so one successful Tender ID is never lost.
                save_csv()
                save_detail_csv()
                save_status("running", tid)
                print(f"DETAIL OK {tid} — SAVED — NEXT ID", flush=True)

            except Exception as e:
                failure = {
                    "Tender ID": tid,
                    "error": f"{type(e).__name__}: {e}",
                    "retry_pass": bool(is_retry)
                }
                errors.append(failure)
                if not is_retry:
                    failed_bases.append(base)
                print(
                    f"DETAIL {'RETRY ' if is_retry else ''}FAIL {tid}: "
                    f"{type(e).__name__}: {e}",
                    flush=True
                )
            finally:
                if context:
                    context.close()
                if browser:
                    browser.close()

    save_status("running")
    with sync_playwright() as p:
        # First pass: every new/current incomplete Tender ID.
        process_targets(p, targets, is_retry=False)

        # Exactly one retry for IDs that failed in the first pass.
        retry_targets = list(failed_bases)
        if retry_targets:
            print(f"RETRY PASS START: {len(retry_targets)} IDs", flush=True)
            before = len(errors)
            process_targets(p, retry_targets, is_retry=True)

            # Keep only the final failure entry for IDs that failed twice.
            final_errors = {}
            for e in errors:
                final_errors[e["Tender ID"]] = e
            errors[:] = list(final_errors.values())

    save_csv()
    save_detail_csv()
    save_status("completed")
    print(
        json.dumps(
            {"success": success, "failed": len(errors), "errors": errors},
            indent=2
        ),
        flush=True
    )

if __name__ == "__main__":
    main()
