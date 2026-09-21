import csv, json, os, re, time
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from scraper import parse_detail, parse_tender_rows, PORTAL

IDS = [
    "2026_CPA_534387_1",
    "2026_HED_530874_2",
    "2026_HED_533520_2",
    "2026_HED_527553_1",
    "2026_DFWAD_533409_1",
]

OUT = Path("manual_detail_test.csv")
STATUS = Path("data/manual_detail_test_status.json")

def clean(s):
    return re.sub(r"\s+"," ",str(s or "")).strip()

def visible_text_inputs(scope):
    return [x for x in scope.locator("input").all() if x.is_visible() and (x.get_attribute("type") or "text").lower() in ("text","search")]

def find_search_area(page):
    heading=page.get_by_text("Search with ID/Title/Reference no",exact=False).first
    if heading.count()==0:return page
    for level in range(1,7):
        try:
            node=heading.locator("xpath=" + "/.."*level)
            if visible_text_inputs(node):return node
        except Exception:pass
    return page

def do_search(page,tender_id):
    page.goto(PORTAL,wait_until="domcontentloaded",timeout=90000)
    page.wait_for_timeout(1500)
    area=find_search_area(page)
    inputs=visible_text_inputs(area)
    if not inputs:
        inputs=[x for x in page.locator("input").all() if x.is_visible() and (x.get_attribute("type") or "text").lower() in ("text","search")]
    if not inputs:raise RuntimeError("Search Tender input not found")
    chosen=None
    for inp in inputs:
        meta=" ".join(clean(inp.get_attribute(a)) for a in ("id","name","placeholder","title","class"))
        if re.search(r"search|tender|reference|id",meta,re.I):
            chosen=inp;break
    chosen=chosen or inputs[-1]
    chosen.fill(tender_id)
    buttons=[b for b in area.locator("input,button,a").all() if b.is_visible()]
    go=None
    for b in buttons:
        txt=clean(b.inner_text() if b.evaluate("(e)=>e.tagName==='BUTTON' || e.tagName==='A'") else b.get_attribute("value") or b.get_attribute("title") or b.get_attribute("alt"))
        meta=" ".join(clean(b.get_attribute(a)) for a in ("id","name","value","title","alt","class"))
        if re.fullmatch(r"go|search",txt,re.I) or re.search(r"\b(go|search)\b",meta,re.I):
            go=b;break
    if not go:
        form=chosen.locator("xpath=ancestor::form[1]")
        if form.count():go=form.locator("input[type='submit'],button").first
    if not go or go.count()==0:raise RuntimeError("GO/Search button not found")
    go.click()
    page.wait_for_load_state("domcontentloaded",timeout=90000)
    page.wait_for_timeout(2500)
    # IMPORTANT: GO only shows the SEARCH RESULT page. The detail page is
    # reached only by clicking the actual Tender Title link in that result row.
    result_soup=BeautifulSoup(page.content(),"html.parser")
    result_rows=parse_tender_rows(result_soup,page.url)
    match=next((r for r in result_rows if clean(r.get("tender_id")).casefold()==tender_id.casefold()),None)
    if not match:
        raise RuntimeError(f"Search returned no matching Tender row for {tender_id}; url={page.url}")
    title=clean(match.get("title"))
    reference=clean(match.get("reference"))
    if not title:
        raise RuntimeError(f"Tender Title not identified in search result row for {tender_id}")
    title_link=None
    anchors=page.locator("a")
    for i in range(anchors.count()):
        a=anchors.nth(i)
        if not a.is_visible():continue
        txt=clean(a.inner_text())
        if title.casefold() in txt.casefold() or txt.casefold()==title.casefold():
            title_link=a
            break
    if title_link is None:
        raise RuntimeError(f"Actual Tender Title link not found after GO for {tender_id}; title={title[:160]}")
    title_link.click()
    page.wait_for_load_state("domcontentloaded",timeout=90000)
    page.wait_for_timeout(2500)
    soup=BeautifulSoup(page.content(),"html.parser")
    detail=parse_detail(soup,page.url)
    if clean(detail.get("Tender ID"))!=tender_id:
        body=clean(soup.get_text(" ",strip=True))
        if tender_id.casefold() not in body.casefold():raise RuntimeError(f"Detail page does not contain requested Tender ID; got={detail.get('Tender ID','')}")
        detail["Tender ID"]=tender_id
    detail["Search Route"]="Home Search -> Tender ID -> GO -> Tender Title"
    detail["Search Result Title"]=title
    detail["Tested At"]=datetime.now(timezone.utc).isoformat()
    return detail

def main():
    rows=[];errors=[]
    with sync_playwright() as p:
        # Each Tender ID gets a completely fresh Chromium browser/session.
        # No MP Tender session URL is copied or reused between tenders.
        for tid in IDS:
            browser=None
            context=None
            try:
                print(f"TEST START {tid} — NEW BROWSER",flush=True)
                browser=p.chromium.launch(headless=True)
                context=browser.new_context(
                    locale="en-IN",
                    timezone_id="Asia/Kolkata",
                    viewport={"width":1366,"height":900}
                )
                page=context.new_page()
                detail=do_search(page,tid)
                # Never persist even the base/session navigation URL in test data.
                detail.pop("URL",None)
                rows.append(detail)
                print(f"TEST OK {tid} — BROWSER WILL CLOSE",flush=True)
            except Exception as e:
                errors.append({"Tender ID":tid,"error":f"{type(e).__name__}: {e}"})
                print(f"TEST FAIL {tid}: {type(e).__name__}: {e}",flush=True)
            finally:
                if context:
                    context.close()
                if browser:
                    browser.close()
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    if fields:
        with OUT.open("w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)
    STATUS.parent.mkdir(parents=True,exist_ok=True)
    STATUS.write_text(json.dumps({"tested":len(IDS),"success":len(rows),"failed":len(errors),"errors":errors,"updated_at":datetime.now(timezone.utc).isoformat(),"route":"Home -> Search with ID/Title/Reference no -> Tender ID -> GO -> Tender Title -> detail"},indent=2),encoding="utf-8")
    print(json.dumps({"tested":len(IDS),"success":len(rows),"failed":len(errors),"errors":errors},indent=2))
    if errors:raise SystemExit(1)

if __name__=="__main__":main()

# manual test trigger 2026-09-21
