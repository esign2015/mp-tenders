# [run-scrape-details] start fresh 100-record detail batch after restoring verified tender list
# [run-scrape-details] resume after checkpoint-safe workflow
# [run-scrape-details] accelerated detail batch + list-level hierarchy
# [run-scrape-details] resume from recovered dataset and continue new detail batch
import csv
import json
import os
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

# [run-scrape-details] retry real session-bound detail extraction
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
    "Tender Fee", "Processing Fee", "Total Fee", "Location", "Pincode",
    "Work Description", "Product Category", "Sub Category", "Contract Type",
    "Bid Validity", "Pre Qualification Details",
    "Bid Submission Start Date", "Bid Submission End Date",
    "Bid Opening Date", "Document Download Start Date", "Document Download End Date",
    "Fee Payable To", "Fee Payable At", "Status", "Detail Extracted",
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
    """Preserve portal NA/zero values instead of turning them into blanks."""
    raw = clean(value)
    if not raw:
        return ""
    if raw.casefold() in {"na", "n/a", "not applicable", "-", "nil"}:
        return "NA"
    n = money_number(raw)
    if n == 0 and not re.search(r"\d", raw):
        return "0"
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
    # MP Tender portal Organisation Chain is normally:
    # Organisation || Department || Division || Sub Division
    # Some portal views render the same hierarchy with >> separators.
    text = clean(chain)
    if "||" in text:
        parts = [clean(x) for x in text.split("||")]
    elif ">>" in text:
        parts = [clean(x) for x in text.split(">>")]
    elif " > " in text:
        parts = [clean(x) for x in text.split(" > ")]
    else:
        parts = [text] if text else []
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
    publish = (
        find_critical_date(soup, "Published Date")
        or find_critical_date(soup, "Publish Date")
    )
    closing = find_critical_date(soup, "Bid Submission End Date")
    opening = find_critical_date(soup, "Bid Opening Date")
    bid_submission_start = find_critical_date(soup, "Bid Submission Start Date")
    bid_submission_end = find_critical_date(soup, "Bid Submission End Date")
    document_start = find_critical_date(soup, "Document Download / Sale Start Date")
    document_end = find_critical_date(soup, "Document Download / Sale End Date")
    if not title:
        title = work_description

    pac = find_label_value(soup, ["Tender Value in ₹", "Tender Value"])
    tender_fee = find_label_value(soup, ["Tender Fee in ₹"])
    processing_fee = find_label_value(soup, ["Processing Fee in ₹"])
    emd = find_label_value(soup, ["EMD Amount in ₹"])
    location = find_label_value(soup, ["Location"])
    pincode = find_label_value(soup, ["Pincode", "PIN Code", "Pin Code"])
    product_category = find_label_value(soup, ["Product Category"])
    sub_category = find_label_value(soup, ["Sub Category"])
    contract_type = find_label_value(soup, ["Contract Type"])
    bid_validity = find_label_value(soup, ["Bid Validity"])
    pre_qualification = find_label_value(
        soup,
        [
            "Pre Qualification Details",
            "Pre-Qualification Details",
            "NDA/Pre Qualification",
            "NDA / Pre Qualification",
        ],
    )
    fee_payable_to = find_label_value(soup, ["Fee Payable To"])
    fee_payable_at = find_label_value(soup, ["Fee Payable At"])
    # MP Tender's displayed Total Fee excludes EMD.
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
        publish = (
            between("Published Date", ["Bid Opening Date", "Document Download / Sale Start Date"])
            or between("Publish Date", ["Bid Opening Date", "Document Download / Sale Start Date"])
        )
    if not closing:
        closing = between("Bid Submission End Date", ["Financial Bid Opening Date", "Document Documents", "Tender Documents"])
    if not opening:
        opening = between("Bid Opening Date", ["Document Download / Sale Start Date", "Document Download / Sale End Date"])
    if not bid_submission_start:
        bid_submission_start = between("Bid Submission Start Date", ["Bid Submission End Date", "Financial Bid Opening Date"])
    if not bid_submission_end:
        bid_submission_end = between("Bid Submission End Date", ["Financial Bid Opening Date", "Tender Documents"])
    if not document_start:
        document_start = between("Document Download / Sale Start Date", ["Document Download / Sale End Date", "Bid Submission Start Date"])
    if not document_end:
        document_end = between("Document Download / Sale End Date", ["Bid Submission Start Date", "Bid Submission End Date"])
    if not pac:
        pac = between("Tender Value in ₹", ["Product Category", "Sub category", "Contract Type"])
    if not pac:
        # NIC/MP Tender detail pages can render the label and value in the
        # same text node, so also parse the complete page text directly.
        pac_match = re.search(
            r"(?:Tender Value(?:\s+in\s+₹)?|PAC(?:\s+(?:Amount|cost))?)\s*(?:Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.\d+)?)",
            body_text,
            re.I,
        )
        if pac_match:
            pac = pac_match.group(1)
    if not pac:
        # Some MP notices put PAC only in the work title/description.
        pac_match = re.search(
            r"PAC(?:\s+(?:Amount|cost))?\s*(?:Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.\d+)?)",
            " ".join([title, work_description]),
            re.I,
        )
        if pac_match:
            pac = pac_match.group(1)
    if not tender_fee:
        tender_fee = between("Tender Fee in ₹", ["Processing Fee in ₹", "Fee Payable To"])
    if not pre_qualification:
        pre_qualification = (
            between("Pre Qualification Details", ["Independent External Monitor/Remarks", "Tender Value in ₹"])
            or between("NDA/Pre Qualification", ["Independent External Monitor/Remarks", "Tender Value in ₹"])
        )
    if not processing_fee:
        processing_fee = between("Processing Fee in ₹", ["Fee Payable To", "Fee Payable At"])
    if not emd:
        emd = between("EMD Amount in ₹", ["EMD Exemption Allowed", "EMD Fee Type"])
    if not location:
        location = between("Location", ["Pincode", "Pre Bid Meeting Place"])
    if not pincode:
        pin_match = re.search(r"\bPincode\s+([0-9]{6})\b", body_text, re.I)
        pincode = pin_match.group(1) if pin_match else ""
    total_fee = money_number(tender_fee) + money_number(processing_fee)

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
        "Work Description": clean(work_description),
        "Product Category": clean(product_category),
        "Sub Category": clean(sub_category),
        "Contract Type": clean(contract_type),
        "Bid Validity": clean(bid_validity),
        "Pre Qualification Details": clean(pre_qualification),
        "Bid Submission Start Date": clean(bid_submission_start),
        "Bid Submission End Date": clean(bid_submission_end or closing),
        "Bid Opening Date": clean(opening),
        "Document Download Start Date": clean(document_start),
        "Document Download End Date": clean(document_end),
        "Fee Payable To": clean(fee_payable_to),
        "Fee Payable At": clean(fee_payable_at),
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
        table_has_view_link = any(
            clean(a.get("title", "")).casefold() == "view tender information"
            for a in table.find_all("a")
        )
        looks_like_tender_table = (
            ("tender" in header_text and
             ("closing" in header_text or "title" in header_text or "reference" in header_text))
            or table_has_tender_id
            or table_has_view_link
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
            has_detail_link = any(
                clean(a.get("title", "")).casefold() == "view tender information"
                for a in tr.find_all("a")
            )
            if not tender_id and not has_detail_link:
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

            # Prefer the portal's explicit detail link; otherwise use the longest meaningful link.
            detail_candidates = [
                x for x in candidates
                if clean(x[0].get("title", "")).casefold() == "view tender information"
            ]
            anchor, href, anchor_text = (
                detail_candidates[0] if detail_candidates else max(candidates, key=lambda x: len(x[2]))
            )

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

            # The organisation chain is already present in the tender-list row.
            # Capture it here so Organisation / Department / Division / Sub Division
            # do not require opening the detail page.
            organisation_chain = ""
            for cell_text in texts:
                if "||" in cell_text:
                    organisation_chain = clean(cell_text)
                    break

            result.append({
                "url": href,
                "tender_id": tender_id,
                "reference": reference,
                "title": title,
                "organisation_chain": organisation_chain,
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


def open_tender_detail_by_click(page, tender):
    """Open a tender detail through the live JSF link in the current browser session.
    DirectLink URLs are session-bound, so never navigate to a stale copied URL."""
    tender_id = clean(tender.get("tender_id"))
    tender_title = clean(tender.get("title"))
    tender_ref = clean(tender.get("reference"))

    links = page.locator('a[title="View Tender Information"]')
    chosen = None
    for j in range(links.count()):
        link = links.nth(j)
        txt = clean(link.inner_text())
        raw = (link.get_attribute("href") or "") + " " + (link.get_attribute("onclick") or "")
        hay = f"{txt} {raw}".casefold()
        if (
            tender_id.casefold() in hay
            or (tender_title and tender_title.casefold() in hay)
            or (tender_ref and tender_ref.casefold() in hay)
        ):
            chosen = link
            break

    if chosen is None:
        raise RuntimeError(f"View Tender Information link not found for {tender_id}")

    chosen.click()
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)

    detail_soup = BeautifulSoup(page.content(), "html.parser")
    detail_text = clean(detail_soup.get_text(" ", strip=True))
    if tender_id and tender_id.casefold() not in detail_text.casefold():
        raise RuntimeError(
            f"Clicked link but detail page did not contain Tender ID {tender_id}; "
            f"current_url={page.url}"
        )
    return detail_soup


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
            # Keep the list-page URL generated in THIS Chromium session.
            row["list_page_url"] = page.url
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
    """Validate exactly N tender detail pages in one live Chromium session.
    The portal generates JSF DirectLinks tied to that browser session, so we
    click the actual 'View Tender Information' anchor on the organisation list."""
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

    results = []
    errors = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=HEADERS["User-Agent"],
            locale="en-IN",
            viewport={"width": 1920, "height": 1080},
        )
        try:
            # Establish a fresh browser session on the organisation master page.
            browser_page(page, ORG_URL)
            org_soup = BeautifulSoup(page.content(), "html.parser")
            live_orgs = parse_organisation_rows(org_soup, page.url)
            live_org = next(
                (x for x in live_orgs
                 if clean(x.get("name")).casefold() == target_name.casefold()),
                None,
            )
            if not live_org:
                raise RuntimeError(f"Live organisation not found: {target_name}")

            # Open the organisation tender list using the URL created in THIS browser session.
            browser_page(page, live_org["url"])
            list_soup = BeautifulSoup(page.content(), "html.parser")
            candidates = parse_tender_rows(list_soup, page.url)
            candidates = [
                row for row in candidates
                if clean(row.get("tender_id"))
            ]
            if len(candidates) < sample_size:
                raise RuntimeError(
                    f"Only {len(candidates)} live tenders found for {target_name}; expected {sample_size}."
                )

            selected = candidates[:sample_size]

            for idx, tender in enumerate(selected, 1):
                try:
                    # Return to the same live organisation list before each click so
                    # the JSF DirectLink and its session token are freshly generated.
                    browser_page(page, live_org["url"])

                    tender_id = clean(tender.get("tender_id"))
                    tender_title = clean(tender.get("title"))
                    tender_ref = clean(tender.get("reference"))

                    links = page.locator('a[title="View Tender Information"]')
                    chosen = None
                    for j in range(links.count()):
                        link = links.nth(j)
                        txt = clean(link.inner_text())
                        raw = (link.get_attribute("href") or "") + " " + (link.get_attribute("onclick") or "")
                        hay = f"{txt} {raw}".casefold()
                        if (
                            tender_id.casefold() in hay
                            or (tender_title and tender_title.casefold() in hay)
                            or (tender_ref and tender_ref.casefold() in hay)
                        ):
                            chosen = link
                            break

                    if chosen is None:
                        # Fallback: inspect every link's outerHTML for the ID/ref/title.
                        all_links = page.locator("a")
                        for j in range(all_links.count()):
                            link = all_links.nth(j)
                            outer = link.evaluate("(e) => e.outerHTML") or ""
                            if (
                                tender_id.casefold() in outer.casefold()
                                or (tender_title and tender_title.casefold() in outer.casefold())
                                or (tender_ref and tender_ref.casefold() in outer.casefold())
                            ):
                                chosen = link
                                break

                    if chosen is None:
                        raise RuntimeError(f"View Tender Information link not found for {tender_id}")

                    chosen.click()
                    page.wait_for_load_state("domcontentloaded", timeout=60000)
                    page.wait_for_timeout(1000)

                    detail_soup = BeautifulSoup(page.content(), "html.parser")
                    detail_text = clean(detail_soup.get_text(" ", strip=True))
                    if tender_id.casefold() not in detail_text.casefold():
                        raise RuntimeError(
                            f"Clicked link but detail page did not contain Tender ID {tender_id}; "
                            f"current_url={page.url}"
                        )

                    detail = parse_detail(detail_soup, page.url)
                    list_dates = parse_list_dates(tender)
                    detail["Tender ID"] = clean(detail.get("Tender ID")) or tender_id
                    detail["Title"] = clean(detail.get("Title")) or tender_title
                    detail["Reference Number"] = clean(detail.get("Reference Number")) or tender_ref
                    detail["Published Date"] = clean(detail.get("Published Date")) or list_dates[0]
                    detail["Closing Date"] = clean(detail.get("Closing Date")) or list_dates[1]
                    detail["Opening Date"] = clean(detail.get("Opening Date")) or list_dates[2]
                    detail["Organisation"] = target_name
                    detail["URL"] = page.url

                    # Quality gate: these three identifiers must be present; otherwise
                    # the page is not considered a successful detail extraction.
                    if not clean(detail.get("Tender ID")) or not clean(detail.get("Reference Number")):
                        raise RuntimeError(
                            f"Detail extraction missing required identifier: "
                            f"id={detail.get('Tender ID')} ref={detail.get('Reference Number')}"
                        )

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
        finally:
            browser.close()

    write_csv(output_file, results)
    if len(results) != sample_size:
        raise RuntimeError(
            f"Detail validation incomplete: {len(results)}/{sample_size} succeeded. "
            + (" | ".join(errors) if errors else "")
        )

    # Never replace the dashboard with the 9-test sample. Enrich only matching
    # Tender IDs already present in the dashboard file.
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

    print(f"DETAIL VALIDATION COMPLETE: {len(results)}/{sample_size} tenders, organisation={target_name}")
    return {
        "ok": True,
        "detail_validation": len(results),
        "organisation": target_name,
        "output": str(output_file),
        "errors": errors,
    }



def parse_portal_datetime(value):
    value = clean(value)
    formats = [
        "%d/%m/%Y %I:%M %p", "%d-%m-%Y %I:%M %p",
        "%d/%b/%Y %I:%M %p", "%d-%b-%Y %I:%M %p",
        "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M",
        "%d/%b/%Y %H:%M", "%d-%b-%Y %H:%M",
        "%d/%m/%Y", "%d-%m-%Y", "%d/%b/%Y", "%d-%b-%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        except ValueError:
            continue
    return None


def should_run_monitor_now():
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    if not (9 <= now.hour <= 19):
        return False
    minutes_from_anchor = (now.hour * 60 + now.minute) - (9 * 60)
    exact_times = {(9, 0), (11, 0), (13, 0), (15, 0), (17, 0), (19, 0)}
    if (now.hour, now.minute) in exact_times:
        return True
    if minutes_from_anchor < 0:
        return False
    remainder = minutes_from_anchor % 14
    # GitHub's 5-minute scheduler can start a few minutes after the exact slot.
    # Accept the nearest scheduled tick so the monitor remains effectively 14-minute based.
    return remainder <= 4 or remainder >= 10


def monitor_tender_changes(csv_file):
    if not should_run_monitor_now():
        print("MONITOR: outside 14-minute/fixed-time window; skipped.")
        return {"ok": True, "skipped": True, "changes": 0}

    org_csv = csv_file.parent / "organisations.csv"
    tender_list_csv = csv_file.parent / "organisation_tenders.csv"
    existing_rows = read_existing(csv_file)
    existing_by_id = {clean(r.get("Tender ID")): dict(r) for r in existing_rows if clean(r.get("Tender ID"))}
    old_org_rows = read_existing(org_csv)
    old_org_by_name = {clean(r.get("Organisation Name")).casefold(): r for r in old_org_rows if clean(r.get("Organisation Name"))}

    session = requests.Session()
    response = request(session, ORG_URL, sleep=0.4)
    soup = BeautifulSoup(response.text, "html.parser")
    live_orgs = parse_organisation_rows(soup, ORG_URL)
    if not live_orgs:
        raise RuntimeError("Monitor could not parse live organisation counts.")

    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    changed_orgs = []
    for org in live_orgs:
        name = clean(org["name"])
        old = old_org_by_name.get(name.casefold(), {})
        old_count = int(re.sub(r"\D", "", clean(old.get("Tender Count"))) or 0)
        active_expected = 0
        for row in existing_by_id.values():
            if clean(row.get("Organisation")).casefold() != name.casefold():
                continue
            closing = parse_portal_datetime(row.get("Closing Date"))
            if not closing or closing > now:
                active_expected += 1
        if org["count"] != old_count or org["count"] != active_expected:
            changed_orgs.append(org)

    if not changed_orgs:
        print(f"MONITOR: no changes ({len(live_orgs)} organisations).")
        return {"ok": True, "changes": 0, "organisations_checked": len(live_orgs)}

    tender_list_rows = read_existing(tender_list_csv)
    tender_list_by_id = {clean(r.get("Tender ID")): dict(r) for r in tender_list_rows if clean(r.get("Tender ID"))}
    stats = {"organisations_checked": len(live_orgs), "organisations_changed": 0, "new_tenders": 0, "details_opened": 0}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"], locale="en-IN", viewport={"width": 1920, "height": 1080})
        try:
            for org in changed_orgs:
                try:
                    rows, _ = browser_get_all_tender_rows(page, org, org["count"])
                    stats["organisations_changed"] += 1
                    count_match = len(rows) == org["count"]
                    for tender in rows:
                        tender_id = clean(tender.get("tender_id"))
                        if not tender_id:
                            continue
                        published, closing, opening = parse_list_dates(tender)
                        old = existing_by_id.get(tender_id, {})
                        tender_list_by_id[tender_id] = {
                            "S.No.": len(tender_list_by_id) + 1,
                            "Organisation Name": org["name"],
                            "Portal Tender Count": org["count"],
                            "Copied Tender Count": len(rows),
                            "Count Status": "MATCH" if count_match else "MISMATCH",
                            "Tender ID": tender_id,
                            "Title": tender.get("title", ""),
                            "Reference Number": tender.get("reference", ""),
                            "Published Date": published,
                            "Closing Date": closing,
                            "Opening Date": opening,
                            "Tender URL": tender.get("url", ""),
                            "Raw Row": tender.get("row_text", ""),
                        }
                        needs_detail = (
                            not old
                            or clean(old.get("Closing Date")) != clean(closing)
                            or not all(clean(old.get(k)) for k in (
                                "Department", "Division", "Sub Division",
                                "PAC Amount", "EMD Fee", "Tender Fee",
                                "Processing Fee", "Total Fee", "Work Description"
                            ))
                        )
                        if needs_detail:
                            try:
                                browser_page(page, org.get("url", "") or ORG_URL)
                                detail_soup = open_tender_detail_by_click(page, tender)
                                detail = parse_detail(detail_soup, page.url)
                                detail["Tender ID"] = clean(detail.get("Tender ID")) or tender_id
                                detail["Reference Number"] = clean(detail.get("Reference Number")) or clean(tender.get("reference"))
                                detail["Title"] = clean(detail.get("Title")) or clean(tender.get("title"))
                                detail["Published Date"] = clean(detail.get("Published Date")) or published
                                detail["Closing Date"] = clean(detail.get("Closing Date")) or closing
                                detail["Opening Date"] = clean(detail.get("Opening Date")) or opening
                                detail["Organisation"] = clean(detail.get("Organisation")) or org["name"]
                                detail["URL"] = clean(detail.get("URL")) or clean(tender.get("url"))
                                existing_by_id[tender_id] = {**old, **detail}
                                stats["details_opened"] += 1
                                if not old:
                                    stats["new_tenders"] += 1
                            except Exception as exc:
                                print(f"MONITOR detail error {org['name']} / {tender_id}: {exc}")
                        else:
                            existing_by_id[tender_id] = {
                                **old,
                                "Title": clean(tender.get("title")) or old.get("Title", ""),
                                "Reference Number": clean(tender.get("reference")) or old.get("Reference Number", ""),
                                "Published Date": published or old.get("Published Date", ""),
                                "Closing Date": closing or old.get("Closing Date", ""),
                                "Opening Date": opening or old.get("Opening Date", ""),
                                "Organisation": org["name"],
                                "URL": clean(tender.get("url")) or old.get("URL", ""),
                            }
                except Exception as exc:
                    print(f"MONITOR organisation error {org['name']}: {exc}")
        finally:
            browser.close()

    retrieved_at = now.isoformat()
    new_org_rows = [{
        "S.No.": i,
        "Organisation Name": org["name"],
        "Tender Count": org["count"],
        "Portal URL": org["url"],
        "Retrieved At": retrieved_at,
    } for i, org in enumerate(live_orgs, 1)]
    write_list_csv(org_csv, ORG_FIELDS, new_org_rows)
    write_csv(csv_file, list(existing_by_id.values()))
    write_list_csv(tender_list_csv, ORG_TENDER_FIELDS, list(tender_list_by_id.values()))
    print(f"MONITOR COMPLETE: {stats}")
    return {"ok": True, **stats}

def write_extraction_status(csv_file, status):
    status_file = csv_file.parent / "data" / "status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def scrape_mp_tenders(csv_file):
    """
    Full MP tender collection:
    1) Save the complete organisation list and portal Tender Count.
    2) Open every organisation one-by-one in the same Chromium session.
    3) Verify copied tender count equals the portal count.
    4) When FETCH_DETAIL_PAGES=1, open each tender detail page and enrich the main CSV with fees, PAC, organisation chain and critical dates.
    """
    process_started_at = datetime.now(timezone.utc).isoformat()
    stats = {
        "organisations_opened": 0,
        "tenders_seen": 0,
        "detail_opened": 0,
        "organisations_verified": 0,
        "organisations_count_mismatch": 0,
        "tender_listed": 0,
        "errors": [],
    }
    write_extraction_status(csv_file, {
        "status": "running",
        "process_started_at": process_started_at,
        "total_tenders": 0,
        "detail_complete": 0,
        "detail_remaining": 0,
        "errors": 0,
        "latest_error": "",
        "organisation_progress": "0/0",
        "detail_batch_size": int(os.getenv("DETAIL_BATCH_SIZE", "100") or 100),
        "detail_batch_completed": 0,
        "updated_at": process_started_at,
    })

    session = requests.Session()

    response = request(session, ORG_URL, sleep=0.5)
    soup = BeautifulSoup(response.text, "html.parser")
    organisations = parse_organisation_rows(soup, ORG_URL)
    if not organisations:
        raise RuntimeError("Organisation list could not be parsed from MP Tender portal.")

    if os.getenv("DETAIL_VALIDATION_ONLY") == "1":
        return run_detail_validation(csv_file, organisations)

    retrieved_at = process_started_at

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
    existing_detail_rows = read_existing(csv_file)
    existing_by_id = {
        clean(row.get("Tender ID")): dict(row)
        for row in existing_detail_rows
        if clean(row.get("Tender ID"))
    }
    # Repair records marked successful by the previous broken detail navigation.
    # Those pages were actually the portal home/menu, so fields such as Work Description
    # contain the repeated navigation text. Preserve Tender ID/basic list data, but make
    # these records eligible for a fresh, real detail extraction.
    corrupted = 0
    portal_menu_marker = "MIS Reports Tenders by Location Tenders by Organisation"
    for tid, row in existing_by_id.items():
        suspect_text = " ".join([
            clean(row.get("Work Description")),
            clean(row.get("Product Category")),
            clean(row.get("Sub Category")),
            clean(row.get("Contract Type")),
        ])
        if portal_menu_marker.casefold() in suspect_text.casefold():
            for key in (
                "Department","Division","Sub Division","PAC Amount","EMD Fee",
                "Tender Fee","Processing Fee","Total Fee","Location","Pincode",
                "Work Description","Product Category","Sub Category","Contract Type",
                "Bid Validity","Pre Qualification Details","Bid Submission Start Date",
                "Bid Submission End Date","Bid Opening Date","Document Download Start Date",
                "Document Download End Date","Fee Payable To","Fee Payable At"
            ):
                row[key] = ""
            row["Detail Extracted"] = ""
            corrupted += 1
    if corrupted:
        write_csv(csv_file, list(existing_by_id.values()))
        print(f"RESET CORRUPTED DETAIL CHECKPOINTS: {corrupted}")

    fetch_details = os.getenv("FETCH_DETAIL_PAGES", "0").lower() in ("1", "true", "yes")

    # Publish the real starting inventory immediately; never show a fake 0
    # while the detail extraction job is still running.
    initial_complete = sum(
        1 for r in existing_by_id.values()
        if clean(r.get("Detail Extracted")).upper() == "YES"
        and all(clean(r.get(k)) for k in (
            "Tender ID", "Tender Fee", "Processing Fee", "EMD Fee",
            "Total Fee", "Location", "Pincode", "Work Description",
            "Product Category", "Contract Type", "Bid Validity"
        ))
    )
    write_extraction_status(csv_file, {
        "status": "running",
        "process_started_at": process_started_at,
        "total_tenders": len(existing_by_id),
        "detail_complete": initial_complete,
        "detail_remaining": max(0, len(existing_by_id) - initial_complete),
        "errors": 0,
        "latest_error": "",
        "organisation_progress": f"0/{len(organisations)}",
        "detail_batch_size": int(os.getenv("DETAIL_BATCH_SIZE", "100") or 100),
        "detail_batch_completed": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    # Detail extraction is intentionally incremental: first run 10 records,
    # then 50 records per successful run. The complete tender list is still
    # collected every run, so the dashboard always retains all tenders.
    batch_state_file = csv_file.parent / "scrape_batch_state.json"
    try:
        batch_state = json.loads(batch_state_file.read_text(encoding="utf-8"))
    except Exception:
        batch_state = {}
    # Detail extraction is no longer artificially capped at 10 records.
    # Use 100 per run by default; an environment override can tune it.
    try:
        batch_size = max(10, min(500, int(os.getenv("DETAIL_BATCH_SIZE", "100"))))
    except ValueError:
        batch_size = 100
    detail_successes = 0
    detail_candidates_seen = 0
    detail_completed_ids = []

    # A previous 100-record run completed successfully but older code did not
    # persist the completed Tender IDs. Recover that checkpoint once, using the
    # same candidate order, so those records are never opened again.
    recovered_completed_ids = [
        tid for tid, row in existing_by_id.items()
        if tid and clean(row.get("Detail Extracted")).upper() == "YES"
        and all(clean(row.get(k)) for k in (
            "Tender ID", "Tender Fee", "Processing Fee", "EMD Fee",
            "Total Fee", "Location", "Pincode", "Work Description",
            "Product Category", "Contract Type", "Bid Validity"
        ))
    ]
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
                    write_extraction_status(csv_file, {
                        "status": "running",
                        "process_started_at": process_started_at,
                        "total_tenders": max(len(existing_by_id), stats.get("tender_listed", 0)),
                        "detail_complete": len(recovered_completed_ids) + detail_successes,
                        "detail_remaining": max(0, len(existing_by_id) - (len(recovered_completed_ids) + detail_successes)),
                        "errors": len(stats["errors"]),
                        "latest_error": stats["errors"][-1] if stats["errors"] else "",
                        "organisation_progress": f"{index-1}/{len(organisations)}",
                        "current_organisation": org["name"],
                        "detail_batch_size": batch_size,
                        "detail_batch_completed": detail_successes,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
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

                    # Save every tender-list row and, when requested, open its
                    # session-bound detail page in the same Chromium session.
                    for tender in tender_rows:
                        published, closing, opening = parse_list_dates(tender)
                        tender_id = clean(tender.get("tender_id"))
                        tender_list_rows.append({
                            "S.No.": len(tender_list_rows) + 1,
                            "Organisation Name": org["name"],
                            "Portal Tender Count": org["count"],
                            "Copied Tender Count": copied_count,
                            "Count Status": "MATCH" if count_match else "MISMATCH",
                            "Tender ID": tender_id,
                            "Title": tender.get("title", ""),
                            "Reference Number": tender.get("reference", ""),
                            "Published Date": published,
                            "Closing Date": closing,
                            "Opening Date": opening,
                            "Tender URL": tender.get("url", ""),
                            "Raw Row": tender.get("row_text", ""),
                        })

                        if fetch_details and tender_id:
                            old = existing_by_id.get(tender_id, {})

                            # Organisation hierarchy is available directly in the tender
                            # list row. Fill it immediately; do not spend a detail-page
                            # request just to obtain these four fields.
                            # LOCK the Organisation Chain exactly as it appears on
                            # the tender-list page. Detail-page parsing must never replace
                            # these hierarchy fields with a partial/different value.
                            chain = clean(tender.get("organisation_chain"))
                            locked_chain = None
                            if chain:
                                chain_org, chain_department, chain_division, chain_sub_division = parse_chain(chain)
                                locked_chain = (
                                    chain_org or org["name"],
                                    chain_department,
                                    chain_division,
                                    chain_sub_division,
                                )
                                old = {
                                    **old,
                                    "Organisation": locked_chain[0],
                                    "Department": locked_chain[1],
                                    "Division": locked_chain[2],
                                    "Sub Division": locked_chain[3],
                                }
                                existing_by_id[tender_id] = old

                            # Re-open a detail page whenever any important detail is missing,
                            # including the full Organisation Chain. Older CSV records may contain
                            # Missing detail fields are backfilled incrementally.
                            force_detail = os.getenv("FORCE_DETAIL_REFRESH", "0").lower() in ("1", "true", "yes")
                            old = existing_by_id.get(tender_id, {})
                            needs_detail = force_detail or not all(clean(old.get(k)) for k in (
                                "Tender ID", "PAC Amount", "EMD Fee", "Tender Fee",
                                "Processing Fee", "Total Fee", "Location", "Pincode",
                                "Work Description", "Product Category", "Sub Category",
                                "Contract Type", "Bid Validity", "Pre Qualification Details",
                                "Bid Submission Start Date", "Bid Submission End Date",
                                "Bid Opening Date", "Document Download Start Date",
                                "Document Download End Date", "Fee Payable To", "Fee Payable At"
                            ))
                            if fetch_details and tender_id and needs_detail and not clean(old.get("Detail Extracted")) and detail_successes < batch_size:
                                detail_candidates_seen += 1
                                try:
                                    # Refresh the organisation list in this same browser
                                    # session, then click the live JSF detail link. The copied
                                    # Tender URL is session-bound and must not be opened directly.
                                    list_page_url = clean(tender.get("list_page_url"))
                                    if not list_page_url:
                                        raise RuntimeError("live tender list page URL missing")
                                    browser_page(page, list_page_url)
                                    detail_soup = open_tender_detail_by_click(page, tender)
                                    detail = parse_detail(detail_soup, page.url)
                                    detail["Tender ID"] = clean(detail.get("Tender ID")) or tender_id
                                    detail["Reference Number"] = clean(detail.get("Reference Number")) or clean(tender.get("reference"))
                                    detail["Title"] = clean(detail.get("Title")) or clean(tender.get("title"))
                                    detail["Published Date"] = clean(detail.get("Published Date")) or published
                                    detail["Closing Date"] = clean(detail.get("Closing Date")) or closing
                                    detail["Opening Date"] = clean(detail.get("Opening Date")) or opening
                                    detail["Organisation"] = clean(detail.get("Organisation")) or org["name"]
                                    detail["URL"] = clean(detail.get("URL")) or clean(tender.get("url"))
                                    if not detail["Tender ID"]:
                                        raise RuntimeError("detail Tender ID missing")
                                    detail_blob = " ".join(
                                        clean(detail.get(k)) for k in (
                                            "Department","Division","Sub Division","PAC Amount",
                                            "EMD Fee","Tender Fee","Processing Fee","Location",
                                            "Pincode","Work Description","Product Category",
                                            "Contract Type","Bid Validity"
                                        )
                                    )
                                    if portal_menu_marker.casefold() in detail_blob.casefold():
                                        raise RuntimeError("detail page returned portal menu/home content")
                                    if not any(clean(detail.get(k)) for k in (
                                        "Department","Division","Sub Division","PAC Amount","EMD Fee",
                                        "Tender Fee","Processing Fee","Location","Pincode",
                                        "Work Description","Product Category","Contract Type",
                                        "Bid Validity","Pre Qualification Details"
                                    )):
                                        raise RuntimeError("detail page contained no usable tender detail fields")
                                    merged_detail = {**old, **detail, "Detail Extracted": "YES"}
                                    # Keep the exact four-level Organisation Chain from
                                    # the tender-list row, even if detail parsing differs.
                                    if locked_chain:
                                        merged_detail["Organisation"] = locked_chain[0]
                                        merged_detail["Department"] = locked_chain[1]
                                        merged_detail["Division"] = locked_chain[2]
                                        merged_detail["Sub Division"] = locked_chain[3]
                                    existing_by_id[tender_id] = merged_detail
                                    stats["detail_opened"] += 1
                                    detail_successes += 1
                                    detail_completed_ids.append(tender_id)
                                    # Save every 10 successful detail pages so the
                                    # checkpoint is never lost and progress becomes visible.
                                    if detail_successes % 10 == 0:
                                        write_csv(csv_file, list(existing_by_id.values()))
                                        complete_count = sum(
                                            1 for r in existing_by_id.values()
                                            if clean(r.get("Detail Extracted")).upper() == "YES" and all(clean(r.get(k)) for k in (
                                                "Tender ID", "Tender Fee", "Processing Fee", "EMD Fee",
                                                "Total Fee", "Location", "Pincode", "Work Description",
                                                "Product Category", "Contract Type", "Bid Validity"
                                            ))
                                        )
                                        write_extraction_status(csv_file, {
                                            "status": "running",
                                            "process_started_at": process_started_at,
                                            "total_tenders": len(existing_by_id),
                                            "detail_complete": complete_count,
                                            "detail_remaining": max(0, len(existing_by_id) - complete_count),
                                            "errors": len(stats["errors"]),
                                            "latest_error": stats["errors"][-1] if stats["errors"] else "",
                                            "organisation_progress": f"{index}/{len(organisations)}",
                                            "detail_batch_size": batch_size,
                                            "detail_batch_completed": detail_successes,
                                            "updated_at": datetime.now(timezone.utc).isoformat(),
                                        })
                                except Exception as detail_exc:
                                    stats["errors"].append(
                                        f"{org['name']} / {tender_id}: detail {type(detail_exc).__name__}: {detail_exc}"
                                    )
                                    # Save successful records even when one tender fails.
                                    write_csv(csv_file, list(existing_by_id.values()))
                    # Persist after every organisation so a long run keeps
                    # previously collected list data.
                    write_list_csv(
                        tender_list_csv, ORG_TENDER_FIELDS, tender_list_rows
                    )
                    # Also checkpoint all successful detail records after every
                    # organisation, so a long full extraction can resume safely.
                    write_csv(csv_file, list(existing_by_id.values()))
                    complete_count = sum(
                        1 for r in existing_by_id.values()
                        if clean(r.get("Detail Extracted")).upper() == "YES" and all(clean(r.get(k)) for k in (
                            "Tender ID", "Tender Fee", "Processing Fee", "EMD Fee",
                            "Total Fee", "Location", "Pincode", "Work Description",
                            "Product Category", "Contract Type", "Bid Validity"
                        ))
                    )
                    write_extraction_status(csv_file, {
                        "status": "running",
                        "total_tenders": len(existing_by_id),
                        "detail_complete": max(detail_successes, complete_count),
                        "detail_remaining": max(0, len(existing_by_id) - max(detail_successes, complete_count)),
                        "errors": len(stats["errors"]),
                        "latest_error": stats["errors"][-1] if stats["errors"] else "",
                        "organisation_progress": f"{index}/{len(organisations)}",
                        "detail_batch_size": batch_size,
                        "detail_batch_completed": detail_successes,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })

                except Exception as exc:
                    stats["errors"].append(
                        f"{org['name']}: {type(exc).__name__}: {exc}"
                    )
        finally:
            browser.close()

    # Build/refresh the main detailed CSV from the complete tender-list collection.
    # Safety: never replace a known-good dataset with an empty scrape. [detail-run]
    if not tender_list_rows:
        write_extraction_status(csv_file, {
            "status": "error",
            "process_started_at": process_started_at,
            "total_tenders": len(existing_by_id),
            "detail_complete": 0,
            "detail_remaining": len(existing_by_id),
            "errors": 1,
            "latest_error": "Portal tender rows could not be parsed; previous dataset preserved.",
            "organisation_progress": f"{len(organisations)}/{len(organisations)}",
            "detail_batch_size": batch_size,
            "detail_batch_completed": detail_successes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        print("SCRAPER SAFETY STOP: zero tender-list rows; previous dataset preserved.")
        return {"ok": False, "preserved_previous_dataset": True, "tender_list_records": 0}

    # Existing detail fields are preserved; the 9-tender validation is run separately
    # with DETAIL_VALIDATION_ONLY=1 so it can never replace the dashboard dataset.
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
            "Work Description": clean(old.get("Work Description")),
            "Product Category": clean(old.get("Product Category")),
            "Sub Category": clean(old.get("Sub Category")),
            "Contract Type": clean(old.get("Contract Type")),
            "Bid Validity": clean(old.get("Bid Validity")),
            "Pre Qualification Details": clean(old.get("Pre Qualification Details")),
            "Bid Submission Start Date": clean(old.get("Bid Submission Start Date")),
            "Bid Submission End Date": clean(old.get("Bid Submission End Date")),
            "Bid Opening Date": clean(row.get("Opening Date")) or clean(old.get("Bid Opening Date")),
            "Document Download Start Date": clean(old.get("Document Download Start Date")),
            "Document Download End Date": clean(old.get("Document Download End Date")),
            "Fee Payable To": clean(old.get("Fee Payable To")),
            "Fee Payable At": clean(old.get("Fee Payable At")),
            "Status": clean(old.get("Status")) or "Open",
            "URL": clean(row.get("Tender URL")),
            "Detail Extracted": clean(old.get("Detail Extracted")),
        }
        merged_rows.append(base)
    write_csv(csv_file, merged_rows)
    complete_count = sum(
        1 for r in merged_rows
        if all(clean(r.get(k)) for k in (
            "PAC Amount", "EMD Fee", "Tender Fee", "Processing Fee",
            "Pincode", "Department", "Division", "Sub Division"
        ))
    )
    write_extraction_status(csv_file, {
        "status": "completed",
        "process_started_at": process_started_at,
        "total_tenders": len(merged_rows),
        "detail_complete": sum(1 for r in merged_rows if clean(r.get("Detail Extracted")).upper() == "YES" and all(clean(r.get(k)) for k in ("PAC Amount","EMD Fee","Tender Fee","Processing Fee","Department","Division","Sub Division","Location","Pincode"))),
        "detail_remaining": max(0, len(merged_rows) - sum(1 for r in merged_rows if clean(r.get("Detail Extracted")).upper() == "YES" and all(clean(r.get(k)) for k in ("PAC Amount","EMD Fee","Tender Fee","Processing Fee","Department","Division","Sub Division","Location","Pincode")))),
        "errors": len(stats["errors"]),
        "latest_error": stats["errors"][-1] if stats["errors"] else "",
        "organisation_progress": f"{len(organisations)}/{len(organisations)}",
        "detail_batch_size": batch_size,
        "detail_batch_completed": detail_successes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    # Keep the next run at the configured full batch size. A failed detail
    # page must not reduce the whole project back to an artificial 10-record cap.
    if stats["errors"]:
        next_batch_size = batch_size
    else:
        next_batch_size = batch_size
    batch_state_file.write_text(
        json.dumps({
            "next_batch_size": next_batch_size,
            "last_batch_size": batch_size,
            "last_batch_completed": detail_successes,
            "detail_candidates_seen": detail_candidates_seen,
            "completed_detail_ids": detail_completed_ids[:batch_size] or recovered_completed_ids[:batch_size],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "source": ORG_URL,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "organisation_records": len(org_rows),
        "tender_list_records": len(tender_list_rows),
        "total_records": len(merged_rows),
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
    if os.getenv("MONITOR_ONLY") == "1":
        print(monitor_tender_changes(target))
    else:
        try:
            print(scrape_mp_tenders(target))
        except Exception as exc:
            existing = read_existing(target)
            completed = sum(
                1 for r in existing
                if clean(r.get("Detail Extracted")).upper() == "YES"
                and all(clean(r.get(k)) for k in (
                    "PAC Amount","EMD Fee","Tender Fee","Processing Fee",
                    "Department","Division","Sub Division","Location","Pincode"
                ))
            )
            write_extraction_status(target, {
                "status": "failed",
                "process_started_at": "",
                "total_tenders": len(existing),
                "detail_complete": completed,
                "detail_remaining": max(0, len(existing) - completed),
                "errors": 1,
                "latest_error": f"{type(exc).__name__}: {exc}",
                "organisation_progress": "failed",
                "detail_batch_size": int(os.getenv("DETAIL_BATCH_SIZE", "100") or 100),
                "detail_batch_completed": completed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            raise
