"""Copy Home's latest 10 tenders + 10 corrigenda, then enrich within 12 min.

Only links obtained in the current live session are opened. Reference numbers
are matched exactly; Tender ID from the opened page is the final merge key.
"""
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from checks_cutoff import ChecksClosed, ensure_open, seconds_left
import requests
from bs4 import BeautifulSoup
from scraper import PORTAL, HEADERS, clean, parse_detail, parse_tender_rows, write_csv, TENDER_ID_RE
from inventory_summary import detail_complete
from publish_data_checkpoint import publish

MASTER = Path("all_tenders_org_detailed.csv")
STATUS = Path("data/home_latest_status.json")
QUEUE = Path("data/home_latest_queue.json")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def reference_key(value):
    # Keep punctuation: A/12 and A-12 can be different tenders.
    return clean(value).casefold()

def home_entries(soup):
    result = []
    for table_id, kind in (("activeTenders", "tender"), ("activeCorrigendums", "corrigendum")):
        table = soup.find("table", id=table_id)
        if table is None:
            raise RuntimeError(f"Home table missing: {table_id}")
        for tr in table.find_all("tr")[:10]:
            cells = tr.find_all("td", recursive=False)
            if len(cells) < 4 or cells[0].find("a", href=True) is None:
                continue
            anchor = cells[0].find("a", href=True)
            result.append({"kind": kind, "reference": clean(cells[1].get_text()),
                           "title": clean(anchor.get_text()),
                           "closing": clean(cells[2].get_text()),
                           "opening": clean(cells[3].get_text())})
    return result

def matching_row(entry, rows):
    candidates = [row for row in rows
                  if reference_key(row.get("Reference Number") or row.get("Tender Reference Number"))
                  == reference_key(entry["reference"])]
    if len(candidates) == 1:
        return candidates[0]
    if entry["kind"] == "tender":
        title = clean(entry["title"]).split(". ", 1)[-1].casefold()
        candidates = [row for row in candidates if clean(row.get("Title")).casefold() == title]
        if len(candidates) == 1:
            return candidates[0]
    return None  # Ambiguous references must be resolved by opening the live link.

def main():
    if seconds_left() <= 0:
        print('HOME: after 18:30 IST; skipped without changing data.')
        return
    deadline = time.monotonic() + min(720, int(os.getenv("HOME_LATEST_BUDGET_SECONDS", "720")))
    status = {"status": "running", "started_at": now_iso(), "budget_seconds": 720,
              "success_ids": [], "unchanged_ids": [], "errors": [], "pending": []}
    rows = list(csv.DictReader(MASTER.open(encoding="utf-8-sig", newline=""))) if MASTER.exists() else []
    by_id = {clean(row.get("Tender ID")): row for row in rows if clean(row.get("Tender ID"))}
    session = requests.Session()
    session.headers.update(HEADERS)
    def get(url, referer=PORTAL):
        ensure_open()
        remaining = min(deadline - time.monotonic(), seconds_left())
        if remaining <= 1:
            raise TimeoutError("12-minute extraction budget reached")
        response = session.get(url, headers={"Referer": referer}, timeout=min(30, remaining))
        ensure_open()
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser"), response.url
    def checkpoint():
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        status["updated_at"] = now_iso()
        STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        write_csv(MASTER, list(by_id.values()))
    def search_detail(tid):
        home, url = get(PORTAL)
        form = home.find("form", id="tenderSearch")
        if form is None:
            raise RuntimeError("Home Tender ID search form missing")
        payload = {i["name"]: i.get("value", "")
                   for i in form.find_all("input", attrs={"name": True})}
        payload["SearchDescription"] = tid
        remaining = min(deadline - time.monotonic(), seconds_left())
        if remaining <= 1:
            raise TimeoutError("12-minute extraction budget reached")
        ensure_open()
        response = session.post(urljoin(url, form.get("action", PORTAL)), data=payload,
                                headers={"Referer": url}, timeout=min(30, remaining))
        ensure_open()
        response.raise_for_status()
        result = BeautifulSoup(response.text, "html.parser")
        listing = next((r for r in parse_tender_rows(result, response.url)
                        if clean(r.get("tender_id")) == tid), None)
        if listing is None:
            raise RuntimeError(f"Tender ID search returned no exact match: {tid}")
        # Only a link in the result row containing this exact ID is valid.
        for tr in result.find_all("tr"):
            if tid not in tr.get_text() or tr.find("table"):
                continue
            anchor = next((a for a in tr.find_all("a", href=True)
                           if clean(listing.get("title")).casefold() in clean(a.get_text()).casefold()), None)
            if anchor:
                return get(urljoin(response.url, anchor["href"]), response.url)[0]
        raise RuntimeError(f"Actual title link missing for {tid}")
    seen = set()
    try:
        home, _ = get(PORTAL)
        entries = home_entries(home)
        status["copied_at"] = now_iso()
        status["latest_tenders"] = sum(e["kind"] == "tender" for e in entries)
        status["latest_corrigenda"] = sum(e["kind"] == "corrigendum" for e in entries)
        # Persist the copy before extraction; never persist session URLs.
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        QUEUE.write_text(json.dumps({"copied_at": status["copied_at"], "entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
        checkpoint()
        for position, entry in enumerate(entries):
            if time.monotonic() >= deadline - 1:
                status["pending"].extend(entries[position:])
                break
            try:
                ensure_open()
                prior = matching_row(entry, list(by_id.values()))
                if (entry["kind"] == "tender" and prior and detail_complete(prior)
                        and clean(prior.get("Closing Date")) == entry["closing"]
                        and clean(prior.get("Opening Date")) == entry["opening"]):
                    status["unchanged_ids"].append(prior["Tender ID"])
                    continue
                # Refresh Home for a fresh session-bound link each time.
                home, home_url = get(PORTAL)
                table = home.find("table", id="activeTenders" if entry["kind"] == "tender" else "activeCorrigendums")
                anchor = None
                for tr in table.find_all("tr") if table else []:
                    cells = tr.find_all("td", recursive=False)
                    if (len(cells) >= 4 and reference_key(cells[1].get_text()) == reference_key(entry["reference"])
                            and clean(cells[0].get_text()) == entry["title"]):
                        anchor = cells[0].find("a", href=True)
                        break
                if anchor is None:
                    raise RuntimeError("Entry moved off Home; retry next cycle")
                soup, url = get(urljoin(home_url, anchor["href"]), home_url)
                initial = parse_detail(soup, PORTAL)
                tid = clean(initial.get("Tender ID"))
                if not TENDER_ID_RE.fullmatch(tid):
                    raise RuntimeError("Live page did not identify a Tender ID")
                if reference_key(initial.get("Reference Number")) != reference_key(entry["reference"]):
                    raise RuntimeError("Detail reference differs from Home reference")
                base = dict(by_id.get(tid, {}))
                if entry["kind"] == "corrigendum" and tid not in seen:
                    # CorrViewDetails omits fees; its View More link opens
                    # history, so use the permanent ID to open the full tender.
                    soup = search_detail(tid)
                ensure_open()
                detail = parse_detail(soup, PORTAL)
                if clean(detail.get("Tender ID")) != tid:
                    raise RuntimeError("Full detail Tender ID differs from live entry")
                if tid not in seen:
                    detail.pop("URL", None)
                    base.update(detail)
                    base["Tested At"] = now_iso()
                    base["Search Route"] = "Home latest -> exact reference -> live link -> full detail"
                    base["Detail Extracted"] = "YES" if detail_complete({**base, "Detail Extracted": "YES"}) else ""
                    by_id[tid] = base
                    seen.add(tid)
                # Always take the revised dates from CorrViewDetails/Home.
                base = by_id[tid]
                base["Closing Date"] = entry["closing"]
                base["Opening Date"] = entry["opening"]
                if entry["kind"] == "corrigendum":
                    base["Corrigendum"] = entry["title"]
                    base["Corrigendum Last Checked"] = now_iso()
                if detail_complete(base):
                    if tid not in status["success_ids"]:
                        status["success_ids"].append(tid)
                else:
                    status["pending"].append(entry)
                checkpoint()
                print(f"HOME DETAIL {tid}: complete={detail_complete(base)}", flush=True)
            except ChecksClosed:
                status['pending'].extend(entries[position:])
                break
            except Exception as exc:
                status["errors"].append({"reference": entry["reference"], "kind": entry["kind"], "error": str(exc)})
                status["pending"].append(entry)
                checkpoint()
        status["status"] = "completed" if not status["pending"] else "partial"
    except ChecksClosed:
        status['status'] = 'stopped_at_cutoff'
    except Exception as exc:
        status["status"] = "failed"
        status["errors"].append({"error": str(exc)})
    finally:
        session.close()
        checkpoint()
    if os.getenv("GITHUB_ACTIONS") == "true":
        publish([str(MASTER), str(QUEUE), str(STATUS)])
    print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)
    if status["status"] == "failed":
        raise RuntimeError("Home discovery failed; see home_latest_status.json")

if __name__ == "__main__":
    main()
