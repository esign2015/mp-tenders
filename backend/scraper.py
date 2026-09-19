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
    "Tender Fee", "Processing Fee", "Total Fee", "Pincode", "Status", "URL",
]
ORG_FIELDS = ["S.No.", "Organisation Name", "Tender Count", "Portal URL", "Retrieved At"]
ORG_TENDER_FIELDS = [
    "S.No.", "Organisation Name", "Portal Tender Count", "Copied Tender Count",
    "Count Status", "Tender ID", "Title", "Reference Number",
    "Published Date", "Closing Date", "Opening Date", "Tender URL", "Raw Row"
]
TENDER_ID_RE = re.compile(r"\b20\d{2}_[A-Z0-9]+_\d+_\d+\b", re.I)


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
    pincode = find_label_value(soup, ["Pincode", "PIN Code", "Pin Code"])
    # Dashboard Total Fee = Tender Fee + EMD + Processing Fee.
    total_fee = money_number(tender_fee) + money_number(emd) + money_number(processing_fee)

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
        "Pincode": re.sub(r"\D", "", clean(pincode))[:6],
        "Status": "Open",
        "URL": url,
    }


def parse_organisation_rows(soup, base):
    """Parse the MP organisation table by its actual row structure.

    The portal's organisation name is plain text while the tender count is
    the clickable DirectLink. Do not rely on fixed column indexes because
    the JSF markup can contain hidden/extra cells.
    """
    result = []
    seen = set()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        # Identify only the real organisation table.
        header_index = -1
        for idx, tr in enumerate(rows[:10]):
            header_text = clean(tr.get_text(" ", strip=True)).lower()
            if "organisation name" in header_text and "tender count" in header_text:
                header_index = idx
                break
        if header_index < 0:
            continue

        for tr in rows[header_index + 1:]:
            cells = tr.find_all("td", recursive=False)
            if len(cells) < 3:
                # Fall back to direct th/td children when the markup differs.
                cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) < 3:
                continue

            cell_texts = [clean(c.get_text(" ", strip=True)) for c in cells]

            # S.No. must be a plain numeric value. This prevents headers,
            # pager rows and unrelated nested tables from being counted.
            sno_match = re.fullmatch(r"\d+", cell_texts[0].replace(",", ""))
            if not sno_match:
                continue
            sno = int(sno_match.group(0))

            # The tender-count cell contains a numeric DirectLink anchor.
            count_anchor = None
            count_cell = None
            count = None
            for cell in cells:
                for a in cell.find_all("a"):
                    anchor_text = clean(a.get_text(" ", strip=True))
                    if re.fullmatch(r"\d[\d,]*", anchor_text):
                        count_anchor = a
                        count_cell = cell
                        count = int(anchor_text.replace(",", ""))
                        break
                if count_anchor:
                    break

            if count_anchor is None or count_cell is None:
                continue

            # The organisation name is the cell immediately before the
            # clickable tender-count cell. This is stable on the MP portal
            # and avoids the previous fixed-index parsing error.
            try:
                org_cell = count_cell.find_previous_sibling("td")
            except Exception:
                org_cell = None

            name = clean(org_cell.get_text(" ", strip=True)) if org_cell else ""

            # If the portal adds an extra wrapper cell, use the nearest
            # preceding non-numeric cell as a safe fallback.
            if not name or name.isdigit():
                count_pos = cells.index(count_cell)
                for pos in range(count_pos - 1, 0, -1):
                    candidate = clean(cells[pos].get_text(" ", strip=True))
                    if candidate and not re.fullmatch(r"\d[\d,]*", candidate):
                        name = candidate
                        break

            if not name or name.lower() in {
                "s.no", "organisation name", "tender count"
            }:
                continue

            href = link_from_anchor(count_anchor, base)
            if not href:
                continue

            key = (sno, name, count, href)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "sno": sno,
                "name": name,
                "count": count,
                "url": href,
            })

        if result:
            # Preserve the portal's S.No. order; never sort by tender count.
            result.sort(key=lambda x: x["sno"])
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


def write_list_csv(csv_file, fieldnames, rows):
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    temp = csv_file.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(csv_file)


def parse_list_dates(tender):
    """Best-effort extraction of dates from a tender-list row.
    The raw row is always retained, so no list information is lost."""
    text = tender.get("row_text", "")
    dates = re.findall(
        r"\b\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?\b",
        text,
        flags=re.I,
    )
    return (
        dates[0] if len(dates) > 0 else "",
        dates[1] if len(dates) > 1 else "",
        dates[2] if len(dates) > 2 else "",
    )


def scrape_mp_tenders(csv_file):
    """
    Current requested stage (full organisation list + tender lists; detail pages later):
    1) Save the complete Organisation/Department list and portal Tender Count.
    2) Open every organisation one-by-one in the same Chromium session.
    3) Save only the tender-list rows for each organisation.
    4) Do NOT open individual tender detail pages yet.
    """
    session = requests.Session()

    response = request(session, ORG_URL, sleep=0.5)
    soup = BeautifulSoup(response.text, "html.parser")
    organisations = parse_organisation_rows(soup, ORG_URL)
    if not organisations:
        raise RuntimeError("Organisation list could not be parsed from MP Tender portal.")

    retrieved_at = datetime.now(timezone.utc).isoformat()

    # Save the complete organisation list first.
    org_rows = []
    for index, org in enumerate(organisations, 1):
        org_rows.append({
            "S.No.": index,
            "Organisation Name": org["name"],
            "Tender Count": org["count"],
            "Portal URL": org["url"],
            "Retrieved At": retrieved_at,
        })

    org_csv = csv_file.parent / "organisations.csv"
    tender_list_csv = csv_file.parent / "organisation_tenders.csv"
    write_list_csv(org_csv, ORG_FIELDS, org_rows)

    tender_list_rows = []
    stats = {
        "organisations": len(organisations),
        "organisations_opened": 0,
        "organisations_verified": 0,
        "organisations_count_mismatch": 0,
        "tender_listed": 0,
        "detail_opened": 0,
        "new_tenders": 0,
        "updated_records": 0,
        "errors": [],
    }

    # One Chromium session is used throughout because the portal uses
    # session-bound JSF $DirectLink URLs.
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=HEADERS["User-Agent"],
            locale="en-IN",
            viewport={"width": 1920, "height": 1080},
        )
        try:
            for index, org in enumerate(organisations, 1):
                try:
                    stats["organisations_opened"] += 1
                    tender_rows, pages = browser_get_all_tender_rows(
                        page, org, org["count"]
                    )

                    copied_count = len(tender_rows)
                    count_match = copied_count == org["count"]
                    if count_match:
                        stats["organisations_verified"] += 1
                    else:
                        stats["organisations_count_mismatch"] += 1

                    stats["tender_listed"] += copied_count

                    # Save every tender-list row. No tender detail page is opened.
                    for tender in tender_rows:
                        published, closing, opening = parse_list_dates(tender)
                        tender_list_rows.append({
                            "S.No.": len(tender_list_rows) + 1,
                            "Organisation Name": org["name"],
                            "Portal Tender Count": org["count"],
                            "Copied Tender Count": copied_count,
                            "Count Status": "MATCH" if count_match else "MISMATCH",
                            "Tender ID": tender.get("tender_id", ""),
                            "Title": tender.get("title", ""),
                            "Reference Number": tender.get("reference", ""),
                            "Published Date": published,
                            "Closing Date": closing,
                            "Opening Date": opening,
                            "Tender URL": tender.get("url", ""),
                            "Raw Row": tender.get("row_text", ""),
                        })

                    # Persist after every organisation so a long run keeps
                    # previously collected list data.
                    write_list_csv(
                        tender_list_csv, ORG_TENDER_FIELDS, tender_list_rows
                    )

                except Exception as exc:
                    stats["errors"].append(
                        f"{org['name']}: {type(exc).__name__}: {exc}"
                    )
        finally:
            browser.close()

    # The old detailed CSV stays untouched at this stage.
    if not csv_file.exists():
        write_csv(csv_file, [])

    return {
        "ok": True,
        "source": ORG_URL,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "organisation_records": len(org_rows),
        "tender_list_records": len(tender_list_rows),
        "total_records": 0,
        "stats": stats,
        "message": (
            "Organisation list and organisation-level tender lists were collected. "
            "Individual tender detail pages were intentionally not opened."
        ),
    }

if __name__ == "__main__":
    target = Path(os.getenv(
        "CSV_FILE",
        Path(__file__).resolve().parent.parent / "all_tenders_org_detailed.csv",
    ))
    print(scrape_mp_tenders(target))
