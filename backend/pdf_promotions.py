"""Clickable SAR services and tender community cards shared by list PDFs."""
from reportlab.lib import colors

SERVICE_LINKS = (
    ('WhatsApp', 'https://wa.me/919893610244'),
    ('Telegram', 'https://t.me/rdgyan'),
    ('Email', 'mailto:imriteshdhoot@gmail.com'),
    ('Buy DSC', 'https://secure.certificate.digital/web/dsc/referral/?bp=fVNSQNJt@@@@@@L0='),
)
COMMUNITY_LINKS = (
    ('Join Telegram Channel', 'https://t.me/mptendersalert'),
    ('Join WhatsApp Group', 'https://chat.whatsapp.com/BuKI6bxZGHVBIHA6KZt7Vy'),
)
NAVY = colors.HexColor('#173d70')


def draw_promotion_card(canvas, kind, x, y, width, height):
    """Scale two bounded card layouts for A4 cards and A3 table page two."""
    scale = height / 220
    font_scale = min(1, width / 265)
    pad = 14
    def text(value, offset, size=10, color=NAVY, bold=False):
        canvas.setFillColor(color)
        canvas.setFont('Helvetica-Bold' if bold else 'Helvetica', size * font_scale)
        canvas.drawString(x + pad, y + height - offset * scale, value)
    def button(label, url, bx, by, bw, fill):
        bh = 25 * scale
        canvas.setFillColor(fill)
        canvas.roundRect(bx, by, bw, bh, 4, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont('Helvetica-Bold', max(7, 9 * min(scale, font_scale)))
        canvas.drawCentredString(bx + bw / 2, by + bh / 2 - 2.5, label)
        canvas.linkURL(url, (bx, by, bx + bw, by + bh), relative=0)
    canvas.saveState()
    services = kind == 'services'
    canvas.setFillColor(NAVY if services else colors.HexColor('#eff6ff'))
    canvas.setStrokeColor(colors.HexColor('#d4a514' if services else '#bfdbfe'))
    canvas.roundRect(x, y, width, height, 9, fill=1, stroke=1)
    if services:
        text('ADVERTISEMENT', 20, 7, colors.HexColor('#fde68a'), True)
        text('SAR Digital Services, Kannod', 48, 12, colors.white, True)
        text('DSC Sale | PWD Registration', 78, 10, colors.white, True)
        text('E-Tender Submission Assistance', 100, 10, colors.white)
        text('Rs 1,000.00 per tender', 127, 12, colors.HexColor('#fde68a'), True)
        bw = (width - 2 * pad - 8) / 2
        for i, (label, url) in enumerate(SERVICE_LINKS):
            button(label, url, x + pad + (i % 2) * (bw + 8),
                   y + (16 + (1 - i // 2) * 34) * scale, bw, colors.HexColor('#2563eb'))
    else:
        text('MP Tender Alerts', 40, 15, NAVY, True)
        text('Closing Today list daily at 8 AM IST', 76, 9)
        text('New tender updates and useful alerts', 98, 9)
        for i, (label, url) in enumerate(COMMUNITY_LINKS):
            button(label, url, x + pad, y + (28 + (1 - i) * 43) * scale,
                   width - 2 * pad, colors.HexColor('#0284c7' if i == 0 else '#16a34a'))
    canvas.restoreState()


TABLE_PROMOTION_HEIGHT = 64
TABLE_PROMOTION_BOTTOM = 24


def draw_table_promotions(canvas, page_width, page_height):
    """Two compact card-style adverts below the table, above the footer."""
    margin, gap, height, y = 24, 12, TABLE_PROMOTION_HEIGHT, TABLE_PROMOTION_BOTTOM
    width = (page_width - 2 * margin - gap) / 2
    canvas.saveState()
    for kind, x in (('services', margin), ('community', margin + width + gap)):
        services = kind == 'services'
        canvas.setFillColor(NAVY if services else colors.HexColor('#eff6ff'))
        canvas.setStrokeColor(colors.HexColor('#d4a514' if services else '#bfdbfe'))
        canvas.roundRect(x, y, width, height, 6, fill=1, stroke=1)
        canvas.setFillColor(colors.white if services else NAVY)
        canvas.setFont('Helvetica-Bold', 10)
        canvas.drawString(x + 12, y + height - 15,
                          'SAR Digital Services, Kannod' if services else 'MP Tender Alerts')
        canvas.setFont('Helvetica', 8)
        canvas.drawString(x + 12, y + height - 29,
                          'DSC Sale | PWD Registration | E-Tender Submission Assistance'
                          if services else 'Today closing: 8 AM | Tomorrow closing: 4 PM | New tenders: evening')
        if services:
            canvas.setFillColor(colors.HexColor('#fde68a'))
            canvas.setFont('Helvetica-Bold', 9)
            canvas.drawRightString(x + width - 12, y + height - 15, 'Rs 1,000.00 per tender')
        links = SERVICE_LINKS if services else COMMUNITY_LINKS
        button_width = (width - 24 - 6 * (len(links) - 1)) / len(links)
        for index, (label, url) in enumerate(links):
            bx, by = x + 12 + index * (button_width + 6), y + 7
            canvas.setFillColor(colors.HexColor('#2563eb' if services else '#0284c7' if index == 0 else '#16a34a'))
            canvas.roundRect(bx, by, button_width, 16, 3, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont('Helvetica-Bold', 7.5)
            canvas.drawCentredString(bx + button_width / 2, by + 5, label)
            canvas.linkURL(url, (bx, by, bx + button_width, by + 16), relative=0)
    canvas.restoreState()
