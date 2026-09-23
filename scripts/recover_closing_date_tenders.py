#!/usr/bin/env python3
import csv,re
from pathlib import Path
from datetime import datetime,timezone
from playwright.sync_api import sync_playwright

URL="https://mptenders.gov.in/nicgep/app?page=FrontEndListTendersbyDate&service=page"
OUT=Path("data/closing_date_recovery.csv")

def clean(v): return re.sub(r"\s+"," ",v or "").strip()
def tid(v):
    m=re.findall(r"\b20\d{2}_[A-Z0-9_./-]+?_\d+\b",v or "",re.I)
    return m[-1] if m else ""

with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    page=b.new_page(viewport={"width":1920,"height":1080})
    page.goto(URL,wait_until="domcontentloaded",timeout=60000)
    page.get_by_text("Closing within 14 days",exact=True).first.click(timeout=15000)
    page.wait_for_load_state("domcontentloaded",timeout=30000)
    page.wait_for_timeout(800)
    rows={}
    for _ in range(100):
        for tr in page.locator("table").filter(has_text="Organisation Chain").locator("tbody tr").all():
            cells=[clean(x) for x in tr.locator("td").all_text_contents()]
            if len(cells)<6: continue
            x=tid(cells[4])
            if not x: continue
            parts=re.findall(r"\[([^\]]+)\]",cells[4])
            ref=parts[-1] if parts else ""
            title=re.sub(r"\s*\[[^\]]+\]\s*\[[^\]]+\]\s*$","",cells[4]).strip()
            rows[x]={
                "Tender ID":x,"Organisation Name":clean(cells[5].split("||",1)[0]),
                "Organisation Chain":clean(cells[5]),"Title":title,
                "Reference Number":ref,"E-Published Date":clean(cells[1]),
                "Closing Date":clean(cells[2]),"Opening Date":clean(cells[3]),
                "Source":"Closing within 14 days","Retrieved At":datetime.now(timezone.utc).isoformat()
            }
        nxt=page.locator("a").filter(has_text=re.compile(r"^>$")).first
        if not nxt.count() or not nxt.is_visible(): break
        try:
            nxt.click(timeout=10000)
            page.wait_for_load_state("domcontentloaded",timeout=20000)
            page.wait_for_timeout(500)
        except Exception: break
    b.close()

OUT.parent.mkdir(parents=True,exist_ok=True)
fields=["Tender ID","Organisation Name","Organisation Chain","Title","Reference Number","E-Published Date","Closing Date","Opening Date","Source","Retrieved At"]
with OUT.open("w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows.values())
print("recovered",len(rows),"tender IDs")
