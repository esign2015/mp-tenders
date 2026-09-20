import csv
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request, parse

from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

IST = timezone(timedelta(hours=5, minutes=30))
SITE_URL = "https://tenders.codinglms.xyz/"
TELEGRAM_URL = "https://t.me/mptendersalert"
CSV_PATH = Path("organisation_tenders.csv")


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
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
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
        if '"ok":true' not in body:
            raise RuntimeError(body)


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
        if '"ok":true' not in result:
            raise RuntimeError(result)


def make_pdf(rows, filename, report_title):
    path = Path(filename)
    styles = getSampleStyleSheet()
    cell = ParagraphStyle(
        "cell", parent=styles["Normal"], fontName="Helvetica",
        fontSize=6.2, leading=7.4, textColor=colors.HexColor("#233044")
    )
    center = ParagraphStyle("center", parent=cell, alignment=TA_CENTER)
    subtitle = ParagraphStyle(
        "subtitle", parent=styles["Normal"], fontName="Helvetica",
        fontSize=7.5, leading=9, alignment=TA_CENTER,
        textColor=colors.HexColor("#4b5563")
    )

    doc = SimpleDocTemplate(
        str(path), pagesize=landscape(A3),
        leftMargin=18, rightMargin=18, topMargin=58, bottomMargin=24,
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
        Spacer(1, 7),
    ]

    header = [
        "S.No.", "Tender ID", "Closing Date", "Title", "Ref.No.",
        "PAC Amount", "EMD Fee", "Tender Fee", "Processing Fee", "Total Fee"
    ]
    data = [header]
    for i, row in enumerate(rows, 1):
        data.append([
            Paragraph(str(i), center),
            Paragraph(strip_brackets(row.get("Tender ID")), cell),
            Paragraph(clean(row.get("Closing Date")), center),
            Paragraph(strip_brackets(row.get("Title")), cell),
            Paragraph(strip_brackets(row.get("Reference Number")), cell),
            Paragraph(clean(row.get("PAC Amount")), center),
            Paragraph(clean(row.get("EMD Fee")), center),
            Paragraph(clean(row.get("Tender Fee")), center),
            Paragraph(clean(row.get("Processing Fee")), center),
            Paragraph(clean(row.get("Total Fee")), center),
        ])

    table = Table(
        data,
        colWidths=[28, 125, 95, 260, 250, 95, 75, 75, 90, 90],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#14376e")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 7),
        ("ALIGN", (0,0), (-1,0), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#cdd7e4")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#ebf3fc")]),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    story.append(table)

    generated = datetime.now(IST).strftime("%d/%m/%Y %I:%M %p IST")

    def page_header_footer(canvas, document):
        w, h = landscape(A3)
        canvas.saveState()

        # Header
        canvas.setFillColor(colors.HexColor("#14376e"))
        canvas.rect(0, h-32, w, 32, fill=1, stroke=0)

        # Telegram button - left
        canvas.setFillColor(colors.HexColor("#0088cc"))
        canvas.roundRect(18, h-25, 55, 12, 3, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawCentredString(45.5, h-21, "Get Daily Alert")
        canvas.linkURL(TELEGRAM_URL, (18, h-25, 73, h-13), relative=0)

        # Website button - right
        canvas.setFillColor(colors.HexColor("#2563eb"))
        canvas.roundRect(w-73, h-25, 55, 12, 3, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.drawCentredString(w-45.5, h-21, "Website")
        canvas.linkURL(SITE_URL, (w-73, h-25, w-18, h-13), relative=0)

        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawCentredString(w/2, h-10, "SAR Digital Services, Kannod")
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(w/2, h-18, "MP Tender Live Dashboard")
        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.drawCentredString(w/2, h-26, "For DSC & E-Tendering Services • Contact Admin: t.me/rdgyan")
        canvas.linkURL("https://t.me/rdgyan", (w/2-85, h-31, w/2+85, h-22), relative=0)

        # Footer
        canvas.setFillColor(colors.HexColor("#14376e"))
        canvas.rect(0, 0, w, 16, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(18, 6, "SAR Digital Services, Kannod • Contact Admin: t.me/rdgyan")
        canvas.linkURL("https://t.me/rdgyan", (18, 4, 125, 11), relative=0)
        canvas.drawRightString(w-18, 6, f"Page {document.page} • Generated {generated}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page_header_footer, onLaterPages=page_header_footer)
    return path


def main():
    token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = clean(os.getenv("TELEGRAM_CHAT_ID"))
    mode = clean(os.getenv("NOTIFY_MODE", "evening")).lower()

    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets are missing.")
    if not CSV_PATH.exists():
        raise FileNotFoundError(CSV_PATH)

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    today = datetime.now(IST).date()
    d = today.strftime("%d-%m-%Y")
    display = today.strftime("%d/%m/%Y")

    warning = (
        "⚠️ Disclaimer: This dashboard is an assistance tool only. Always verify the final tender notice, "
        "corrigendum, eligibility requirements, fees, and deadline on the official tender portal."
    )

    if mode == "morning":
        closing = sorted(
            [r for r in rows if is_on_date(r.get("Closing Date"), today)],
            key=closing_sort_key
        )
        message = (
            "🔔 एमपी टेंडर्स अलर्ट\n\n"
            f"📅 दिनांक: {display}\n\n"
            f"⏰ आज बंद होने वाले टेंडर: {len(closing)}\n\n"
            + ("📎 आज कोई भी टेंडर Closing Today में नहीं है, इसलिए इसकी PDF नहीं भेजी जा रही है।\n\n" if not closing else "")
            + f"🌐 वेबसाइट: {SITE_URL}\n"
            f"📢 टेलीग्राम चैनल: {TELEGRAM_URL}\n\n"
            f"⚠️ सूचना: यह डैशबोर्ड केवल सहायता के लिए है। अंतिम टेंडर सूचना, शुद्धिपत्र, पात्रता, शुल्क और अंतिम तिथि की पुष्टि आधिकारिक टेंडर पोर्टल से करें।\n\n"
            f"🕒 मॉर्निंग अलर्ट: {datetime.now(IST).strftime('%d/%m/%Y %I:%M %p')} IST"
        )
        pdf = make_pdf(
            closing,
            f"Closing Date {d} Tenders List on MPTenders.pdf",
            f"Closing Date {d} Tenders List on MPTenders • {len(closing)} tenders",
        )
        telegram_message(token, chat_id, message)
        if closing:
            telegram_document(
                token, chat_id, pdf,
                f"📎 Closing Date {d} Tenders List on MPTenders"
            )
        return 0

    new = sorted(
        [r for r in rows if is_on_date(r.get("Published Date"), today)],
        key=closing_sort_key
    )
    total_sorted = sorted(rows, key=closing_sort_key)

    message = (
        "🔔 एमपी टेंडर्स अलर्ट\n\n"
        f"📅 दिनांक: {display}\n\n"
        f"🆕 आज प्रकाशित नए टेंडर: {len(new)}\n\n"
        + ("📎 आज एक भी टेंडर प्रकाशित नहीं हुआ है, इसलिए New Published Tenders की PDF नहीं भेजी जा रही है।\n\n" if not new else "")
        + f"📋 आज तक कुल टेंडर: {len(rows)}\n\n"
        f"🌐 वेबसाइट: {SITE_URL}\n"
        f"📢 टेलीग्राम चैनल: {TELEGRAM_URL}\n\n"
        f"⚠️ सूचना: यह डैशबोर्ड केवल सहायता के लिए है। अंतिम टेंडर सूचना, शुद्धिपत्र, पात्रता, शुल्क और अंतिम तिथि की पुष्टि आधिकारिक टेंडर पोर्टल से करें।\n\n"
        f"🕒 अपडेट: {datetime.now(IST).strftime('%d/%m/%Y %I:%M %p')} IST"
    )

    total_pdf = make_pdf(
        total_sorted,
        f"Total Tenders as on {d} on MPTenders.pdf",
        f"Total Tenders as on {d} on MPTenders • {len(rows)} tenders",
    )
    new_pdf = make_pdf(
        new,
        f"New Publish Tender List on Date {d} on MPTenders.pdf",
        f"New Publish Tender List on Date {d} on MPTenders • {len(new)} tenders",
    )

    telegram_message(token, chat_id, message)
    telegram_document(
        token, chat_id, total_pdf,
        f"📎 Total Tenders as on {d} on MPTenders"
    )
    if new:
        telegram_document(
            token, chat_id, new_pdf,
            f"📎 New Publish Tender List on Date {d} on MPTenders"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
