import csv
import os
import re
import time
import random
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
    "Tender Fee", "Processing Fee", "Total Fee", "Location", "Pincode", "Status", "URL",
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
    # Preserve delimiter positions exactly:
    # Organisation || Department || Division || Sub Division
    parts = [clean(x) for x in clean(chain).split("||")]
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
    location = find_label_value(soup, ["Location"])
    pincode = find_label_value(soup, ["Pincode", "PIN Code", "Pin Code"])
    # Dashboard Total Fee = Tender Fee + EMD + Processing Fee.
    # Robust fallback: the MP portal often renders label + value in the same
    # table cell, so cell-pair parsing alone can miss these fields.
    body_text = clean(soup.get_text(" ", strip=True))

    def between(label, stop_labels):
        match = re.search(re.escape(label) + r"\s*(.*?)\s*(?:" + "|".join(re.escape(x) for x in stop_labels) + r"|$)", body_text, re.I)
        return clean(match.group(1)) if match else ""

    if not chain:
        chain = between("Organisation Chain", ["Tender Reference Number", "Tender ID"])
        organisation, department, division, sub_division = parse_chain(chain)
    if not tender_id:
        match = re.search(r"\b20\d{2}_[A-Z0-9]+_\d+_\d+\b", body_text, re.I)
        tender_id = match.group(0) if match else ""
    if not reference:
        reference = between("Tender Reference Number", ["Tender ID", "Withdrawal Allowed"])
    if not title:
        title = between("Work /Item(s) Title", ["Work Description", "Pre Qualification Details"])
    if not publish:
        publish = between("Publish Date", ["Bid Opening Date", "Document Download / Sale Start Date"])
    if not closing:
        closing = between("Bid Submission End Date", ["Financial Bid Opening Date", "Document Documents", "Tender Documents"])
    if not opening:
        opening = between("Bid Opening Date", ["Document Download / Sale Start Date", "Document Download / Sale End Date"])
    if not pac:
        pac = between("Tender Value in ₹", ["Product Category", "Sub category", "Contract Type"])
    if not tender_fee:
        tender_fee = between("Tender Fee in ₹", ["Processing Fee in ₹", "Fee Payable To"])
    if not processing_fee:
        processing_fee = between("Processing Fee in ₹", ["Fee Payable To", "Fee Payable At"])
    if not emd:
        emd = between("EMD Amount in ₹", ["EMD Exemption Allowed", "EMD Fee Type"])
    if not location:
        location = between("Location", ["Pincode", "Pre Bid Meeting Place"])
    if not pincode:
        pin_match = re.search(r"\bPincode\s+([0-9]{6})\b", body_text, re.I)
        pincode = pin_match.group(1) if pin_match else ""
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

            # A real tender-list row must contain a Tender ID. The MP portal
            # places navigation/accessibility links in or around the same
            # tables; accepting rows without an ID creates false records such
            # as "Tenders by Closing Date" and "Screen Reader Access".
            if not tender_id:
                continue

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

            # MP Tender list links commonly render as:
            # [Title] [Reference Number] [Tender ID]
            # while the Tender ID may also be visible elsewhere in the row.
            # Capture these bracketed values directly so the dashboard gets
            # the real Reference Number instead of an empty field.
            bracket_values = [
                clean(x)
                for x in re.findall(r"\[([^\]]+)\]", full_text)
                if clean(x)
            ]

            bracket_tender_id = next(
                (x for x in bracket_values if TENDER_ID_RE.fullmatch(x)),
                "",
            )
            if bracket_tender_id:
                tender_id = bracket_tender_id

            non_id_brackets = [
                x for x in bracket_values
                if not TENDER_ID_RE.fullmatch(x)
            ]

            title = (
                non_id_brackets[0]
                if non_id_brackets
                else clean(anchor_text)
            )

            reference = (
                non_id_brackets[1]
                if len(non_id_brackets) >= 2
                else ""
            )

            # Fallback for rows whose portal markup does not use the
            # [Title] [Reference] [Tender ID] pattern.
            if not reference:
                ref_match = re.search(
                    r"(?i)(?:ref(?:erence)?\.?\s*(?:no\.?|number)?\s*[:\-]?\s*)([^|]+)",
                    full_text,
                )
                if ref_match:
                    reference = clean(ref_match.group(1))

            title = clean(title).strip("[]")
            reference = clean(reference).strip("[]")
            tender_id = clean(tender_id).strip("[]")

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
    page.wait_for_timeout(2000)
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



def run_detail_validation(csv_file, organisations):
    """Validate exactly N detail pages using the SAME requests session that
    created the JSF DirectLink URLs. This keeps session-bound links valid."""
    target_name = clean(os.getenv("DETAIL_ORGANISATION", "Directorate Sports and Youth Welfare"))
    sample_size = int(os.getenv("DETAIL_SAMPLE_SIZE", "9"))
    output_file = csv_file.parent / os.getenv("DETAIL_OUTPUT_FILE", "detail_validation.csv")

    target_org = next(
        (org for org in organisations
         if clean(org.get("name")).casefold() == target_name.casefold()),
        None,
    )
    if not target_org:
        raise RuntimeError(f"DETAIL_ORGANISATION not found: {target_name}")

    session = requests.Session()
    results = []
    errors = []

    # Establish the same portal session, then create the organisation's
    # DirectLink URLs and immediately consume those URLs with that session.
    request(session, ORG_URL, sleep=0.5)
    fresh_rows, pages = get_all_tender_rows(
        session, target_org["url"], target_org["count"]
    )
    candidates = [
        row for row in fresh_rows
        if clean(row.get("tender_id")) and clean(row.get("url"))
    ]
    if len(candidates) < sample_size:
        raise RuntimeError(
            f"Only {len(candidates)} live tenders found for {target_name}; expected {sample_size}."
        )

    selected = candidates[:sample_size]

    # Debug the first live tender row structure once so we can bind to the
    # portal's actual JSF link instead of guessing at its href.
    try:
        debug_response = request(session, target_org["url"], sleep=0.2)
        debug_soup = BeautifulSoup(debug_response.text, "html.parser")
        first = selected[0]
        needles = [clean(first.get("tender_id")), clean(first.get("title")), clean(first.get("reference"))]
        snippets = []
        for a in debug_soup.find_all("a"):
            raw = str(a)
            if any(n and n.casefold() in raw.casefold() for n in needles):
                snippets.append(raw[:5000])
                if len(snippets) >= 5:
                    break
        print("DETAIL ANCHOR DEBUG:", " || ".join(snippets))
    except Exception as exc:
        print("DETAIL ANCHOR DEBUG ERROR:", type(exc).__name__, exc)

    for idx, tender in enumerate(selected, 1):
        try:
            response = request(
                session,
                tender["url"],
                sleep=0.8,
                referer=target_org["url"],
            )
            soup = BeautifulSoup(response.text, "html.parser")
            body = clean(soup.get_text(" ", strip=True))

            tender_id = clean(tender.get("tender_id"))
            # Reject navigation/home/list pages before parsing.
            if (
                tender_id.casefold() not in body.casefold()
                and not re.search(r"(?i)Tender Reference Number|EMD Amount in|Tender Fee in|Organisation Chain", body)
            ):
                raise RuntimeError(
                    f"Detail URL returned non-detail page: {response.url}"
                )

            detail = parse_detail(soup, response.url)
            list_dates = parse_list_dates(tender)
            detail["Tender ID"] = clean(detail.get("Tender ID")) or tender_id
            detail["Title"] = clean(detail.get("Title")) or clean(tender.get("title"))
            detail["Reference Number"] = clean(detail.get("Reference Number")) or clean(tender.get("reference"))
            detail["Published Date"] = clean(detail.get("Published Date")) or list_dates[0]
            detail["Closing Date"] = clean(detail.get("Closing Date")) or list_dates[1]
            detail["Opening Date"] = clean(detail.get("Opening Date")) or list_dates[2]
            detail["Organisation"] = target_org["name"]
            detail["URL"] = response.url

            results.append(detail)
            print(
                f"DETAIL VALIDATION {idx}/{sample_size}: "
                f"{detail.get('Tender ID')} | ref={detail.get('Reference Number')} | "
                f"PAC={detail.get('PAC Amount')} | EMD={detail.get('EMD Fee')} | "
                f"Fee={detail.get('Tender Fee')} | Processing={detail.get('Processing Fee')} | "
                f"Total={detail.get('Total Fee')} | Location={detail.get('Location')} | "
                f"Pincode={detail.get('Pincode')}"
            )
        except Exception as exc:
            errors.append(f"{tender.get('tender_id')}: {type(exc).__name__}: {exc}")

    write_csv(output_file, results)
    if len(results) != sample_size:
        raise RuntimeError(
            f"Detail validation incomplete: {len(results)}/{sample_size} succeeded. "
            + (" | ".join(errors) if errors else "")
        )

    # Never replace the dashboard with the 9-test sample. Only enrich existing
    # matching Tender IDs; the scheduled full scrape owns the complete dataset.
    main_rows = read_existing(csv_file)
    by_id = {
        clean(row.get("Tender ID")): dict(row)
        for row in main_rows
        if clean(row.get("Tender ID"))
    }
    for detail in results:
        key = clean(detail.get("Tender ID"))
        if key in by_id:
            by_id[key].update(detail)
    write_csv(csv_file, list(by_id.values()))

    print(
        f"DETAIL VALIDATION COMPLETE: {len(results)}/{sample_size} tenders, "
        f"organisation={target_name}, pages={pages}"
    )
    return {
        "ok": True,
        "detail_validation": len(results),
        "organisation": target_name,
        "output": str(output_file),
        "errors": errors,
    }


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

    if os.getenv("DETAIL_VALIDATION_ONLY") == "1":
        return run_detail_validation(csv_file, organisations)

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
                    tender_rows, pages = get_all_tender_rows(
                        session, org["url"], org["count"]
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

    # Build/refresh the main detailed CSV from the complete tender-list collection.
    # Existing detail fields are preserved; the 9-tender validation is run separately
    # with DETAIL_VALIDATION_ONLY=1 so it can never replace the dashboard dataset.
    existing_detail_rows = read_existing(csv_file)
    existing_by_id = {
        clean(row.get("Tender ID")): row
        for row in existing_detail_rows
        if clean(row.get("Tender ID"))
    }
    merged_rows = []
    for row in tender_list_rows:
        tender_id = clean(row.get("Tender ID"))
        old = dict(existing_by_id.get(tender_id, {}))
        base = {
            "Tender ID": tender_id,
            "Published Date": clean(row.get("Published Date")),
            "Closing Date": clean(row.get("Closing Date")),
            "Opening Date": clean(row.get("Opening Date")),
            "Title": clean(row.get("Title")),
            "Reference Number": clean(row.get("Reference Number")),
            "Organisation": clean(row.get("Organisation Name")),
            "Department": clean(old.get("Department")),
            "Division": clean(old.get("Division")),
            "Sub Division": clean(old.get("Sub Division")),
            "PAC Amount": clean(old.get("PAC Amount")),
            "EMD Fee": clean(old.get("EMD Fee")),
            "Tender Fee": clean(old.get("Tender Fee")),
            "Processing Fee": clean(old.get("Processing Fee")),
            "Total Fee": clean(old.get("Total Fee")),
            "Location": clean(old.get("Location")),
            "Pincode": clean(old.get("Pincode")),
            "Status": clean(old.get("Status")) or "Open",
            "URL": clean(row.get("Tender URL")),
        }
        merged_rows.append(base)
    write_csv(csv_file, merged_rows)

    return {
        "ok": True,
        "source": ORG_URL,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "organisation_records": len(org_rows),
        "tender_list_records": len(tender_list_rows),
        "total_records": 0,
        "stats": stats,
        "message": (
            f"Organisation list and organisation-level tender lists were collected. "
            f"Random detail sample opened: {stats['detail_opened']}."
        ),
    }

if __name__ == "__main__":
    target = Path(os.getenv(
        "CSV_FILE",
        Path(__file__).resolve().parent.parent / "all_tenders_org_detailed.csv",
    ))
    print(scrape_mp_tenders(target))
