# DAILY_DETAIL_WORKER_VERSION = 2
# Fresh bootstrap trigger: [fresh-bootstrap]
# Retry after bootstrap writer fix
import csv, json, os, re, time
from pathlib import Path
from datetime import datetime, timezone
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from scraper import (
    parse_detail,
    parse_tender_rows,
    parse_organisation_rows,
    get_all_tender_rows,
    request as portal_request,
    PORTAL,
    ORG_URL,
    HEADERS,
)

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
    page.wait_for_timeout(500)
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
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    result_soup = None
    result_rows = []
    match = None
    for _ in range(12):
        page.wait_for_timeout(300)
        result_soup = BeautifulSoup(page.content(), "html.parser")
        result_rows = parse_tender_rows(result_soup, page.url)
        match = next((r for r in result_rows if clean(r.get("tender_id")).casefold() == tender_id.casefold()), None)
        if match and clean(match.get("title")):
            break
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
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(700)
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

def rsp_style_extract_targets(target_rows, by_id, save_status, save_csv, save_detail_csv, success_ids):
    """RSP-style detail extraction.

    Uses one requests.Session, opens the MP portal/home first, discovers the
    live tender DirectLinks from the organisation pages, and parses the detail
    HTML directly with the same resilient RSP-derived parser. Session-bound
    DirectLink URLs are used only in memory and are never written to CSV.
    """
    target_ids = {clean(row.get("Tender ID")) for row in target_rows if clean(row.get("Tender ID"))}
    if not target_ids:
        return [], []

    session = requests.Session()
    session.headers.update(HEADERS)
    remaining = set(target_ids)
    extracted = []
    failed = []

    try:
        print("RSP-STYLE: opening MP Home and Organisation page...", flush=True)
        portal_request(session, PORTAL, retries=4, sleep=0.25)
        org_response = portal_request(session, ORG_URL, retries=4, sleep=0.25, referer=PORTAL)
        organisations = parse_organisation_rows(BeautifulSoup(org_response.text, "html.parser"), PORTAL)
        print(f"RSP-STYLE: organisations discovered = {len(organisations)}", flush=True)

        for org in organisations:
            if not remaining:
                break
            name = clean(org.get("name"))
            expected = int(org.get("count") or 0)
            org_url = clean(org.get("url"))
            if not org_url:
                continue
            try:
                tender_rows, pages = get_all_tender_rows(
                    session, org_url, expected
                )
                matches = [
                    row for row in tender_rows
                    if clean(row.get("Tender ID")) in remaining
                ]
                if matches:
                    print(
                        f"RSP-STYLE: {name} | portal={expected} | pages={pages} | "
                        f"target matches={len(matches)}",
                        flush=True
                    )
                for listing in matches:
                    tid = clean(listing.get("Tender ID"))
                    if not tid or tid not in remaining:
                        continue
                    try:
                        # This is the key RSP flow: GET the live DirectLink
                        # with the same requests session that discovered it.
                        response = portal_request(
                            session,
                            listing.get("url"),
                            retries=4,
                            sleep=0.20,
                            referer=org_url,
                        )
                        soup = BeautifulSoup(response.text, "html.parser")
                        detail = parse_detail(soup, PORTAL)
                        parsed_id = clean(detail.get("Tender ID"))
                        if parsed_id and parsed_id.casefold() != tid.casefold():
                            raise RuntimeError(
                                f"Detail Tender ID mismatch: expected {tid}, got {parsed_id}"
                            )
                        if not parsed_id:
                            body = clean(soup.get_text(" ", strip=True))
                            if tid.casefold() not in body.casefold():
                                raise RuntimeError(
                                    f"RSP detail page does not contain Tender ID {tid}"
                                )
                            detail["Tender ID"] = tid

                        base = by_id.get(tid, {})
                        for key in (
                            "Organisation", "Department", "Division", "Sub Division",
                        ):
                            if clean(base.get(key)) and len(clean(base.get(key))) >= len(clean(detail.get(key))):
                                detail[key] = base.get(key, "")
                        for key in (
                            "Title", "Reference Number", "Published Date",
                            "Closing Date", "Opening Date",
                        ):
                            if not clean(detail.get(key)):
                                detail[key] = base.get(key, "")

                        detail.pop("URL", None)
                        detail["Detail Extracted"] = "YES"
                        detail["Search Route"] = (
                            "RSP -> Home -> Organisation -> Live Tender Link -> Detail"
                        )
                        merged = dict(base)
                        merged.update(detail)
                        by_id[tid] = merged
                        remaining.remove(tid)
                        success_ids.add(tid)
                        extracted.append(tid)

                        if len(extracted) % 10 == 0:
                            save_csv()
                            save_detail_csv()
                        save_status("running", tid)
                        print(
                            f"RSP-STYLE DETAIL OK {tid} | remaining={len(remaining)}",
                            flush=True,
                        )
                    except Exception as exc:
                        failed.append((by_id.get(tid, {}), exc))
                        print(
                            f"RSP-STYLE DETAIL FAIL {tid}: "
                            f"{type(exc).__name__}: {exc}",
                            flush=True,
                        )
            except Exception as exc:
                print(
                    f"RSP-STYLE ORG FAIL {name}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
    finally:
        session.close()

    print(
        f"RSP-STYLE COMPLETE: extracted={len(extracted)} "
        f"remaining={len(remaining)}",
        flush=True,
    )
    # Anything still remaining goes to the existing Playwright search route.
    fallback = [by_id[tid] for tid in target_ids if tid in remaining and tid in by_id]
    return fallback, failed


def main():
    # The 20:15 recovery scraper run was already launched from an older
    # workflow and its detail child must not race the dedicated recovery job.
    # This guard is temporary and is removed after the current recovery.
    if os.environ.get("GITHUB_RUN_ID") == "35649935496":
        print("Skipping superseded detail child for workflow run 35649935496.", flush=True)
        return
    if not CSV.exists():
        raise RuntimeError("all_tenders_org_detailed.csv not found")

    with CSV.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    # By default extract only current portal IDs. For the explicit full
    # inventory backfill, include every Tender ID already copied to CSV so
    # the organisation snapshot's Pending count is fully processed.
    extract_all_inventory = os.environ.get("EXTRACT_ALL_INVENTORY", "0") == "1"
    current_ids = []
    if SNAPSHOT.exists():
        with SNAPSHOT.open(encoding="utf-8-sig", newline="") as sf:
            for sr in csv.DictReader(sf):
                tid = clean(sr.get("Tender ID"))
                if tid and tid not in current_ids:
                    current_ids.append(tid)
    if extract_all_inventory:
        current_ids = list(dict.fromkeys(
            clean(r.get("Tender ID")) for r in rows if clean(r.get("Tender ID"))
        ))
    current_set = set(current_ids)

    # Deduplicate by Tender ID before extraction. The organisation snapshot
    # can contain repeated rows for the same ID, but a detail page must be
    # opened only once per Tender ID.
    incomplete = []
    seen_incomplete = set()
    for row in rows:
        tid = clean(row.get("Tender ID"))
        if (
            tid
            and tid in current_set
            and clean(row.get("Detail Extracted")).upper() != "YES"
            and tid not in seen_incomplete
        ):
            seen_incomplete.add(tid)
            incomplete.append(row)
    targets = incomplete[:BATCH_SIZE] if BATCH_SIZE > 0 else incomplete
    skipped_ids = [
        clean(row.get("Tender ID")) for row in rows
        if clean(row.get("Tender ID")) in current_set
        and clean(row.get("Detail Extracted")).upper() == "YES"
    ]

    print(
        f"CSV IDs: {len(rows)} | current portal IDs: {len(current_ids)} | "
        f"new/incomplete current IDs: {len(incomplete)} | targets: {len(targets)}",
        flush=True
    )

    by_id = {clean(r.get("Tender ID")): r for r in rows if clean(r.get("Tender ID"))}
    errors = []
    failed_bases = []
    success = 0
    success_ids = set()
    final_failed_ids = set()

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
            "failed": len(final_failed_ids),
            "success_ids": sorted(success_ids),
            "failed_ids": sorted(final_failed_ids),
            "skipped_ids": sorted(set(skipped_ids)),
            "pending_ids": sorted(current_set - success_ids - final_failed_ids - set(skipped_ids)),
            "last_successful_tender_id": last_id,
            "errors": errors,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, indent=2), encoding="utf-8")

    def process_targets(page, target_rows, is_retry=False):
        nonlocal success
        save_every = 10
        for base in target_rows:
            tid = clean(base.get("Tender ID"))
            try:
                print(
                    f"DETAIL {'RETRY ' if is_retry else ''}START {tid}",
                    flush=True
                )
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
                success_ids.add(tid)

                # Save in small checkpoints instead of rewriting the full 5,000+
                # row CSV after every Tender ID.
                if success % save_every == 0:
                    save_csv()
                    save_detail_csv()
                save_status("running", tid)
                print(f"DETAIL OK {tid} — {'CHECKPOINT SAVED' if success % save_every == 0 else 'MEMORY SAVED'} — NEXT ID", flush=True)

            except Exception as e:
                failure = {
                    "Tender ID": tid,
                    "error": f"{type(e).__name__}: {e}",
                    "retry_pass": bool(is_retry)
                }
                errors.append(failure)
                if is_retry:
                    final_failed_ids.add(tid)
                if not is_retry:
                    failed_bases.append(base)
                print(
                    f"DETAIL {'RETRY ' if is_retry else ''}FAIL {tid}: "
                    f"{type(e).__name__}: {e}",
                    flush=True
                )
            # Reuse the same browser/page for the next Tender ID.

    save_status("running")

    # Primary route: the proven RSP-style requests/session extraction.
    # Keep the existing Playwright Tender-ID search route as a safe fallback
    # for only those IDs that the direct session route cannot resolve.
    rsp_fallback, rsp_failed = rsp_style_extract_targets(
        targets,
        by_id,
        save_status,
        save_csv,
        save_detail_csv,
        success_ids,
    )
    success = len(success_ids)

    for base, exc in rsp_failed:
        failed_bases.append(base)
        errors.append({
            "Tender ID": clean(base.get("Tender ID")),
            "error": f"RSPStyle: {type(exc).__name__}: {exc}",
            "retry_pass": False,
        })

    if rsp_fallback:
        print(
            f"PLAYWRIGHT FALLBACK: {len(rsp_fallback)} Tender IDs",
            flush=True,
        )
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                viewport={"width": 1366, "height": 900}
            )
            page = context.new_page()
            try:
                process_targets(page, rsp_fallback, is_retry=False)

                retry_targets = list(failed_bases)
                if retry_targets:
                    print(f"RETRY PASS START: {len(retry_targets)} IDs", flush=True)
                    process_targets(page, retry_targets, is_retry=True)

                    final_errors = {}
                    for e in errors:
                        final_errors[e["Tender ID"]] = e
                    errors[:] = list(final_errors.values())
            finally:
                context.close()
                browser.close()

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
