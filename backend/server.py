import csv, io, os, re, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / 'all_tenders_org_detailed.csv'
PORTAL = 'https://www.mptenders.gov.in/nicgep/app'
HEADERS = {'User-Agent': 'Mozilla/5.0 MP-Tender-Monitor/1.0'}
FIELDS = ['Tender ID','Published Date','Closing Date','Opening Date','Title','Reference Number','Organisation','Department','Division','Sub Division','Status']

def read_rows():
    if not CSV_FILE.exists(): return []
    with CSV_FILE.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_rows(rows):
    with CSV_FILE.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader(); w.writerows(rows)

def clean(s): return re.sub(r'\s+', ' ', s or '').strip()

def fetch_home():
    r = requests.get(PORTAL, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return BeautifulSoup(r.text, 'html.parser')

def parse_public_tables(soup):
    found=[]
    for table in soup.find_all('table'):
        rows=table.find_all('tr')
        for tr in rows[1:]:
            cells=[clean(x.get_text(' ', strip=True)) for x in tr.find_all(['td','th'])]
            if len(cells) < 3: continue
            text=' | '.join(cells)
            if any(x in text.lower() for x in ['tender title','corrigendum title']): continue
            # Public home page exposes title/reference/closing/opening; detail fields may be unavailable.
            title=cells[0]; ref=cells[1] if len(cells)>1 else ''
            close=cells[2] if len(cells)>2 else ''
            opening=cells[3] if len(cells)>3 else ''
            if title and ref and close:
                found.append({'Tender ID': '', 'Published Date': '', 'Closing Date': close,
                    'Opening Date': opening, 'Title': title, 'Reference Number': ref,
                    'Organisation':'', 'Department':'', 'Division':'', 'Sub Division':'', 'Status':'Open'})
    return found

@app.get('/')
def home(): return jsonify({'status':'online','message':'MP Tenders API is running','source':PORTAL})

@app.get('/health')
def health(): return jsonify({'status':'healthy','csv_exists':CSV_FILE.exists(),'records':len(read_rows())})

@app.get('/api/tenders')
def tenders(): return jsonify({'updated_at':datetime.now(timezone.utc).isoformat(),'count':len(read_rows()),'tenders':read_rows()})

@app.post('/api/fetch')
def fetch():
    soup=fetch_home(); public=parse_public_tables(soup)
    old=read_rows(); by_ref={r.get('Reference Number',''):r for r in old if r.get('Reference Number')}
    added=0
    for item in public:
        key=item['Reference Number']
        if key and key not in by_ref:
            by_ref[key]=item; added += 1
    rows=list(by_ref.values())
    write_rows(rows)
    return jsonify({'ok':True,'found_on_portal':len(public),'added':added,'total':len(rows),'note':'Public homepage data only; detailed organisation/tender-page crawling is not yet included.'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT','10000')))
