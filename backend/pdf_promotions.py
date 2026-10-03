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


def draw_table_promotions(canvas, page_width, page_height, top=78):
    gap, margin, height = 16, 24, 150
    width = (page_width - 2 * margin - gap) / 2
    y = page_height - top - height
    draw_promotion_card(canvas, 'services', margin, y, width, height)
    draw_promotion_card(canvas, 'community', margin + width + gap, y, width, height)
