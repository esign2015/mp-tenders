import csv
import os
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request

from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

IST = timezone(timedelta(hours=5, minutes=30))
SITE_URL = "https://esign2015.github.io/mp-tenders/"
TELEGRAM_URL = "https://t.me/mptendersalert"
CSV_PATH = Path("organisation_tenders.csv")
PDF_PATH = Path("MP_Tender_Current_Data_Test.pdf")


def clean(v):
    return str(v or "").strip()


def strip_brackets(v):
    return re.sub(r"^\[|\]$", "", clean(v))


def make_pdf(rows):
    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=landscape(A3),
        rightMargin=18,
        leftMargin=18,
        topMargin=42,
        bottomMargin=28,
        title="MP Tender Current Data Test Report",
        author="SAR Digital Services, Kannod",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title2", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=17, leading=20, textColor=colors.HexColor("#14376e"),
        alignment=TA_CENTER, spaceAfter=4
    )
    sub_style = ParagraphStyle(
        "sub2", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8.5, leading=11, alignment=TA_CENTER
    )
    cell_style = ParagraphStyle(
        "cell2", parent=styles["Normal"], fontName="Helvetica",
        fontSize=6.5, leading=8
    )
    cell_center = ParagraphStyle(
        "cellc2", parent=cell_style, alignment=TA_CENTER
    )

    story = [
        Paragraph("SAR Digital Services, Kannod", title_style),
        Paragraph("MP Tender Live Dashboard • Current Tender Data Test Report", sub_style),
        Paragraph(
            "This PDF contains the tender-list data currently available in the repository. "
            "Always verify the final tender notice, corrigendum, eligibility requirements, fees, "
            "and deadline on the official tender portal.",
            sub_style,
        ),
        Spacer(1, 8),
    ]

    header = [
        "S.No.", "Tender ID", "Closing Date", "Title", "Ref.No.",
        "PAC Amount", "EMD Fee", "Tender Fee", "Processing Fee", "Total Fee"
    ]
    data = [header]

    for i, row in enumerate(rows, 1):
        data.append([
            Paragraph(str(i), cell_center),
            Paragraph(strip_brackets(row.get("Tender ID")), cell_style),
            Paragraph(clean(row.get("Closing Date")), cell_center),
            Paragraph(strip_brackets(row.get("Title")), cell_style),
            Paragraph(strip_brackets(row.get("Reference Number")), cell_style),
            Paragraph(clean(row.get("PAC Amount")), cell_center),
            Paragraph(clean(row.get("EMD Fee")), cell_center),
            Paragraph(clean(row.get("Tender Fee")), cell_center),
            Paragraph(clean(row.get("Processing Fee")), cell_center),
            Paragraph(clean(row.get("Total Fee")), cell_center),
        ])

    col_widths = [28, 125, 95, 260, 250, 95, 75, 75, 90, 90]
    table = Table(data, colWidths=col_widths, repeatRows=1)
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

    def footer(canvas, doc):
        w, h = landscape(A3)
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#14376e"))
        canvas.rect(0, 0, w, 18, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(18, 7, "SAR Digital Services, Kannod • MP Tender Live Dashboard")
        canvas.drawRightString(
            w - 18, 7,
            f"Page {doc.page} • Daily Tender Alerts: {TELEGRAM_URL}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def send_document(token, chat_id, path, caption):
    boundary = "----MPTenderBoundary7f3c"
    parts = []

    def field(name, value):
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )

    field("chat_id", chat_id)
    field("caption", caption)

    body = b"".join(p.encode("utf-8") for p in parts)
    file_bytes = path.read_bytes()
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with request.urlopen(req, timeout=60) as response:
        result = response.read().decode("utf-8")
        if '"ok":true' not in result:
            raise RuntimeError(f"Telegram sendDocument failed: {result}")


def send_message(token, chat_id, text):
    from urllib.parse import urlencode
    data = urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "false",
    }).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with request.urlopen(request.Request(url, data=data), timeout=30) as response:
        result = response.read().decode("utf-8")
        if '"ok":true' not in result:
            raise RuntimeError(f"Telegram sendMessage failed: {result}")


def main():
    token = clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = clean(os.getenv("TELEGRAM_CHAT_ID"))
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets are missing.")
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"{CSV_PATH} not found")

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    make_pdf(rows)

    today = datetime.now(IST).strftime("%d/%m/%Y")
    org_counts = Counter(clean(r.get("Organisation Name")) for r in rows if clean(r.get("Organisation Name")))
    org_count = len(org_counts)
    today_new = 0
    for r in rows:
        value = clean(r.get("Published Date"))
        if re.search(rf"(?<!\d){re.escape(datetime.now(IST).strftime('%d/%m/%Y'))}(?!\d)", value):
            today_new += 1

    message = (
        "🔔 MP Tenders Alert Bot\n\n"
        f"📅 Date: {today}\n"
        f"🆕 Today’s New Published Tenders: {today_new}\n"
        f"🏢 Organisation Summary: {org_count} organisations / {len(rows)} tender records\n\n"
        f"🌐 Website: {SITE_URL}\n"
        "📎 Current Tender Data PDF: attached\n\n"
        f"📢 Daily alert पाने के लिए Telegram channel join करें: {TELEGRAM_URL}\n\n"
        "⚠️ This dashboard is an assistance tool only. Always verify the final tender notice, "
        "corrigendum, eligibility requirements, fees, and deadline on the official tender portal.\n\n"
        f"🕒 Updated: {datetime.now(IST).strftime('%d/%m/%Y %I:%M %p')} IST"
    )

    send_message(token, chat_id, message)
    send_document(
        token,
        chat_id,
        PDF_PATH,
        "📎 MP Tender Current Data PDF • Test message"
    )
    print(f"TEST Telegram message + PDF sent. Rows: {len(rows)}, organisations: {org_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
