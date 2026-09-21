# [run-scrape-details] data extraction engine: dual live routes + checkpoint-safe full run
# [run-scrape-details] recovery/detail route audit trigger
# [run-scrape-details] protect recovered dataset before live collection
# [run-scrape-details] syntax-verified merge block
# [run-scrape-details] final syntax repair trigger
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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests

# [run-scrape-details] retry real session-bound detail extraction
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

PORTAL = "https://mptenders.gov.in/nicgep/app"
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
    "Fee Payable To", "Fee Payable At", "Status", "Detail Extracted", "Corrigendum", "Corrigendum Last Checked", "Corrigendum Detected At", "Corrigendum Type", "Corrigendum 15m Checked", "Corrigendum 5m Checked",
]
ORG_FIELDS = ["S.No.", "Organisation Name", "Tender Count", "Portal URL", "Retrieved At"]
ORG_TENDER_FIELDS = [
    "S.No.", "Organisation Name", "Portal Tender Count", "Copied Tender Count",
    "Count Status", "Tender ID", "Title", "Reference Number",
    "Published Date", "Closing Date", "Opening Date", "Tender URL", "Raw Row"
]
TENDER_ID_RE = re.compile(r"\b20\d{2}_[A-Z0-9]+_\d+_\d+\b", re.I)

# Never persist or directly open MP JSF session URLs. Tender ID is the permanent key.
SESSION_URL_RE = re.compile(r"(?:[?&])session=", re.I)


def clean(value):
    # Portal tables sometimes expose numeric values (e.g. tender counts); normalize them before regex parsing.
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


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


def parse_latest_corrigendum(soup):
    """Read the Latest Corrigendum List shown on a tender detail page."""
    result = {"title": "", "type": "", "key": ""}
    for heading in soup.find_all(string=re.compile(r"Latest\s+Corrigendum\s+List", re.I)):
        table = heading.find_parent("table")
        if not table:
            continue
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td","th"])]
            if len(cells) < 3:
                continue
            low = " ".join(c.casefold() for c in cells)
            if "corrigendum title" in low or "corrigendum type" in low:
                continue
            result["title"] = cells[1]
            result["type"] = cells[2]
            result["key"] = (result["title"] + "||" + result["type"]).strip().casefold()
            return result
    return result

def parse_detail(soup, url):
    """RSP-derived resilient MP Tender detail extraction.

    Navigation/session handling stays in our live Playwright routes; this
    function only parses the already-open detail page.
    """
    pairs = find_value_pairs(soup)
    body_text = clean(soup.get_text(" ", strip=True))

    def exact_value(*labels):
        wanted = {clean(x).rstrip(":").casefold() for x in labels}
        for cell in soup.find_all(["td", "th"]):
            key = clean(cell.get_text(" ", strip=True)).rstrip(":").casefold()
            if key not in wanted:
                continue
            sibling = cell.find_next_sibling(["td", "th"])
            while sibling is not None:
                value = clean(sibling.get_text(" ", strip=True))
                if value and value not in (":", "-"):
                    return value
                sibling = sibling.find_next_sibling(["td", "th"])
        return ""

    def value(*labels):
        found = exact_value(*labels)
        if found:
            return found
        for label in labels:
            key = clean(label).rstrip(":").casefold()
            if key in pairs and clean(pairs[key]):
                return clean(pairs[key])
        return clean(find_label_value(soup, list(labels)))

    def labeled_amount(prefixes):
        """Extract the numeric amount from fee/EMD labels even when the portal
        appends text such as '(18.00% GST Incl.)' to the label."""
        wanted = [clean(x).casefold() for x in prefixes]
        for cell in soup.find_all(["td", "th", "label", "div", "span"]):
            label_text = clean(cell.get_text(" ", strip=True))
            low = label_text.casefold()
            if not any(low.startswith(x) for x in wanted):
                continue
            sibling = cell.find_next_sibling(["td", "th", "label", "div", "span"])
            if sibling is not None:
                raw = clean(sibling.get_text(" ", strip=True))
                m = re.search(r"(?<![A-Za-z])(?:Rs\\.?\\s*|₹\\s*)?([0-9][0-9,]*(?:\\.\\d+)?)", raw)
                if m:
                    return m.group(1)
            m = re.search(r"(?<![A-Za-z])(?:Rs\\.?\\s*|₹\\s*)?([0-9][0-9,]*(?:\\.\\d+)?)", label_text[len(min((x for x in prefixes if x), key=len, default="")):])
            if m:
                return m.group(1)
        return ""

    def between(label, stop_labels):
        stops = "|".join(re.escape(clean(x)) for x in stop_labels if clean(x))
        match = re.search(
            re.escape(label) + r"\s*(.*?)\s*(?:" + stops + r"|$)",
            body_text,
            re.I,
        )
        return clean(match.group(1)) if match else ""

    def date_value(*labels):
        found = value(*labels)
        if found:
            return found
        for label in labels:
            found = find_critical_date(soup, label)
            if found:
                return clean(found)
        return ""

    chain = value("Organisation Chain", "Organization Chain")
    if not chain:
        chain = between("Organisation Chain", ["Tender Reference Number", "Tender ID"])
    organisation, department, division, sub_division = parse_chain(chain)

    tender_id = value("Tender ID")
    if not tender_id:
        match = TENDER_ID_RE.search(body_text)
        tender_id = match.group(0) if match else ""

    reference = value(
        "Tender Reference Number", "Tender Ref. No.", "Tender Ref.No", "Tender Reference"
    )
    if not reference:
        reference = between("Tender Reference Number", ["Tender ID", "Withdrawal Allowed"])

    title = value("Work /Item(s) Title", "Work Item(s) Title", "Title")
    work_description = value("Work Description")
    if not title:
        title = between("Work /Item(s) Title", ["Work Description", "Pre Qualification Details"])
    if not title:
        title = work_description

    published = date_value("Published Date", "Publish Date", "Publication Date")
    closing = date_value(
        "Bid Submission End Date",
        "Bid Submission Closing Date",
        "Submission End Date",
    )
    opening = date_value("Bid Opening Date")
    bid_submission_start = date_value("Bid Submission Start Date")
    bid_submission_end = date_value("Bid Submission End Date")
    document_start = date_value(
        "Document Download / Sale Start Date",
        "Document Download Start Date",
    )
    document_end = date_value(
        "Document Download / Sale End Date",
        "Document Download End Date",
    )

    if not published:
        published = between(
            "Published Date",
            ["Bid Opening Date", "Document Download / Sale Start Date"],
        ) or between(
            "Publish Date",
            ["Bid Opening Date", "Document Download / Sale Start Date"],
        )
    if not closing:
        closing = between(
            "Bid Submission End Date",
            ["Financial Bid Opening Date", "Tender Documents"],
        )
    if not opening:
        opening = between(
            "Bid Opening Date",
            ["Document Download / Sale Start Date", "Document Download / Sale End Date"],
        )
    if not bid_submission_start:
        bid_submission_start = between(
            "Bid Submission Start Date",
            ["Bid Submission End Date", "Financial Bid Opening Date"],
        )
    if not bid_submission_end:
        bid_submission_end = between(
            "Bid Submission End Date",
            ["Financial Bid Opening Date", "Tender Documents"],
        )
    if not document_start:
        document_start = between(
            "Document Download / Sale Start Date",
            ["Document Download / Sale End Date", "Bid Submission Start Date"],
        )
    if not document_end:
        document_end = between(
            "Document Download / Sale End Date",
            ["Bid Submission Start Date", "Bid Submission End Date"],
        )

    pac = value(
        "Tender Value in ₹", "Tender Value",
        "Estimated Cost in ₹", "Estimated Cost",
        "Estimated Value in ₹", "Estimated Value",
        "Estimated Tender Value",
    )
    if not pac:
        pac = between(
            "Tender Value in ₹",
            ["Product Category", "Sub category", "Contract Type"],
        )
    if not pac:
        match = re.search(
            r"(?:Tender Value(?:\s+in\s+₹)?|Estimated Cost(?:\s+in\s+₹)?|"
            r"Estimated Value(?:\s+in\s+₹)?|Estimated Tender Value|"
            r"PAC(?:\s+(?:Amount|cost))?)\s*(?:Rs\.?|₹)?\s*"
            r"([0-9][0-9,]*(?:\.\d+)?)",
            body_text,
            re.I,
        )
        if match:
            pac = match.group(1)
    if not pac:
        match = re.search(
            r"PAC(?:\s+(?:Amount|cost))?\s*(?:Rs\.?|₹)?\s*"
            r"([0-9][0-9,]*(?:\.\d+)?)",
            " ".join([title, work_description]),
            re.I,
        )
        if match:
            pac = match.group(1)

    tender_fee = labeled_amount(["Tender Fee in ₹", "Tender Fee", "Document Fee", "Tender Document Fee"]) or value(
        "Tender Fee in ₹", "Tender Fee", "Document Fee", "Tender Document Fee"
    )
    if not tender_fee:
        tender_fee = between("Tender Fee in ₹", ["Processing Fee in ₹", "Fee Payable To"])

    processing_fee = labeled_amount(["Processing Fee in ₹", "Processing Fee", "Portal Fee"]) or value("Processing Fee in ₹", "Processing Fee", "Portal Fee")
    if not processing_fee:
        processing_fee = between("Processing Fee in ₹", ["Fee Payable To", "Fee Payable At"])
    # Some MP Tender detail templates render the fee label and amount as
    # plain text instead of adjacent table cells. In that case the table
    # parser above can return 0/blank even though the portal shows the fee.
    if not processing_fee:
        # Recover the portal processing fee when the detail template renders
        # the label/value only as plain text.
        match = re.search(
            r"Processing Fee(?:\s+in\s+₹)?\s*[:\-]?\s*(?:Rs\.?\s*|₹\s*)?"
            r"([0-9][0-9,]*(?:\.\d+)?)",
            body_text,
            re.I,
        )
        if match:
            processing_fee = match.group(1)


    emd = labeled_amount(["EMD Amount in ₹", "EMD Amount", "EMD Fee", "Earnest Money Deposit"]) or value(
        "EMD Amount in ₹", "EMD Amount", "EMD Fee", "Earnest Money Deposit"
    )
    if not emd:
        emd = between("EMD Amount in ₹", ["EMD Exemption Allowed", "EMD Fee Type"])

    location = value("Location")
    if not location:
        location = between("Location", ["Pincode", "Pre Bid Meeting Place"])

    pincode = value("Pincode", "PIN Code", "Pin Code")
    if not pincode:
        match = re.search(r"\bPincode\s*[:\-]?\s*([0-9]{6})\b", body_text, re.I)
        pincode = match.group(1) if match else ""

    product_category = value("Product Category")
    sub_category = value("Sub Category", "Sub category")
    contract_type = value("Contract Type")
    bid_validity = value("Bid Validity")
    pre_qualification = value(
        "Pre Qualification Details",
        "Pre-Qualification Details",
        "NDA/Pre Qualification",
        "NDA / Pre Qualification",
    )
    if not pre_qualification:
        pre_qualification = (
            between(
                "Pre Qualification Details",
                ["Independent External Monitor/Remarks", "Tender Value in ₹"],
            )
            or between(
                "NDA/Pre Qualification",
                ["Independent External Monitor/Remarks", "Tender Value in ₹"],
            )
        )

    fee_payable_to = value("Fee Payable To")
    fee_payable_at = value("Fee Payable At")

    # Dashboard Total Fee = EMD + Tender/Form Fee + Processing Fee.
    # EMD is intentionally included because this is the user's required
    # combined payable/fee figure, not the portal's displayed fee subtotal.
    total_fee = (
        money_number(emd)
        + money_number(tender_fee)
        + money_number(processing_fee)
    )

    return {
        "Tender ID": clean(tender_id),
        "Published Date": clean(published),
        "Closing Date": clean(closing),
        "Opening Date": clean(opening),
        "Title": clean(title),
        "Reference Number": clean(reference),
        "Organisation": clean(organisation),
        "Department": clean(department),
        "Division": clean(division),
        "Sub Division": clean(sub_division),
        "PAC Amount": money_text(pac),
        "EMD Fee": money_text(emd),
        "Tender Fee": money_text(tender_fee),
        "Processing Fee": money_text(processing_fee),
        "Total Fee": str(int(total_fee)) if total_fee.is_integer() else f"{total_fee:.2f}",
        "Location": clean(location),
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
        "Status": ("Cancelled" if re.search(r"\\b(cancelled|canceled|tender cancelled|tender canceled|withdrawn|withdrawal)\\b", body_text, re.I) else "Open"),
        # Never persist a JSF session URL.
        "URL": PORTAL,
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



def assert_no_session_url(url):
    """Session-bound MP Tender URLs are ephemeral and must never be opened via goto
    or persisted as a tender URL. Live JSF links may only be activated by clicking
    them inside the current browser session."""
    value = clean(url)
    if "session=" in value.casefold():
        raise RuntimeError(f"Session-bound URL must not be opened or persisted: {value[:180]}")
    return value


def browser_page(page, url, referer=None):
    """Open only a stable MP Tender URL. Never page.goto() a session-bound URL."""
    stable = assert_no_session_url(url)
    page.goto(stable, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    return BeautifulSoup(page.content(), "html.parser")


def click_live_anchor(page, link):
    """Click a JSF DirectLink in the current page/session.
    The href may contain session=, but it is NEVER passed to page.goto()."""
    link.click()
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)
    return BeautifulSoup(page.content(), "html.parser")


def find_live_tender_link(page, tender_id="", tender_title="", tender_ref=""):
    tender_id = clean(tender_id)
    tender_title = clean(tender_title)
    tender_ref = clean(tender_ref)

    # The portal's search result is intentionally followed by clicking the
    # visible Tender Title, exactly as a human bidder does.
    if tender_title:
        anchors = page.locator("a")
        for j in range(anchors.count()):
            link = anchors.nth(j)
            txt = clean(link.inner_text())
            if tender_title.casefold() in txt.casefold():
                return link

    links = page.locator('a[title="View Tender Information"]')
    for j in range(links.count()):
        link = links.nth(j)
        txt = clean(link.inner_text())
        outer = link.evaluate("(e) => e.outerHTML") or ""
        hay = f"{txt} {outer}".casefold()
        if (
            tender_id.casefold() in hay
            or (tender_title and tender_title.casefold() in hay)
            or (tender_ref and tender_ref.casefold() in hay)
        ):
            return link

    # Fallback: the portal search-result title link may not carry the
    # View Tender Information title attribute.
    all_links = page.locator("a")
    for j in range(all_links.count()):
        link = all_links.nth(j)
        txt = clean(link.inner_text())
        outer = link.evaluate("(e) => e.outerHTML") or ""
        hay = f"{txt} {outer}".casefold()
        if (
            (tender_title and tender_title.casefold() in hay)
            or (tender_id and tender_id.casefold() in hay)
            or (tender_ref and tender_ref.casefold() in hay)
        ):
            return link
    return None


def open_tender_detail_by_search(page, tender):
    """Find a tender by Tender ID from the stable MP home page search box,
    click the result title, and parse the resulting detail page.

    This deliberately avoids all saved/session-bound URLs. The only navigation
    URL opened directly is the stable portal home page.
    """
    tender_id = clean(tender.get("tender_id") or tender.get("Tender ID"))
    tender_title = clean(tender.get("title") or tender.get("Title"))
    tender_ref = clean(tender.get("reference") or tender.get("Reference Number"))
    if not tender_id:
        raise RuntimeError("Tender ID is required for ID search")

    browser_page(page, PORTAL)

    search_box = page.locator("#SearchDescription")
    if search_box.count() == 0:
        search_box = page.locator('input[name="SearchDescription"]')
    if search_box.count() == 0:
        raise RuntimeError("MP Tender search box #SearchDescription not found")

    search_box.first.fill(tender_id)

    go = page.locator('input[type="submit"][value="Go"]')
    if go.count() == 0:
        go = page.locator('input[value="Go"]')
    if go.count() == 0:
        go = page.get_by_role("button", name=re.compile(r"^Go$", re.I))
    if go.count() == 0:
        raise RuntimeError("MP Tender search Go button not found")

    go.first.click()
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    # JSF can take longer than the button response; wait until either the
    # searched Tender ID or a no-result message is actually rendered.
    for _ in range(12):
        page.wait_for_timeout(500)
        if tender_id.casefold() in clean(page.locator("body").inner_text()).casefold():
            break

    result_text = clean(page.locator("body").inner_text())
    if tender_id.casefold() not in result_text.casefold():
        raise RuntimeError(
            f"Tender ID search returned no matching result for {tender_id}; "
            f"current_url={page.url}"
        )

    chosen = find_live_tender_link(page, tender_id, tender_title, tender_ref)
    if chosen is None:
        # Search-result pages normally show the title as a normal anchor.
        # Prefer a link whose visible text is the requested title.
        if tender_title:
            exact = page.get_by_text(tender_title, exact=False)
            if exact.count():
                for j in range(exact.count()):
                    candidate = exact.nth(j)
                    if candidate.evaluate("(e) => e.tagName").upper() == "A":
                        chosen = candidate
                        break
        if chosen is None:
            raise RuntimeError(f"Tender title/result link not found for {tender_id}")

    detail_soup = click_live_anchor(page, chosen)
    detail_text = clean(detail_soup.get_text(" ", strip=True))
    if tender_id.casefold() not in detail_text.casefold():
        raise RuntimeError(
            f"Search result title click did not open Tender Details for {tender_id}; "
            f"current_url={page.url}"
        )
    return detail_soup


def open_organisation_page_from_home(page):
    """Open Tenders by Organisation by clicking from the stable portal home page.
    Never goto() the JSF organisation URL directly."""
    browser_page(page, PORTAL)
    candidates = page.locator("a")
    for i in range(candidates.count()):
        a = candidates.nth(i)
        text = clean(a.inner_text()).casefold()
        title = clean(a.get_attribute("title")).casefold()
        if "tenders by organisation" in text or "tenders by organisation" in title:
            return click_live_anchor(page, a)
    raise RuntimeError("Tenders by Organisation link not found on MP Tender home page")


def open_tender_detail_by_organisation(page, tender, org):
    """Open a tender from its live organisation list.
    This is the first extraction route; it never reuses a saved DirectLink URL."""
    tender_id = clean(tender.get("tender_id") or tender.get("Tender ID"))
    tender_title = clean(tender.get("title") or tender.get("Title"))
    tender_ref = clean(tender.get("reference") or tender.get("Reference Number"))
    list_soup = open_organisation_list_by_click(page, org)
    chosen = find_live_tender_link(page, tender_id, tender_title, tender_ref)
    if chosen is None:
        # The list can paginate; use the current live page search as a fallback
        # only after the organisation route has been attempted.
        raise RuntimeError(f"Live organisation tender link not found for {tender_id}")
    detail_soup = click_live_anchor(page, chosen)
    detail_text = clean(detail_soup.get_text(" ", strip=True))
    if tender_id.casefold() not in detail_text.casefold():
        raise RuntimeError(f"Organisation click did not open Tender Details for {tender_id}; current_url={page.url}")
    return detail_soup


def open_tender_detail_dual(page, tender, org):
    """Use both supported portal paths:
    1) Organisation list -> live Title click.
    2) Home page -> Tender ID search -> Go -> live Title click.
    If the first route fails, the second route is used automatically."""
    errors = []
    try:
        return open_tender_detail_by_organisation(page, tender, org)
    except Exception as exc:
        errors.append(f"organisation route: {type(exc).__name__}: {exc}")
    try:
        return open_tender_detail_by_search(page, tender)
    except Exception as exc:
        errors.append(f"home search route: {type(exc).__name__}: {exc}")
        raise RuntimeError(" | ".join(errors))


def open_tender_detail_by_click(page, tender):
    """Backward-compatible wrapper. Detail extraction now uses the stable
    Tender-ID search flow rather than any session-bound list URL."""
    return open_tender_detail_by_search(page, tender)


def open_organisation_list_by_click(page, org):
    """From the stable organisation page, click the organisation's live count.
    Never navigate to the DirectLink href."""
    org_soup = open_organisation_page_from_home(page)
    org_name = clean(org.get("name"))
    expected = int(org.get("count") or 0)

    rows = page.locator("tr")
    chosen = None
    for i in range(rows.count()):
        row = rows.nth(i)
        text = clean(row.inner_text())
        if org_name.casefold() not in text.casefold():
            continue
        anchors = row.locator("a")
        for j in range(anchors.count()):
            a = anchors.nth(j)
            txt = clean(a.inner_text())
            if txt.replace(",", "").isdigit():
                count = int(txt.replace(",", ""))
                if count == expected or expected == 0:
                    chosen = a
                    break
        if chosen is not None:
            break

    if chosen is None:
        raise RuntimeError(f"Live organisation count link not found: {org_name}")

    return click_live_anchor(page, chosen)


def click_next_live_page(page):
    """Click the live Next pagination link; never open its session-bound href."""
    candidates = page.locator("a")
    for i in range(candidates.count()):
        a = candidates.nth(i)
        text = clean(a.inner_text()).casefold()
        title = clean(a.get_attribute("title")).casefold()
        aria = clean(a.get_attribute("aria-label")).casefold()
        if text in {"next", ">", "»", "next >"} or "next page" in title or "next page" in aria:
            return click_live_anchor(page, a)
    return None


def browser_get_all_tender_rows(page, org, expected_count):
    """Collect an organisation's tender rows using only live clicks.
    No session-bound URL is passed to page.goto(), stored, or reused."""
    soup = open_organisation_list_by_click(page, org)
    unique = {}
    pages = 0

    for _ in range(500):
        rows = parse_tender_rows(soup, page.url)
        for row in rows:
            # Tender URLs from the portal are session-bound. Keep no URL in the
            # persistent row; Tender ID is the permanent key.
            row["url"] = ""
            row.pop("list_page_url", None)
            key = row["tender_id"] or row["reference"] or row.get("title")
            if key:
                unique[key] = row
        pages += 1

        if expected_count and len(unique) >= expected_count:
            break

        next_soup = click_next_live_page(page)
        if next_soup is None:
            break
        soup = next_soup

    records = list(unique.values())
    if expected_count and len(records) != expected_count:
        raise RuntimeError(
            f"Organisation tender count mismatch for {clean(org.get('name'))}: "
            f"portal={expected_count}, actually collected={len(records)}"
        )
    return records, pages



def read_existing(csv_file):
    if not csv_file.exists():
        return []
    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # Clean any legacy session-bound URLs left by older scraper versions.
    for row in rows:
        if SESSION_URL_RE.search(clean(row.get("URL"))):
            row["URL"] = PORTAL
        if SESSION_URL_RE.search(clean(row.get("Tender URL"))):
            row["Tender URL"] = ""
        if SESSION_URL_RE.search(clean(row.get("Portal URL"))):
            row["Portal URL"] = ORG_URL
    return rows


def write_csv(csv_file, rows):
    """Write without deleting any previously-known columns or records."""
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    existing_fields = []
    if csv_file.exists() and csv_file.stat().st_size:
        try:
            with csv_file.open("r", encoding="utf-8-sig", newline="") as old_f:
                existing_fields = list(csv.DictReader(old_f).fieldnames or [])
        except Exception:
            existing_fields = []
    fieldnames = list(FIELDS)
    for field in existing_fields:
        if field and field not in fieldnames:
            fieldnames.append(field)
    for row in rows or []:
        for field in row.keys():
            if field and field not in fieldnames:
                fieldnames.append(field)
    temp = csv_file.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows or [])
    temp.replace(csv_file)


def write_list_csv(csv_file, fieldnames, rows):
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    temp = csv_file.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        safe_rows = []
        for row in rows:
            item = dict(row)
            if SESSION_URL_RE.search(clean(item.get("Tender URL"))):
                item["Tender URL"] = ""
            if SESSION_URL_RE.search(clean(item.get("Portal URL"))):
                item["Portal URL"] = ORG_URL
            safe_rows.append(item)
        writer.writerows(safe_rows)
    temp.replace(csv_file)


def merge_preserve_existing(existing_rows, new_rows, key_field="Tender ID"):
    """Merge new rows without deleting prior data or blanking known-good fields."""
    merged = {}
    order = []
    for row in existing_rows or []:
        key = clean(row.get(key_field))
        if not key: continue
        merged[key] = dict(row); order.append(key)
    for row in new_rows or []:
        key = clean(row.get(key_field))
        if not key: continue
        if key not in merged:
            merged[key] = dict(row); order.append(key); continue
        for field, value in dict(row).items():
            value = clean(value)
            if value or not clean(merged[key].get(field)): merged[key][field] = value
    return [merged[k] for k in order if k in merged]

def merge_org_rows(existing_rows, new_rows):
    """Organisation master is additive; counts are refreshed, old orgs remain."""
    merged = {}; order = []
    for row in existing_rows or []:
        key = clean(row.get("Organisation Name")).casefold()
        if not key: continue
        merged[key] = dict(row); order.append(key)
    for row in new_rows or []:
        key = clean(row.get("Organisation Name")).casefold()
        if not key: continue
        if key not in merged:
            merged[key] = dict(row); order.append(key)
        else:
            for field, value in dict(row).items():
                value = clean(value)
                if value or not clean(merged[key].get(field)): merged[key][field] = value
    for idx, key in enumerate(order, 1): merged[key]["S.No."] = idx
    return [merged[k] for k in order if k in merged]

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
            # Build the live organisation list only through clicks; never goto() a
            # session-bound organisation URL.
            list_soup = open_organisation_list_by_click(page, target_org)
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
                    tender_id = clean(tender.get("tender_id"))
                    tender_title = clean(tender.get("title"))
                    tender_ref = clean(tender.get("reference"))

                    # Use the stable home-page Tender ID search for every sample.
                    detail_soup = open_tender_detail_dual(page, {
                        "tender_id": tender_id,
                        "title": tender_title,
                        "reference": tender_ref,
                    }, target_org)
                    detail = parse_detail(detail_soup, PORTAL)
                    list_dates = parse_list_dates(tender)
                    detail["Tender ID"] = clean(detail.get("Tender ID")) or tender_id
                    detail["Title"] = clean(detail.get("Title")) or tender_title
                    detail["Reference Number"] = clean(detail.get("Reference Number")) or tender_ref
                    detail["Published Date"] = clean(detail.get("Published Date")) or list_dates[0]
                    detail["Closing Date"] = clean(detail.get("Closing Date")) or list_dates[1]
                    detail["Opening Date"] = clean(detail.get("Opening Date")) or list_dates[2]
                    detail["Organisation"] = target_name
                    detail["URL"] = PORTAL

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
                    continue

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


def is_watchable_tender(row, now):
    closing = parse_portal_datetime(row.get("Closing Date"))
    opening = parse_portal_datetime(row.get("Opening Date") or row.get("Bid Opening Date"))
    if closing and closing <= now and opening and opening <= now:
        return False
    return True


def _corrigendum_detail_with_retry(page, tender, attempts=3):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            soup = open_tender_detail_by_search(page, tender)
            text = clean(soup.get_text(" ", strip=True))
            if clean(tender.get("tender_id")).casefold() not in text.casefold():
                raise RuntimeError("detail page does not contain requested Tender ID")
            detail = parse_detail(soup, PORTAL)
            if clean(detail.get("Tender ID")).casefold() != clean(tender.get("tender_id")).casefold():
                raise RuntimeError("detail parser returned a different Tender ID")
            return detail
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise last


def _is_cancelled_detail(detail):
    hay = " ".join([clean(detail.get("Status")), clean(detail.get("Corrigendum")), clean(detail.get("Corrigendum Type"))]).casefold()
    return bool(re.search(r"\b(cancelled|canceled|tender cancelled|tender canceled|withdrawn|withdrawal)\b", hay))


def _urgent_corrigendum_stage(opening, now):
    if not opening:
        return ""
    minutes = (opening - now).total_seconds() / 60.0
    if 12.0 <= minutes <= 18.0:
        return "15m"
    if 2.0 <= minutes <= 8.0:
        return "5m"
    return ""


def monitor_corrigendum_changes(csv_file, urgent_only=False):
    """6-hour watch plus reliable 15/5-minute pre-opening checks."""
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    existing_rows = read_existing(csv_file)
    existing_by_id = {clean(r.get("Tender ID")): dict(r) for r in existing_rows if clean(r.get("Tender ID"))}
    watch_rows = []
    for row in existing_by_id.values():
        if not is_watchable_tender(row, now):
            continue
        opening = parse_portal_datetime(row.get("Opening Date") or row.get("Bid Opening Date"))
        if urgent_only:
            closing = parse_portal_datetime(row.get("Closing Date"))
            if not closing or closing > now or not opening:
                continue
            stage = _urgent_corrigendum_stage(opening, now)
            if not stage:
                continue
            marker = clean(row.get("Corrigendum 15m Checked" if stage == "15m" else "Corrigendum 5m Checked"))
            if marker == opening.isoformat():
                continue
            row["_urgent_stage"] = stage
        watch_rows.append(row)

    checked = changed = cancelled = errors = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"], locale="en-IN", viewport={"width":1920,"height":1080})
        try:
            for row in watch_rows:
                tender_id = clean(row.get("Tender ID"))
                tender = {"tender_id": tender_id, "title": clean(row.get("Title")), "reference": clean(row.get("Reference Number"))}
                try:
                    fresh = _corrigendum_detail_with_retry(page, tender, attempts=3 if urgent_only else 2)
                    checked += 1
                    old_close = clean(row.get("Closing Date"))
                    new_close = clean(fresh.get("Closing Date")) or old_close
                    old_open = clean(row.get("Opening Date") or row.get("Bid Opening Date"))
                    new_open = clean(fresh.get("Opening Date") or fresh.get("Bid Opening Date")) or old_open
                    old_corr = clean(row.get("Corrigendum"))
                    old_type = clean(row.get("Corrigendum Type"))
                    new_corr = clean(fresh.get("Corrigendum"))
                    new_type = clean(fresh.get("Corrigendum Type"))

                    updated = dict(row)
                    updated.pop("_urgent_stage", None)
                    for key, value in fresh.items():
                        if clean(value):
                            updated[key] = value
                    updated["Tender ID"] = tender_id
                    updated["URL"] = PORTAL
                    updated["Corrigendum Last Checked"] = now.isoformat()

                    if _is_cancelled_detail(fresh):
                        was_cancelled = clean(row.get("Status")).casefold() == "cancelled"
                        updated["Status"] = "Cancelled"
                        updated["Corrigendum Type"] = "Cancelled"
                        updated["Corrigendum"] = new_corr or "Tender Cancelled"
                        updated["Corrigendum Detected At"] = now.isoformat()
                        if not was_cancelled:
                            changed += 1
                            cancelled += 1
                    elif old_close != new_close or old_open != new_open or old_corr != new_corr or old_type != new_type:
                        old_dt = parse_portal_datetime(old_close)
                        new_dt = parse_portal_datetime(new_close)
                        if old_dt and new_dt and new_dt > old_dt:
                            updated["Corrigendum Type"] = "Date Extension"
                            updated["Corrigendum"] = new_corr or "Date Extension"
                        elif old_dt and new_dt and new_dt < old_dt:
                            updated["Corrigendum Type"] = "Date Changed"
                            updated["Corrigendum"] = new_corr or "Date Changed"
                        elif old_open != new_open:
                            updated["Corrigendum Type"] = "Bid Opening Date Changed"
                            updated["Corrigendum"] = new_corr or "Bid Opening Date Changed"
                        else:
                            updated["Corrigendum Type"] = new_type or "Other"
                            updated["Corrigendum"] = new_corr or "Other Corrigendum"
                        updated["Corrigendum Detected At"] = now.isoformat()
                        changed += 1

                    if urgent_only:
                        stage = row.get("_urgent_stage", "")
                        opening_key = parse_portal_datetime(updated.get("Opening Date") or updated.get("Bid Opening Date"))
                        if stage == "15m" and opening_key:
                            updated["Corrigendum 15m Checked"] = opening_key.isoformat()
                        elif stage == "5m" and opening_key:
                            updated["Corrigendum 5m Checked"] = opening_key.isoformat()
                    existing_by_id[tender_id] = updated
                except Exception as exc:
                    errors += 1
                    print(f"CORRIGENDUM WATCH ERROR {tender_id}: {type(exc).__name__}: {exc}")
        finally:
            browser.close()
    write_csv(csv_file, list(existing_by_id.values()))
    mode = "URGENT-15/5-MIN" if urgent_only else "6-HOUR"
    print(f"CORRIGENDUM WATCH {mode} COMPLETE: candidates={len(watch_rows)}, checked={checked}, changes={changed}, cancelled={cancelled}, errors={errors}")
    return {"ok": True, "checked": checked, "changes": changed, "cancelled": cancelled, "errors": errors, "candidates": len(watch_rows)}

def monitor_tender_changes(csv_file):
    # The normal monitor has its legacy time gate, but the new random
    # organisation scheduler explicitly decides when this function runs.
    if os.getenv("ORG_SCHEDULED_MONITOR") != "1" and not should_run_monitor_now():
        print("MONITOR: outside legacy monitor window; skipped.")
        return {"ok": True, "skipped": True, "changes": 0}

    org_csv = csv_file.parent / "organisations.csv"
    tender_list_csv = csv_file.parent / "organisation_tenders.csv"
    existing_rows = read_existing(csv_file)
    existing_by_id = {clean(r.get("Tender ID")): dict(r) for r in existing_rows if clean(r.get("Tender ID"))}
    old_org_rows = read_existing(org_csv)
    old_org_by_name = {clean(r.get("Organisation Name")).casefold(): r for r in old_org_rows if clean(r.get("Organisation Name"))}

    # Hourly organisation monitor MUST use the same live browser path as the
    # full scraper: open MP Tender home -> click "Tenders by Organisation".
    # This keeps the monitor aligned with the JSF portal and avoids stale/session URLs.
    live_orgs = []
    with sync_playwright() as bootstrap_pw:
        bootstrap_browser = bootstrap_pw.chromium.launch(headless=True)
        bootstrap_page = bootstrap_browser.new_page(
            user_agent=HEADERS["User-Agent"],
            locale="en-IN",
            viewport={"width": 1920, "height": 1080},
        )
        try:
            live_soup = open_organisation_page_from_home(bootstrap_page)
            live_orgs = parse_organisation_rows(live_soup, bootstrap_page.url)
        finally:
            bootstrap_browser.close()
    if not live_orgs:
        raise RuntimeError("Monitor could not parse live Tenders by Organisation counts.")

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
                            "Tender URL": "",
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
                                # Never open the organisation's saved DirectLink URL.
                                # Search by permanent Tender ID from the stable home page.
                                detail_soup = open_tender_detail_by_search(page, tender)
                                detail = parse_detail(detail_soup, PORTAL)
                                detail["Tender ID"] = clean(detail.get("Tender ID")) or tender_id
                                detail["Reference Number"] = clean(detail.get("Reference Number")) or clean(tender.get("reference"))
                                detail["Title"] = clean(detail.get("Title")) or clean(tender.get("title"))
                                detail["Published Date"] = clean(detail.get("Published Date")) or published
                                detail["Closing Date"] = clean(detail.get("Closing Date")) or closing
                                detail["Opening Date"] = clean(detail.get("Opening Date")) or opening
                                detail["Organisation"] = clean(detail.get("Organisation")) or org["name"]
                                detail["URL"] = PORTAL
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
                                "URL": PORTAL,
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
        "Portal URL": ORG_URL,
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

def status_metrics(rows):
    rows = rows or []
    departments = {clean(r.get("Department")) for r in rows if clean(r.get("Department"))}
    pincodes = {clean(r.get("Pincode")).replace(" ", "") for r in rows if re.fullmatch(r"\d{6}", clean(r.get("Pincode")).replace(" ", ""))}
    other_pins = {p for p in pincodes if not re.match(r"^(45|46|47|48)\d{4}$", p)}
    return {"department_count": len(departments), "pincode_count": len(pincodes), "pincode_others_count": len(other_pins)}

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
        "detail_batch_size": int(os.getenv("DETAIL_BATCH_SIZE", "0") or 0),
        "detail_batch_completed": 0,
        "updated_at": process_started_at,
    })

    session = requests.Session()

    # Primary organisation discovery path: open the stable MP home page,
    # click "Tenders by Organisation", then parse the live organisation table.
    # This avoids the direct ORG_URL request that previously timed out.
    organisations = []
    try:
        with sync_playwright() as bootstrap_pw:
            bootstrap_browser = bootstrap_pw.chromium.launch(headless=True)
            bootstrap_page = bootstrap_browser.new_page(
                user_agent=HEADERS["User-Agent"],
                locale="en-IN",
                viewport={"width": 1920, "height": 1080},
            )
            try:
                soup = open_organisation_page_from_home(bootstrap_page)
                organisations = parse_organisation_rows(soup, bootstrap_page.url)
            finally:
                bootstrap_browser.close()
    except Exception as bootstrap_exc:
        stats["errors"].append(f"organisation browser discovery: {type(bootstrap_exc).__name__}: {bootstrap_exc}")
        print(f"Organisation browser discovery failed; trying HTTP fallback: {bootstrap_exc}")

    if not organisations:
        # Secondary fallback: retain the HTTP parser in case the browser route
        # is temporarily unavailable.
        response = request(session, ORG_URL, retries=5, sleep=2.0)
        soup = BeautifulSoup(response.text, "html.parser")
        organisations = parse_organisation_rows(soup, ORG_URL)
    if not organisations:
        raise RuntimeError("Organisation list could not be parsed from MP Tender portal.")

    portal_total_tenders = sum(int(org.get("count") or 0) for org in organisations)

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
            "Portal URL": ORG_URL,
            "Retrieved At": retrieved_at,
        })

    org_csv = csv_file.parent / "organisations.csv"
    tender_list_csv = csv_file.parent / "organisation_tenders.csv"
    existing_org_rows = read_existing(org_csv)
    merged_org_rows = merge_org_rows(existing_org_rows, org_rows)
    write_list_csv(org_csv, ORG_FIELDS, merged_org_rows)

    existing_tender_list_rows = read_existing(tender_list_csv)
    tender_list_by_id = {clean(r.get("Tender ID")): dict(r) for r in existing_tender_list_rows if clean(r.get("Tender ID"))}
    tender_list_rows = list(tender_list_by_id.values())
    existing_detail_rows = read_existing(csv_file)
    existing_by_id = {
        clean(row.get("Tender ID")): dict(row)
        for row in existing_detail_rows
        if clean(row.get("Tender ID"))
    }

    # Retention policy: keep a tender for 3 days after its closing date.
    # After that, remove the complete record from the local dataset so the
    # dashboard/API does not carry stale detail indefinitely. If a later
    # corrigendum extends the closing date, the tender will be discovered
    # again by the next Tenders-by-Organisation snapshot and re-extracted.
    retention_cutoff = now - timedelta(days=3)
    pruned_ids = set()
    for tid, row in list(existing_by_id.items()):
        closing = parse_portal_datetime(row.get("Closing Date"))
        if closing and closing < retention_cutoff:
            pruned_ids.add(tid)
            del existing_by_id[tid]

    if pruned_ids:
        tender_list_by_id = {
            tid: row for tid, row in tender_list_by_id.items()
            if tid not in pruned_ids
        }
        tender_list_rows = list(tender_list_by_id.values())
        write_csv(csv_file, list(existing_by_id.values()))
        write_csv(tender_list_csv, tender_list_rows)
        print(f"RETENTION CLEANUP: deleted {len(pruned_ids)} tender records older than 3 days after closing")
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
        "total_tenders": portal_total_tenders,
        "portal_total_tenders": portal_total_tenders,
        "organisation_count": len(organisations),
        **status_metrics(existing_by_id.values()),
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
        configured_batch = int(os.getenv("DETAIL_BATCH_SIZE", "0") or 0)
        # 0 means no artificial batch cap. Checkpoints are still written every 10 successes.
        batch_size = 0 if configured_batch <= 0 else max(10, min(500, configured_batch))
    except ValueError:
        batch_size = 0
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
                        "total_tenders": portal_total_tenders,
                        "portal_total_tenders": portal_total_tenders,
                        "organisation_count": len(organisations),
                        "detail_complete": len(recovered_completed_ids) + detail_successes,
                        "detail_remaining": max(0, len(existing_by_id) - (len(recovered_completed_ids) + detail_successes)),
                        "errors": len(stats["errors"]),
                        "latest_error": stats["errors"][-1] if stats["errors"] else "",
                        "organisation_progress": f"{index-1}/{len(organisations)}",
                        "current_organisation": org["name"],
                        **status_metrics(existing_by_id.values()),
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
                            "Tender URL": "",
                            "Raw Row": tender.get("row_text", ""),
                        })

                        # Save the list-level record immediately. Detail-only fields
                        # remain blank until the detail page is successfully extracted.
                        if tender_id:
                            old = existing_by_id.get(tender_id, {})
                            chain = clean(tender.get("organisation_chain"))
                            chain_org, chain_department, chain_division, chain_sub_division = parse_chain(chain) if chain else ("", "", "", "")
                            existing_by_id[tender_id] = {
                                **old,
                                "Tender ID": tender_id,
                                "Published Date": published or old.get("Published Date", ""),
                                "Closing Date": closing or old.get("Closing Date", ""),
                                "Opening Date": opening or old.get("Opening Date", ""),
                                "Title": clean(tender.get("title")) or old.get("Title", ""),
                                "Reference Number": clean(tender.get("reference")) or old.get("Reference Number", ""),
                                "Organisation": chain_org or old.get("Organisation") or org["name"],
                                "Department": chain_department or old.get("Department", ""),
                                "Division": chain_division or old.get("Division", ""),
                                "Sub Division": chain_sub_division or old.get("Sub Division", ""),
                                "Status": clean(old.get("Status")) or "Open",
                                "URL": PORTAL,
                                "Detail Extracted": clean(old.get("Detail Extracted")),
                            }

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
                            if fetch_details and tender_id and needs_detail and not clean(old.get("Detail Extracted")) and (batch_size <= 0 or detail_successes < batch_size):
                                detail_candidates_seen += 1
                                try:
                                    # IMPORTANT: never reuse the tender-list URL.
                                    # MP Tender DirectLink URLs contain session= and expire.
                                    # Find this Tender ID again from the stable home-page
                                    # search box, click the live result title, and parse it.
                                    detail_soup = open_tender_detail_dual(page, tender, org)
                                    detail = parse_detail(detail_soup, PORTAL)
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

                                    # LIVE 1-BY-1 CHECKPOINT:
                                    # Save the successfully extracted tender immediately.
                                    # The GitHub Actions writer publishes this CSV checkpoint
                                    # before the scraper moves to the next Tender ID. This makes
                                    # each completed detail appear on GitHub Pages as soon as
                                    # the commit reaches the repository.
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
                                        "total_tenders": portal_total_tenders,
                                        "portal_total_tenders": portal_total_tenders,
                                        "organisation_count": len(organisations),
                                        **status_metrics(existing_by_id.values()),
                                        "detail_complete": complete_count,
                                        "detail_remaining": max(0, len(existing_by_id) - complete_count),
                                        "errors": len(stats["errors"]),
                                        "latest_error": stats["errors"][-1] if stats["errors"] else "",
                                        "organisation_progress": f"{index}/{len(organisations)}",
                                        "detail_batch_size": batch_size,
                                        "detail_batch_completed": detail_successes,
                                        "checkpoint_ready": True,
                                        "checkpoint_tender_id": tender_id,
                                        "updated_at": datetime.now(timezone.utc).isoformat(),
                                    })
                                    print(f"LIVE CHECKPOINT SAVED: {tender_id} ({detail_successes} detail records complete); move to next Tender ID.")
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
                        "total_tenders": portal_total_tenders,
                        "portal_total_tenders": portal_total_tenders,
                        "organisation_count": len(organisations),
                        **status_metrics(existing_by_id.values()),
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

    # Build/refresh the detailed CSV without ever deleting older Tender IDs.
    current_rows_by_id = {
        clean(r.get("Tender ID")): r
        for r in tender_list_rows
        if clean(r.get("Tender ID"))
    }
    merged_rows = [
        dict(old)
        for tid, old in existing_by_id.items()
        if tid not in current_rows_by_id
    ]

    for tender_id, row in current_rows_by_id.items():
        old = dict(existing_by_id.get(tender_id, {}))
        base = {
            "Tender ID": tender_id,
            "Published Date": clean(row.get("Published Date")) or clean(old.get("Published Date")),
            "Closing Date": clean(row.get("Closing Date")) or clean(old.get("Closing Date")),
            "Opening Date": clean(row.get("Opening Date")) or clean(old.get("Opening Date")),
            "Title": clean(row.get("Title")) or clean(old.get("Title")),
            "Reference Number": clean(row.get("Reference Number")) or clean(old.get("Reference Number")),
            "Organisation": clean(row.get("Organisation Name")) or clean(old.get("Organisation")),
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
            "URL": PORTAL,
            "Detail Extracted": clean(old.get("Detail Extracted")),
        }
        merged_rows.append(base)

    if len(merged_rows) < len(existing_by_id):
        print(
            f"SAFETY STOP: merged tender IDs {len(merged_rows)} < existing "
            f"{len(existing_by_id)}; preserving previous dataset."
        )
        return {
            "ok": False,
            "preserved_previous_dataset": True,
            "tender_list_records": len(tender_list_rows),
            "total_records": len(existing_by_id),
        }
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
        "total_tenders": portal_total_tenders,
        "portal_total_tenders": portal_total_tenders,
        "organisation_count": len(organisations),
        **status_metrics(merged_rows),
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
            "checkpoint_interval": 10,
            "detail_candidates_seen": detail_candidates_seen,
            "completed_detail_ids": (detail_completed_ids if batch_size <= 0 else detail_completed_ids[:batch_size]) or (recovered_completed_ids if batch_size <= 0 else recovered_completed_ids[:batch_size]),
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
    if os.getenv("MONITOR_CORRIGENDUM_ONLY") == "1":
        urgent = os.getenv("CORRIGENDUM_URGENT_ONLY", "0").lower() in ("1", "true", "yes")
        print(monitor_corrigendum_changes(target, urgent_only=urgent))
    elif os.getenv("MONITOR_ONLY") == "1":
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