import csv
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

PORTAL = "https://www.mptenders.gov.in/nicgep/app"
ORG_URL = PORTAL + "?page=FrontEndTendersByOrganisation&service=page"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/153.0 Safari/537.36 MP-Tender-Monitor/2.0",
    "Accept-Language": "en-IN,en;q=0.9",
}
FIELDS = [
    "Tender ID", "Published Date", "Closing Date", "Opening Date",
    "Title", "Reference Number", "Organisation", "Department",
    "Division", "Sub Division", "PAC Amount", "EMD Fee",
    "Tender Fee", "Processing Fee", "Total Fee", "Status", "URL",
]
TENDER_ID_RE = re.compile(r"\b20\d{2}_[A-Z0-9]+_\d+_\d+\b", re.I)

# Staged rollout limit. Stage 1 now starts with organisations having <= 10 tenders.
MAX_ORG_TENDER_COUNT = int(os.getenv("MAX_ORG_TENDER_COUNT", "10"))


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def money_number(value):
    text = clean(value).replace(",", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def money_text(value):
    n = money_number(value)
    if not n:
        return ""
    return str(int(n)) if n.is_integer() else f"{n:.2f}"


def request(session, url, retries=3, sleep=1.0, referer=None):
    last = None
    request_headers = dict(HEADERS)
    if referer:
        request_headers["Referer"] = referer
    for attempt in range(retries):
        try:
            response = session.get(url, headers=request_headers, timeout=60, allow_redirects=True)
            response.raise_for_status()
            time.sleep(sleep)
            return response
        except Exception as exc:
            last = exc
            time.sleep(2 + attempt * 2)
    raise last


def absolute(base, href):
    return urljoin(base, href) if href else ""


def link_from_anchor(anchor, base):
    if not anchor:
        return ""
    href = anchor.get("href")
    if href:
        return absolute(base, href)
    onclick = anchor.get("onclick", "")
    match = re.search(r"""['"]((?:https?://|/)[^'"]+)['"]""", onclick)
    return absolute(base, match.group(1)) if match else ""


def find_value_pairs(soup):
    pairs = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            values = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(values) < 2:
                continue
            if len(values) == 2:
                pairs.setdefault(values[0].lower(), values[1])
            else:
                for i in range(0, len(values) - 1, 2):
                    if values[i]:
                        pairs.setdefault(values[i].lower(), values[i + 1])
    return pairs


def find_label_value(soup, label_patterns):
    patterns = [p.lower() for p in label_patterns]
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            texts = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            for i, text in enumerate(texts):
                if any(p in text.lower() for p in patterns) and i + 1 < len(texts) and texts[i + 1]:
                    return texts[i + 1]
    return ""


def find_critical_date(soup, label):
    target = label.lower()
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            texts = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            for i, text in enumerate(texts):
                if target in text.lower() and i + 1 < len(texts):
                    value = texts[i + 1]
                    if value and value.lower() != text.lower():
                        return value
    return ""


def parse_chain(chain):
    parts = [clean(x) for x in clean(chain).split("||") if clean(x)]
    return tuple(parts[i] if i < len(parts) else "" for i in range(4))


def parse_detail(soup, url):
    pairs = find_value_pairs(soup)
    chain = find_label_value(soup, ["Organisation Chain"]) or pairs.get("organisation chain", "")
    organisation, department, division, sub_division = parse_chain(chain)
    tender_id = find_label_value(soup, ["Tender ID"]) or pairs.get("tender id", "")
    reference = (
        find_label_value(soup, ["Tender Reference Number", "Tender Reference"])
        or pairs.get("tender reference number", "")
        or pairs.get("tender reference", "")
    )
    title = find_label_value(soup, ["Work /Item(s) Title", "Title"])
    work_description = find_label_value(soup, ["Work Description"])
    publish = find_critical_date(soup, "Publish Date")
    closing = find_critical_date(soup, "Bid Submission End Date")
    opening = find_critical_date(soup, "Bid Opening Date")
    if not title:
        title = work_description

    pac = find_label_value(soup, ["Tender Value in ₹", "Tender Value"])
    tender_fee = find_label_value(soup, ["Tender Fee in ₹"])
    processing_fee = find_label_value(soup, ["Processing Fee in ₹"])
    emd = find_label_value(soup, ["EMD Amount in ₹"])
    total_fee = money_number(pac) + money_number(emd) + money_number(tender_fee) + money_number(processing_fee)

    return {
        "Tender ID": clean(tender_id),
        "Published Date": clean(publish),
        "Closing Date": clean(closing),
        "Opening Date": clean(opening),
        "Title": clean(title),
        "Reference Number": clean(reference),
        "Organisation": organisation,
        "Department": department,
        "Division": division,
        "Sub Division": sub_division,
        "PAC Amount": money_text(pac),
        "EMD Fee": money_text(emd),
        "Tender Fee": money_text(tender_fee),
        "Processing Fee": money_text(processing_fee),
        "Total Fee": str(int(total_fee)) if total_fee.is_integer() else f"{total_fee:.2f}",
        "Status": "Open",
        "URL": url,
    }


def parse_organisation_rows(soup, base):
    result = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header_index = -1
        header_cells = []
        for idx, tr in enumerate(rows[:5]):
            cells = tr.find_all(["th", "td"])
            texts = [clean(c.get_text(" ", strip=True)).lower() for c in cells]
            joined = " ".join(texts)
            if "organisation name" in joined and "tender count" in joined:
                header_index = idx
                header_cells = texts
                break

        if header_index < 0:
            continue

        org_idx = next((i for i, h in enumerate(header_cells) if "organisation name" in h), 1)
        count_idx = next((i for i, h in enumerate(header_cells) if "tender count" in h), len(header_cells) - 1)

        for tr in rows[header_index + 1:]:
            cells = tr.find_all(["td", "th"])
            texts = [clean(c.get_text(" ", strip=True)) for c in cells]
            if len(cells) <= max(org_idx, count_idx):
                continue

            name = texts[org_idx] if org_idx < len(texts) else ""
            count_text = texts[count_idx] if count_idx < len(texts) else ""
            if not name or name.lower() in {"s.no", "organisation name", "tender count"}:
                continue

            count_match = re.search(r"\d[\d,]*", count_text)
            if not count_match:
                continue
            count = int(count_match.group(0).replace(",", ""))

            # The count itself is the organisation's DirectLink. Prefer that
            # anchor because the MP portal uses session-bound $DirectLink URLs.
            anchor = None
            for a in cells[count_idx].find_all("a"):
                if clean(a.get_text(" ", strip=True)).replace(",", "").isdigit():
                    anchor = a
                    break
            if not anchor:
                for a in cells[count_idx].find_all("a"):
                    if link_from_anchor(a, base):
                        anchor = a
                        break
            if not anchor:
                # Fallback: any link in the row.
                for cell in cells:
                    for a in cell.find_all("a"):
                        if link_from_anchor(a, base):
                            anchor = a
                            break
                    if anchor:
                        break

            href = link_from_anchor(anchor, base) if anchor else ""
            if href:
                result.append({"name": name, "count": count, "url": href})

        if result:
            result.sort(key=lambda x: (x["count"], x["name"].lower()))
            return result

    return result


def parse_tender_rows(soup, base):
    result = []
    navigation_text = {
        "next", "previous", "first", "last", "view", "details",
        "print", "download", "back", "clear", "search"
    }

    for table in soup.find_all("table"):
        header_text = " ".join(
            clean(x.get_text(" ", strip=True)).lower()
            for x in table.find_all(["th", "td"], limit=30)
        )
        rows = table.find_all("tr")
        # The MP portal sometimes renders the tender-list headers differently
        # on DirectLink pages. Accept a table when either its header identifies
        # a tender list OR one of its rows contains a real MP Tender ID.
        table_has_tender_id = any(
            TENDER_ID_RE.search(clean(tr.get_text(" ", strip=True)))
            for tr in rows
        )
        looks_like_tender_table = (
            ("tender" in header_text and
             ("closing" in header_text or "title" in header_text or "reference" in header_text))
            or table_has_tender_id
        )
        if not looks_like_tender_table:
            continue
        # Do not blindly skip the first row: DirectLink tender pages on the MP portal
        # can render the first tender row without a normal header row.
        for tr in rows:
            cells = tr.find_all(["td", "th"])
            texts = [clean(c.get_text(" ", strip=True)) for c in cells]
            if len(texts) < 2:
                continue

            full_text = " ".join(texts)
            tender_id_match = TENDER_ID_RE.search(full_text)
            tender_id = tender_id_match.group(0) if tender_id_match else ""

            # Prefer a session-bound DirectLink in the row. If there are several,
            # choose the one with meaningful title/reference text.
            candidates = []
            for a in tr.find_all("a"):
                href = link_from_anchor(a, base)
                text = clean(a.get_text(" ", strip=True))
                if not href or not text:
                    continue
                low = text.lower()
                if low in navigation_text or text.replace(",", "").isdigit():
                    continue
                candidates.append((a, href, text))

            if not candidates:
                continue

            # Title links are generally the longest meaningful text in the row.
            anchor, href, anchor_text = max(candidates, key=lambda x: len(x[2]))

            reference = ""
            ref_match = re.search(
                r"(?i)(?:ref(?:erence)?\.?\s*(?:no\.?|number)?\s*[:\-]?\s*)([^|]+)",
                full_text,
            )
            if ref_match:
                reference = clean(ref_match.group(1))

            # If the row's visible text contains the title, use the anchor text;
            # otherwise keep the full row text as a fallback title.
            title = clean(anchor_text) or clean(texts[1] if len(texts) > 1 else texts[0])

            result.append({
                "url": href,
                "tender_id": tender_id,
                "reference": reference,
                "title": title,
                "row_text": full_text,
            })

        if result:
            return result

    return result


def next_page_url(soup, base, current):
    for a in soup.find_all("a"):
        text = clean(a.get_text(" ", strip=True)).lower()
        title = clean(a.get("title", "")).lower()
        aria = clean(a.get("aria-label", "")).lower()
        if text in {"next", ">", "»", "next >"} or "next page" in title or "next page" in aria:
            href = link_from_anchor(a, base)
            if href and href != current:
                return href
    return ""


def get_all_tender_rows(session, start_url, expected_count):
    pages = []
    seen = set()
    current = start_url
    unique = {}

    for _ in range(500):
        if not current or current in seen:
            break
        seen.add(current)
        response = request(session, current, sleep=0.35, referer=ORG_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        rows = parse_tender_rows(soup, current)
        for row in rows:
            key = row["tender_id"] or row["reference"] or row["url"]
            unique[key] = row
        pages.append(current)
        if expected_count and len(unique) >= expected_count:
            break
        current = next_page_url(soup, current, current)

    return list(unique.values()), len(pages)



def browser_page(page, url, referer=None):
    """Open an MP portal page in a real browser session so JSF DirectLink
    navigation remains valid. The portal uses session-bound $DirectLink URLs."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(900)
    return BeautifulSoup(page.content(), "html.parser")


def browser_get_all_tender_rows(page, org, expected_count):
    """Open one organisation through the browser session and collect all tender rows."""
    org_soup = browser_page(page, ORG_URL)
    organisations = parse_organisation_rows(org_soup, page.url)
    target = next(
        (x for x in organisations
         if x["name"] == org["name"] and x["count"] == org["count"]),
        None,
    )
    if not target:
        # Fall back to name-only because counts can change while the job is running.
        target = next((x for x in organisations if x["name"] == org["name"]), None)
    if not target:
        raise RuntimeError(f"Organisation row not found in browser session: {org['name']}")

    unique = {}
    seen_urls = set()
    current = target["url"]
    pages = 0

    for _ in range(500):
        if not current or current in seen_urls:
            break
        seen_urls.add(current)
        soup = browser_page(page, current)
        rows = parse_tender_rows(soup, page.url)
        for row in rows:
            key = row["tender_id"] or row["reference"] or row["url"]
            unique[key] = row
        pages += 1

        if expected_count and len(unique) >= expected_count:
            break

        nxt = next_page_url(soup, page.url, page.url)
        if not nxt or nxt in seen_urls:
            break
        current = nxt

    return list(unique.values()), pages



def read_existing(csv_file):
    if not csv_file.exists():
        return []
    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(csv_file, rows):
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    temp = csv_file.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(csv_file)


def scrape_mp_tenders(csv_file):
    session = requests.Session()
    existing = read_existing(csv_file)
    existing_by_id = {clean(r.get("Tender ID")): r for r in existing if clean(r.get("Tender ID"))}
    existing_by_ref = {clean(r.get("Reference Number")): r for r in existing if clean(r.get("Reference Number"))}

    # Keep a requests session for the initial organisation list.
    # Actual organisation/tender navigation is then performed in Chromium because
    # MP Tender uses session-bound JSF DirectLink URLs.
    response = request(session, ORG_URL, sleep=0.5)
    soup = BeautifulSoup(response.text, "html.parser")
    organisations = parse_organisation_rows(soup, ORG_URL)
    if not organisations:
        raise RuntimeError("Organisation list could not be parsed from MP Tender portal.")

    rows_by_id = dict(existing_by_id)
    rows_without_id = {
        clean(r.get("Reference Number")): r for r in existing
        if not clean(r.get("Tender ID")) and clean(r.get("Reference Number"))
    }

    stats = {
        "organisations": len(organisations),
        "organisations_verified": 0,
        "organisations_skipped_count_mismatch": 0,
        "tender_listed": 0,
        "detail_opened": 0,
        "new_tenders": 0,
        "updated_records": 0,
        "organisations_skipped_stage_limit": 0,
        "stage_limit": MAX_ORG_TENDER_COUNT,
        "errors": [],
    }

    # Staged rollout: <=10, then <=50, <=100, <=200, <=400, <=600, and finally
    # above 600. Use one real Chromium session for the MP portal because its
    # organisation/tender links are session-bound JSF $DirectLink URLs.
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=HEADERS["User-Agent"],
            locale="en-IN",
            viewport={"width": 1920, "height": 1080},
        )
        try:
            for index, org in enumerate(organisations, 1):
                if MAX_ORG_TENDER_COUNT >= 0 and org["count"] > MAX_ORG_TENDER_COUNT:
                    stats["organisations_skipped_stage_limit"] += 1
                    continue

                try:
                    tender_rows, pages = browser_get_all_tender_rows(page, org, org["count"])

                    # Do not open detail pages unless the parsed list count exactly matches
                    # the count displayed on the organisation page.
                    if org["count"] and len(tender_rows) != org["count"]:
                        stats["organisations_skipped_count_mismatch"] += 1
                        stats["errors"].append(
                            f"{org['name']}: portal count {org['count']} != parsed {len(tender_rows)}"
                        )
                        continue

                    stats["organisations_verified"] += 1
                    stats["tender_listed"] += len(tender_rows)

                    for tender in tender_rows:
                        tid = tender["tender_id"]
                        ref = tender["reference"]
                        if tid and tid in existing_by_id:
                            continue
                        if ref and ref in existing_by_ref:
                            continue

                        detail_soup = browser_page(page, tender["url"])
                        record = parse_detail(detail_soup, page.url)
                        stats["detail_opened"] += 1

                        if not record["Tender ID"]:
                            record["Tender ID"] = tid
                        if not record["Reference Number"]:
                            record["Reference Number"] = ref
                        if not record["Title"]:
                            record["Title"] = tender["title"]

                        if record["Tender ID"]:
                            if record["Tender ID"] not in rows_by_id:
                                stats["new_tenders"] += 1
                            rows_by_id[record["Tender ID"]] = record
                        elif record["Reference Number"]:
                            rows_without_id[record["Reference Number"]] = record

                except Exception as exc:
                    stats["errors"].append(f"{org['name']}: {type(exc).__name__}: {exc}")
        finally:
            browser.close()

    final_rows = list(rows_by_id.values()) + list(rows_without_id.values())
    now = datetime.now()
    for row in final_rows:
        closing = clean(row.get("Closing Date"))
        try:
            dt = datetime.strptime(closing.split(" ")[0], "%d-%b-%Y")
            row["Status"] = "Closed" if dt.date() < now.date() else "Open"
        except Exception:
            pass

    # Keep archived tenders for 10 days after Closing Date, then remove them.
    retention_cutoff = datetime.now().date().fromordinal(
        datetime.now().date().toordinal() - 10
    )
    retained_rows = []
    for row in final_rows:
        closing_text = clean(row.get("Closing Date"))
        try:
            closing_date = datetime.strptime(
                closing_text.split(" ")[0], "%d-%b-%Y"
            ).date()
            if closing_date < retention_cutoff:
                continue
        except Exception:
            # Do not delete a record when its closing date cannot be parsed.
            pass
        retained_rows.append(row)

    final_rows = retained_rows
    final_rows.sort(key=lambda r: clean(r.get("Closing Date")))
    write_csv(csv_file, final_rows)

    return {
        "ok": True,
        "source": ORG_URL,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "total_records": len(final_rows),
        "stats": stats,
        "message": f"Staged MP Tender scrape completed for organisations with Tender Count <= {MAX_ORG_TENDER_COUNT}.",
    }


if __name__ == "__main__":
    target = Path(os.getenv(
        "CSV_FILE",
        Path(__file__).resolve().parent.parent / "all_tenders_org_detailed.csv",
    ))
    print(scrape_mp_tenders(target))
