from portal_fee_exceptions import verified_fee_omission
from telegram_text import HINDI_DISCLAIMER
import csv
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape
from urllib import request, parse

from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import LongTable, TableStyle, Paragraph, Spacer, BaseDocTemplate, PageTemplate, Frame, Flowable
from pdf_promotions import draw_table_promotions

IST = timezone(timedelta(hours=5, minutes=30))
SITE_URL = "https://tenders.codinglms.xyz/"
TELEGRAM_URL = "https://t.me/mptendersalert"
ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = Path(os.getenv("TENDER_CSV_PATH", "all_tenders_org_detailed.csv"))


def clean(value):
    return str(value or "").strip()


def strip_brackets(value):
    return re.sub(r"^\[|\]$", "", clean(value))


def parse_date(value):
    text = clean(value)
    text = re.sub(r"[\[\]]", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Prefer a complete date + time so PDF ordering uses the actual closing time.
    datetime_patterns = (
        r"\b\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*(?:AM|PM)?\b",
        r"\b\d{1,2}-\d{1,2}-\d{4}\s+\d{1,2}:\d{2}\s*(?:AM|PM)?\b",
        r"\b\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?\b",
    )
    candidates = []
    for pattern in datetime_patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            candidates.append(m.group(0))

    for candidate in candidates + [text]:
        candidate = candidate.strip()
        for fmt in (
            "%d/%m/%Y %I:%M %p", "%d/%m/%Y %H:%M",
            "%d-%m-%Y %I:%M %p", "%d-%m-%Y %H:%M",
            "%d-%b-%Y %I:%M %p", "%d-%b-%Y %H:%M",
            "%d-%B-%Y %I:%M %p", "%d-%B-%Y %H:%M",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(candidate, fmt).replace(tzinfo=IST)
            except ValueError:
                pass

    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=IST)
        except ValueError:
            pass
    return None


def valid_reference(value):
    text = strip_brackets(value)
    return "" if re.search(r"\b20\d{2}_[A-Z0-9]+_\d+_\d+\b", text, re.I) else text


def load_report_rows(path=CSV_PATH, now=None):
    """Join the same detail and current-list fields used by the dashboard."""
    path = Path(path)
    by_id = {}
    for source in (path,):
        if not source.exists():
            continue
        with source.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                tid = clean(row.get("Tender ID"))
                if not tid:
                    continue
                row["Reference Number"] = valid_reference(row.get("Reference Number"))
                base = by_id.setdefault(tid, dict(row))
                for key, value in row.items():
                    if not clean(base.get(key)) and clean(value):
                        base[key] = value
    listing = path.parent / "organisation_tenders.csv"
    if listing.exists():
        with listing.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                tid = clean(row.get("Tender ID"))
                if not tid:
                    continue
                base = by_id.setdefault(tid, {})
                for key in ("Tender ID", "Title", "Published Date", "Closing Date", "Opening Date"):
                    if clean(row.get(key)):
                        base[key] = row[key]
                reference = valid_reference(row.get("Reference Number"))
                if reference:
                    base["Reference Number"] = reference
                base["Reference Number"] = valid_reference(base.get("Reference Number"))
    details = path.parent / "tender_details.csv"
    if details.exists():
        with details.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                base = by_id.get(clean(row.get("Tender ID")))
                if base is None:
                    continue  # Do not add historical detail-only records.
                row["Reference Number"] = valid_reference(row.get("Reference Number"))
                for key, value in row.items():
                    if not clean(base.get(key)) and clean(value):
                        base[key] = value
    active_ids = current_portal_ids(path.parent, now)
    return [row for tid, row in by_id.items() if active_ids is None or tid in active_ids]

def morning_inventory_rows(rows, root):
    """Use the latest official organisation list, also on extraction failure.

    Old master-only tenders must not reappear when yesterday's evening guard
    expires at midnight. Missing details do not exclude a copied Tender ID.
    """
    path = Path(root) / "organisation_tenders.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            ids = {clean(row.get("Tender ID")) for row in csv.DictReader(stream)} - {""}
    except OSError:
        ids = set()
    return [row for row in rows if clean(row.get("Tender ID")) in ids] if ids else rows


def current_portal_ids(root, now=None):
    """The latest copied list defines membership, also when extraction fails.

    Details only enrich IDs; old master-only rows never add live tenders.
    A verified snapshot is a fallback when the copied CSV is unavailable.
    """
    listing = Path(root) / "organisation_tenders.csv"
    try:
        with listing.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if "Tender ID" in (reader.fieldnames or []):
                return {clean(row.get("Tender ID")) for row in reader} - {""}
    except OSError:
        pass
    now = (now or datetime.now(IST)).astimezone(IST)
    try:
        snapshot = json.loads((Path(root) / "data/live_snapshot.json").read_text())
        stamp = datetime.fromisoformat(snapshot.get("snapshot_at", ""))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        stamp = stamp.astimezone(IST)
    except (OSError, ValueError, TypeError):
        return None
    if snapshot.get("verified") is not True or stamp > now:
        return None
    return set(snapshot.get("tender_ids") or [])


def live_rows(rows, now=None, root=None):
    now = now or datetime.now(IST)
    active_ids = current_portal_ids(root if root is not None else CSV_PATH.parent, now)
    return [row for row in rows if clean(row.get("Status")).lower() != "cancelled"
            and (active_ids is None or clean(row.get("Tender ID")) in active_ids)
            and (closing := parse_date(row.get("Closing Date"))) and closing > now]


def fee_amount(value):
    text = clean(value).replace(",", "").replace("₹", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def total_tender_fee(row):
    values = [fee_amount(row.get(key)) for key in ("EMD Fee", "Tender Fee", "Processing Fee")]
    return sum(values, Decimal(0)) if all(value is not None for value in values) else None


def fee_text(value):
    if value is None:
        return "Checking"
    return format(value, ",.2f")


def is_on_date(value, target):
    dt = parse_date(value)
    return bool(dt and dt.date() == target)


def closing_sort_key(row):
    """
    Sort PDFs by exact Closing Date + Closing Time, earliest first.
    Rows without a usable closing date/time go to the end.
    """
    dt = parse_date(row.get("Closing Date"))
    if dt is None:
        return (1, datetime.max.replace(tzinfo=IST))
    return (0, dt)


def telegram_message(token, chat_id, text):
    data = parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "false",
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with request.urlopen(request.Request(url, data=data), timeout=30) as r:
        body = r.read().decode()
        result = json.loads(body)
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram message failed"))
        return result


def telegram_document(token, chat_id, path, caption):
    boundary = "----MPTendersBoundaryA7C1"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    req = request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with request.urlopen(req, timeout=120) as r:
        result = r.read().decode()
        payload = json.loads(result)
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "Telegram document failed"))
        return payload


def make_pdf(rows, filename, report_title, total_available=None, filter_detail="All Tenders", filter_live=True):
    rows = live_rows(rows) if filter_live else list(rows)
    path = Path(filename)
    if total_available is None:
        total_available = len(rows)
    report_scope = f"Total Records: {len(rows)} out of {total_available} • Filter: {filter_detail}"
    styles = getSampleStyleSheet()
    cell = ParagraphStyle(
        "cell", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8.2, leading=10.0, alignment=TA_CENTER, textColor=colors.HexColor("#233044")
    )
    center = ParagraphStyle("center", parent=cell, alignment=TA_CENTER)
    subtitle = ParagraphStyle(
        "subtitle", parent=styles["Normal"], fontName="Helvetica",
        fontSize=7.5, leading=9, alignment=TA_CENTER,
        textColor=colors.HexColor("#4b5563")
    )

    doc = BaseDocTemplate(
        str(path), pagesize=landscape(A3),
        leftMargin=18, rightMargin=18, topMargin=78, bottomMargin=24,
        title=report_title, author="SAR Digital Services, Kannod",
    )

    story = [
        Paragraph("SAR Digital Services, Kannod", ParagraphStyle(
            "t", parent=styles["Title"], fontName="Helvetica-Bold",
            fontSize=15, textColor=colors.HexColor("#14376e"),
            alignment=TA_CENTER, leading=18, spaceAfter=2
        )),
        Paragraph("MP Tender Live Dashboard", subtitle),
        Spacer(1, 4),
        Paragraph(report_title, subtitle),
        Paragraph(report_scope, ParagraphStyle(
            "scope", parent=subtitle, fontName="Helvetica-Bold",
            fontSize=7.5, textColor=colors.HexColor("#14376e")
        )),
        Spacer(1, 7),
    ]

    header_style = ParagraphStyle("table_header", parent=center, fontName="Helvetica-Bold",
                                  fontSize=7.5, leading=9, textColor=colors.white)
    header = [Paragraph(label, header_style) for label in (
        "S.No.", "Tender ID", "Closing Date", "Title", "Ref.No.", "PAC Amount",
        "EMD Fee", "Form Fee", "Processing Fee", "Total Fee<br/>(EMD + Form + Processing)"
    )]
    # Keep the PDF in exact Closing Date + Closing Time order.
    # LongTable automatically fills each page with as many complete rows as
    # fit. There is deliberately NO fixed 15-row page limit.
    rows = sorted(rows, key=closing_sort_key)

    data = [header]
    for offset, row in enumerate(rows, 1):
        data.append([
            Paragraph(str(offset), center),
            Paragraph(escape(strip_brackets(row.get("Tender ID"))), cell),
            Paragraph(clean(row.get("Closing Date")), center),
            Paragraph(escape(strip_brackets(row.get("Title"))), cell),
            Paragraph(escape(valid_reference(row.get("Reference Number"))) or "-", cell),
            Paragraph(fee_text(fee_amount(row.get("PAC Amount"))) if clean(row.get("PAC Amount")).upper() not in {"NA", "N/A"} else "NA", center),
            Paragraph(fee_text(fee_amount(row.get("EMD Fee"))), center),
            Paragraph(fee_text(fee_amount(row.get("Tender Fee"))), center),
            Paragraph("Not provided" if verified_fee_omission(row) else fee_text(fee_amount(row.get("Processing Fee"))), center),
            Paragraph("Not available" if verified_fee_omission(row) else fee_text(total_tender_fee(row)), center),
        ])

    table = LongTable(
        data,
        colWidths=[30, 125, 105, 290, 175, 74, 74, 74, 80, 85],
        repeatRows=1,
        splitByRow=1,
        splitInRow=0,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#14376e")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#cdd7e4")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#ebf3fc")]),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    story.append(table)
    if not rows:
        story.extend([Spacer(1, 18), Paragraph("No tenders match this report. Total Records: 0.", subtitle)])

    generated = datetime.now(IST).strftime("%d/%m/%Y %I:%M %p IST")

    def page_header_footer(canvas, document):
        w, h = landscape(A3)
        canvas.saveState()

        # Dedicated header band: keep ALL header text inside the band and
        # leave enough top margin so the table can never overlap it.
        header_h = 56
        canvas.setFillColor(colors.HexColor("#14376e"))
        canvas.rect(0, h-header_h, w, header_h, fill=1, stroke=0)

        # Telegram button - left
        canvas.setFillColor(colors.HexColor("#0088cc"))
        canvas.roundRect(18, h-22, 58, 12, 3, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawCentredString(47, h-18, "Get Daily Alert")
        canvas.linkURL(TELEGRAM_URL, (18, h-22, 76, h-10), relative=0)

        # Website button - right
        canvas.setFillColor(colors.HexColor("#2563eb"))
        canvas.roundRect(w-76, h-22, 58, 12, 3, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.drawCentredString(w-47, h-18, "Website")
        canvas.linkURL(SITE_URL, (w-76, h-22, w-18, h-10), relative=0)

        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawCentredString(w/2, h-13, "SAR Digital Services, Kannod")
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(w/2, h-26, "MP Tender Live Dashboard")
        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.drawCentredString(w/2, h-38, "For DSC & E-Tendering Services • Contact Admin: t.me/rdgyan")
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawCentredString(w/2, h-50, report_scope)
        canvas.linkURL("https://t.me/rdgyan", (w/2-85, h-53, w/2+85, h-41), relative=0)

        # Footer
        canvas.setFillColor(colors.HexColor("#14376e"))
        canvas.rect(0, 0, w, 16, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(18, 6, "SAR Digital Services, Kannod • Contact Admin: t.me/rdgyan")
        canvas.linkURL("https://t.me/rdgyan", (18, 4, 125, 11), relative=0)
        canvas.drawRightString(w-18, 6, f"Page {document.page} • Generated {generated}")
        canvas.restoreState()

        if document.page == 2:
            draw_table_promotions(canvas, w, h)

    # Page two reserves space for both cards before the continuing tender table.
    w, h = landscape(A3)
    def frame(name, top):
        return Frame(18,24,w-36,h-top-24,id=name)
    doc.addPageTemplates([
        PageTemplate(id='first',frames=[frame('first-frame',78)],onPage=page_header_footer,autoNextPageTemplate='promotions'),
        PageTemplate(id='promotions',frames=[frame('promotions-frame',240)],onPage=page_header_footer,autoNextPageTemplate='remaining'),
        PageTemplate(id='remaining',frames=[frame('remaining-frame',78)],onPage=page_header_footer),
    ])
    # Even a short report includes the requested second-page cards.
    # A zero-size end marker adds that page only if the table stayed on page one.
    class EnsurePromotionPage(Flowable):
        def wrap(self, available_width, available_height):
            return (0, available_height+1) if self.canv.getPageNumber()==1 else (0,0)
        def draw(self):
            pass
    story.append(EnsurePromotionPage())
    doc.build(story)
    return path


def main():
    token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = clean(os.getenv("TELEGRAM_CHAT_ID"))
    mode = clean(os.getenv("NOTIFY_MODE", "evening")).lower()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets are missing.")
    if not CSV_PATH.exists() and not (CSV_PATH.parent / "organisation_tenders.csv").exists():
        raise FileNotFoundError("No available tender list or detail CSV")

    now = datetime.now(IST)
    today = now.date()
    d = today.strftime("%d-%m-%Y")
    rows = live_rows(load_report_rows())
    warning = HINDI_DISCLAIMER
    footer = f"🌐 वेबसाइट: {SITE_URL}\n📢 टेलीग्राम चैनल: {TELEGRAM_URL}\n👤 व्यवस्थापक: https://t.me/rdgyan\n\n{warning}"
    if mode == "morning":
        rows = morning_inventory_rows(rows, CSV_PATH.parent)
    if mode == "manual":
        report = clean(os.getenv("MANUAL_REPORT", "closing_today")).lower()
        view = clean(os.getenv("MANUAL_VIEW", "table")).lower()
        if report not in {"closing_today", "new_today", "all"} or view not in {"table", "card"}:
            raise RuntimeError("Invalid manual report or view")
    else:
        report = {"morning":"closing_today", "evening_new":"new_today", "evening_total":"all", "evening":"all"}.get(mode)
        view = "table"
        if report is None:
            raise RuntimeError(f"Unknown NOTIFY_MODE: {mode}")

    if report == "closing_today":
        selected = [r for r in rows if is_on_date(r.get("Closing Date"), today)]
        label, english = "आज अंतिम तिथि वाले टेंडर", "Closing Today"
    elif report == "new_today":
        selected = [r for r in rows if is_on_date(r.get("Published Date"), today)]
        label, english = "आज प्रकाशित नए टेंडर", "New Published Today"
    else:
        selected = rows
        label, english = "सभी चालू टेंडर", "All Tenders"
    selected = sorted(selected, key=closing_sort_key)
    result = os.getenv("MORNING_EXTRACTION_RESULT", "")
    fallback_note = ""
    if mode != "manual" and result and result != "success":
        fallback_note = "⚠️ नया डेटा संग्रह या जाँच पूरी नहीं हो सकी; उपलब्ध पिछले data से PDF भेजी जा रही है।\n"
    summary = {}
    try:
        with (CSV_PATH.parent / 'organisations.csv').open(encoding='utf-8-sig', newline='') as stream:
            summary['portal_tender_count'] = sum(int(re.sub(r'\D','',clean(r.get('Tender Count'))) or 0) for r in csv.DictReader(stream))
    except OSError:
        pass
    source_time = read_json_report(CSV_PATH.parent / 'data/inventory_counts.json').get('snapshot_at', '')
    message = (
        ("👤 व्यवस्थापक द्वारा भेजी गई टेंडर रिपोर्ट\n\n" if mode == 'manual' else "🔔 एमपी टेंडर्स अलर्ट\n\n")
        + f"📅 दिनांक: {today.strftime('%d/%m/%Y')}\n"
        + f"🕒 रिपोर्ट समय: {now.strftime('%d/%m/%Y %I:%M %p')} IST\n"
        + (f"📥 डेटा संग्रह समय: {source_time}\n" if source_time else "")
        + f"📋 {label}: {len(selected)}\n"
        + f"📊 PDF में {len(selected)} रिकॉर्ड; कुल उपलब्ध चालू टेंडर: {len(rows)}\n"
        + (f"🌐 पोर्टल सूची का कुल टेंडर count: {summary['portal_tender_count']}\n" if summary else "")
        + fallback_note
        + ("ℹ️ इस सूची में शून्य टेंडर हैं; शून्य रिकॉर्ड वाली PDF संलग्न है।\n" if not selected else "")
        + "\n" + footer
    )
    telegram_message(token, chat_id, message)
    title = f"{english} {d} • {len(selected)} tenders"
    filename = f"{english.replace(' ', '_')}_{d}.pdf"
    if view == 'card' and selected:
        from telegram_card_pdf import make_card_pdf
        pdf = make_card_pdf(selected, filename, title, total_available=len(rows), filter_detail=english, filter_live=False)
    else:
        pdf = make_pdf(selected, filename, title, total_available=len(rows), filter_detail=english, filter_live=False)
    telegram_document(token, chat_id, pdf, f"📎 {label} — {d} — {len(selected)} रिकॉर्ड\n\n{footer}")
    # Keep the established card companion for automatic morning/new reports.
    if mode in {'morning', 'evening_new'} and selected:
        from telegram_card_pdf import make_card_pdf
        card = make_card_pdf(selected, f"{english.replace(' ', '_')}_{d}_Card.pdf", title,
                             total_available=len(rows), filter_detail=english, filter_live=False)
        telegram_document(token, chat_id, card, f"📎 {label} — कार्ड प्रारूप — {len(selected)} रिकॉर्ड\n\n{footer}")
    return 0


def read_json_report(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


if __name__ == '__main__':
    raise SystemExit(main())
