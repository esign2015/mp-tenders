import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://mptenders.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page"


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def extract_rows(page):
    soup = BeautifulSoup(page.content(), "html.parser")
    candidates = []
    for table in soup.find_all("table"):
        headers = [clean(x.get_text(" ", strip=True)).lower() for x in table.find_all("th")]
        header_text = " | ".join(headers)
        if "e-published date" not in header_text and "title and ref.no./tender id" not in header_text:
            continue
        for tr in table.find_all("tr"):
            cells = [clean(x.get_text(" ", strip=True)) for x in tr.find_all("td")]
            if len(cells) >= 5:
                candidates.append(cells)
        if candidates:
            break
    return candidates


def find_captcha_input(page):
    labels = page.get_by_text(re.compile(r"Enter\s+Captcha", re.I))
    for i in range(labels.count()):
        try:
            box = labels.nth(i).locator("xpath=following::input[1]")
            if box.count() and box.first.is_visible():
                return box.first
        except Exception:
            pass
    inputs = page.locator("input[type='text']")
    visible = []
    for i in range(inputs.count()):
        try:
            if inputs.nth(i).is_visible():
                visible.append(inputs.nth(i))
        except Exception:
            pass
    return visible[-1] if visible else None


def choose_published_date(page):
    label = page.get_by_text(re.compile(r"^Published\s+Date$", re.I))
    if label.count():
        try:
            label.first.click()
            return
        except Exception:
            pass
    radios = page.locator("input[type='radio']")
    if radios.count():
        radios.first.check()


def click_search(page):
    btn = page.get_by_role("button", name=re.compile(r"^Search$", re.I))
    if btn.count():
        btn.first.click()
        return
    for selector in ["input[type='submit'][value*='Search']", "input[value='Search']"]:
        loc = page.locator(selector)
        if loc.count():
            loc.first.click()
            return
    raise RuntimeError("Search button नहीं मिला।")


def pagination_target(page):
    links = page.locator("a")
    next_link = None
    jump_link = None
    for i in range(links.count()):
        a = links.nth(i)
        try:
            if not a.is_visible():
                continue
            text = clean(a.inner_text())
            if text == "Next >":
                next_link = a
            elif text == ">>":
                jump_link = a
        except Exception:
            pass
    return next_link or jump_link


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1400, "height": 1000})
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1000)

        choose_published_date(page)
        captcha = find_captcha_input(page)
        if captcha is None:
            raise RuntimeError("CAPTCHA input नहीं मिला।")

        print("\nCAPTCHA browser window में दिखाई दे रहा है।")
        print("CAPTCHA देखकर नीचे terminal में केवल उसका text लिखें।")
        value = input("CAPTCHA: ").strip()
        if not value:
            raise RuntimeError("CAPTCHA खाली है।")
        captcha.fill(value)
        click_search(page)
        page.wait_for_load_state("domcontentloaded", timeout=60000)
        page.wait_for_timeout(800)

        target = datetime.now().strftime("%d-%b-%Y").lower()
        rows_out = []
        seen = set()
        page_no = 1

        while True:
            rows = extract_rows(page)
            today_rows = [r for r in rows if target in " ".join(r).lower()]

            for r in today_rows:
                key = " | ".join(r)
                if key not in seen:
                    seen.add(key)
                    rows_out.append(r)

            print(f"Page {page_no}: today={len(today_rows)}, collected_today={len(rows_out)}, rows={len(rows)}")

            if rows and not today_rows:
                break

            target_link = pagination_target(page)
            if target_link is None:
                break

            try:
                target_link.click()
                page.wait_for_load_state("domcontentloaded", timeout=60000)
                page.wait_for_timeout(500)
                page_no += 1
            except Exception as exc:
                print(f"Pagination stopped: {exc}")
                break

        out = Path("latest_active_today_test.csv")
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            f.write("S.No.\tPublished Date\tBid Submission Closing Date\tTender Opening Date\tTitle and Ref.No./Tender ID\tOrganisation Chain\tTender Value\n")
            for r in rows_out:
                f.write("\t".join(r).replace("\n", " ") + "\n")

        print(f"\nTEST COMPLETE: आज के {len(rows_out)} unique rows मिले।")
        print(f"CSV: {out.resolve()}")
        input("Result देखने के बाद Enter दबाएँ ताकि browser बंद हो...")

        browser.close()


if __name__ == "__main__":
    main()
