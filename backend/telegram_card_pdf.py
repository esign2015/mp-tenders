"""A separate six-card A4 report with the dashboard's SAR advertisement."""
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph

AD_LINKS = (
    ('WhatsApp', 'https://wa.me/919893610244'),
    ('Telegram', 'https://t.me/rdgyan'),
    ('Email', 'mailto:imriteshdhoot@gmail.com'),
    ('Buy DSC', 'https://secure.certificate.digital/web/dsc/referral/?bp=fVNSQNJt@@@@@@L0='),
)
NAVY = colors.HexColor('#173d70')
GOLD = colors.HexColor('#d4a514')
MUTED = colors.HexColor('#64748b')


def paragraph(canvas, text, x, top, width, height, size=9, bold=False, color=NAVY):
    """Fit bounded text without crossing the next card field."""
    style = ParagraphStyle('card', fontName='Helvetica-Bold' if bold else 'Helvetica', fontSize=size,
                           leading=size * 1.25, textColor=color, splitLongWords=True)
    text = ' '.join(str(text or '-').split())
    def make(value):
        p = Paragraph(escape(value), style)
        _, h = p.wrap(width, 10000)
        return p, h
    p, h = make(text)
    if h > height:
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if make(text[:mid].rstrip() + '...')[1] <= height:
                lo = mid
            else:
                hi = mid - 1
        p, h = make(text[:lo].rstrip() + '...')
    p.drawOn(canvas, x, top - h)


def button(canvas, label, url, x, y, width, fill=NAVY):
    canvas.setFillColor(fill)
    canvas.roundRect(x, y, width, 20, 5, fill=1, stroke=0)
    canvas.setFont('Helvetica-Bold', 8)
    canvas.setFillColor(colors.white)
    canvas.drawCentredString(x + width / 2, y + 6, label)
    canvas.linkURL(url, (x, y, x + width, y + 20), relative=0)


def ad_card(canvas, x, y, width, height):
    canvas.setFillColor(NAVY)
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(1.5)
    canvas.roundRect(x, y, width, height, 10, fill=1, stroke=1)
    top = y + height
    paragraph(canvas, 'ADVERTISEMENT', x+14, top-12, width-28, 12, 7, True, GOLD)
    canvas.setFillColor(GOLD)
    canvas.roundRect(x+14, top-60, 50, 28, 5, fill=1, stroke=0)
    canvas.setFont('Helvetica-Bold', 16)
    canvas.setFillColor(NAVY)
    canvas.drawCentredString(x+39, top-51, 'SAR')
    paragraph(canvas, 'SAR Digital Services, Kannod', x+74, top-29, width-88, 31, 11, True, colors.white)
    paragraph(canvas, 'DSC Sale | PWD Registration | Tender Submission', x+14, top-74, width-28, 34, 11, True, colors.white)
    paragraph(canvas, 'Tender Submission only Rs 1,000.00 per tender.', x+14, top-111, width-28, 27, 10, True, GOLD)
    paragraph(canvas, 'DSC and PWD registration assistance available.', x+14, top-143, width-28, 18, 7, False, colors.white)
    bw = (width-38)/2
    for i,(label,url) in enumerate(AD_LINKS):
        button(canvas,label,url,x+14+(i%2)*(bw+10),y+12+(1-i//2)*28,bw,colors.HexColor('#2563eb'))


def tender_card(canvas, row, number, x, y, width, height):
    from telegram_alerts import clean, strip_brackets, valid_reference, fee_amount, fee_text, total_tender_fee
    canvas.setFillColor(colors.white)
    canvas.setStrokeColor(colors.HexColor('#cbd5e1'))
    canvas.setLineWidth(.7)
    canvas.roundRect(x,y,width,height,10,fill=1,stroke=1)
    top=y+height
    canvas.setFillColor(NAVY)
    canvas.roundRect(x,y+height-25,width,25,10,fill=1,stroke=0)
    canvas.rect(x,y+height-25,width,15,fill=1,stroke=0)
    paragraph(canvas, f'{number}. {clean(row.get("Tender ID"))}',x+12,top-7,width-24,14,8.2,True,colors.white)
    paragraph(canvas, strip_brackets(row.get('Title')),x+12,top-33,width-24,30,9,True)
    paragraph(canvas, 'Ref: '+(valid_reference(row.get('Reference Number')) or '-'),x+12,top-67,width-24,18,7,False,MUTED)
    canvas.setFillColor(colors.HexColor('#fff1f2'))
    canvas.roundRect(x+10,top-111,width-20,22,4,fill=1,stroke=0)
    paragraph(canvas,'Closing: '+clean(row.get('Closing Date')),x+17,top-95,width-34,13,8,True,colors.HexColor('#b91c1c'))
    org = clean(row.get('Organisation')) or ' | '.join(clean(row.get(k)) for k in ('Organisation Name','Department','Division','Sub Division') if clean(row.get(k)))
    paragraph(canvas,org or '-',x+12,top-116,width-24,18,7,False,MUTED)
    place=' | '.join(clean(row.get(k)) for k in ('District','Location','Pincode') if clean(row.get(k)))
    paragraph(canvas,place or 'Location: -',x+12,top-138,width-24,18,7,False,MUTED)
    pac = 'NA' if clean(row.get('PAC Amount')).upper() in {'NA','N/A'} else fee_text(fee_amount(row.get('PAC Amount')))
    values=(('PAC',pac),('EMD',fee_text(fee_amount(row.get('EMD Fee')))),('Form Fee',fee_text(fee_amount(row.get('Tender Fee')))),('Processing',fee_text(fee_amount(row.get('Processing Fee')))))
    for i,(label,value) in enumerate(values):
        cx=x+12+(i%2)*(width-24)/2
        cy=top-160-(i//2)*23
        paragraph(canvas,label,cx,cy,(width-30)/2,9,6.3,False,MUTED)
        paragraph(canvas,value,cx,cy-8,(width-30)/2,11,7.8,True)
    paragraph(canvas,'Total Fee: '+fee_text(total_tender_fee(row)),x+12,y+17,width-24,11,8,True)


def make_card_pdf(rows, filename, report_title, total_available=None, filter_detail='All Tenders', filter_live=True):
    from telegram_alerts import live_rows, closing_sort_key, IST, SITE_URL, TELEGRAM_URL
    rows=sorted(live_rows(rows) if filter_live else list(rows),key=closing_sort_key)
    total_available=len(rows) if total_available is None else total_available
    slots=[None]
    for i,row in enumerate(rows,1):
        slots.append((i,row))
        if i%12==0 and i<len(rows):slots.append(None)
    pages=(len(slots)+5)//6
    path=Path(filename)
    path.parent.mkdir(parents=True,exist_ok=True)
    canvas=Canvas(str(path),pagesize=A4)
    canvas.setTitle(report_title+' - Card View')
    canvas.setAuthor('SAR Digital Services, Kannod')
    w,h=A4
    margin,gap=22,10
    cw=(w-2*margin-gap)/2
    ch=(h-100-55-2*gap)/3
    generated=datetime.now(IST).strftime('%d/%m/%Y %I:%M %p IST')
    for page in range(pages):
        canvas.setFillColor(colors.HexColor('#f4f7fb'));canvas.rect(0,0,w,h,fill=1,stroke=0)
        canvas.setFillColor(NAVY);canvas.rect(0,h-85,w,85,fill=1,stroke=0)
        paragraph(canvas,'SAR Digital Services, Kannod',margin,h-14,w-2*margin,22,16,True,colors.white)
        paragraph(canvas,report_title+' - Card View',margin,h-40,w-2*margin,20,10,True,colors.white)
        paragraph(canvas,f'Total Records: {len(rows)} out of {total_available} | {filter_detail}',margin,h-63,w-2*margin,15,9,False,colors.white)
        for slot,item in enumerate(slots[page*6:(page+1)*6]):
            x=margin+(slot%2)*(cw+gap)
            y=h-100-(slot//2)*(ch+gap)-ch
            if item is None:ad_card(canvas,x,y,cw,ch)
            else:tender_card(canvas,item[1],item[0],x,y,cw,ch)
        paragraph(canvas,'This dashboard is an assistance tool. Always verify the final tender notice, corrigendum, eligibility, fee and deadline on the official tender portal.',margin,42,w-2*margin,18,7,False,MUTED)
        canvas.setFont('Helvetica',7);canvas.setFillColor(NAVY)
        canvas.drawString(margin,13,'tenders.codinglms.xyz | Telegram: @mptendersalert')
        canvas.linkURL(SITE_URL,(margin,11,160,22),relative=0)
        canvas.linkURL(TELEGRAM_URL,(160,11,340,22),relative=0)
        canvas.drawRightString(w-margin,13,f'Page {page+1}/{pages} | {generated}')
        canvas.showPage()
    canvas.save()
    return path
