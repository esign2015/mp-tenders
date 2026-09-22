import csv
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://mptenders.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page"


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def extract_rows(page):
    """Find tender rows without depending on the portal's TH markup."""
    soup = BeautifulSoup(page.content(), "html.parser")
    best = []
    date_re = re.compile(r"\b\d{1,2}-[A-Za-z]{3}-\d{4}\b")

    for table in soup.find_all("table"):
        table_rows = []
        for tr in table.find_all("tr"):
            cells = [clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            if len(cells) < 5:
                continue

            text = " | ".join(cells)
            has_date = bool(date_re.search(text))
            has_tender_shape = (
                len(cells) >= 7
                and (
                    "Organisation Chain" in text
                    or "Tender Value" in text
                    or "Tender ID" in text
                    or "Ref.No." in text
                    or "e-Published Date" in text
                )
            )
            # Data rows normally have a date in the second cell and a long title/ref
            # in the fifth cell. Keep them even when headers are implemented as TDs.
            data_like = (
                len(cells) >= 7
                and has_date
                and (date_re.search(cells[1]) or date_re.search(cells[0]))
            )
            if has_tender_shape or data_like:
                table_rows.append(cells)

        if len(table_rows) > len(best):
            best = table_rows

    # Remove header-like rows while preserving actual tender rows.
    result = []
    for row in best:
        row_text = " | ".join(row).lower()
        if "e-published date" in row_text and "title and ref" in row_text:
            continue
        if len(row) >= 7 and re.search(r"\b\d{1,2}-[A-Za-z]{3}-\d{4}\b", row[1] if len(row) > 1 else ""):
            result.append(row)
    return result


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
    radios = page.locator("input[type='radio']")
    if radios.count():
        try:
            radios.first.check()
            return
        except Exception:
            pass
    label = page.get_by_text(re.compile(r"^Published\s+Date$", re.I))
    if label.count():
        label.first.click()


def click_search(page):
    for selector in [
        "input[type='submit'][value*='Search']",
        "input[value='Search']",
        "button:has-text('Search')",
    ]:
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


def wait_for_results(page):
    # The portal can update the result table after the navigation event.
    for _ in range(20):
        if extract_rows(page):
            return
        page.wait_for_timeout(500)


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
        page.wait_for_timeout(1200)
        wait_for_results(page)

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

            print(
                f"Page {page_no}: today={len(today_rows)}, "
                f"collected_today={len(rows_out)}, rows={len(rows)}"
            )

            # If the parser sees no tender rows at all, stop rather than blindly
            # walking through every pagination page.
            if not rows:
                page.screenshot(path=f"latest_active_debug_page_{page_no}.png", full_page=True)
                Path(f"latest_active_debug_page_{page_no}.html").write_text(
                    page.content(), encoding="utf-8"
                )
                print("No tender rows detected. Debug HTML/screenshot saved.")
                break

            # Published Date sorting means today's records are at the beginning.
            # Once a page has rows but none from today, today's block is finished.
            if page_no > 1 and not today_rows:
                break

            target_link = pagination_target(page)
            if target_link is None:
                break

            try:
                target_link.click()
                page.wait_for_timeout(800)
                wait_for_results(page)
                page_no += 1
            except Exception as exc:
                print(f"Pagination stopped: {exc}")
                break

        out = Path("latest_active_today_test.csv")
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "S.No.", "Published Date", "Bid Submission Closing Date",
                "Tender Opening Date", "Title and Ref.No./Tender ID",
                "Organisation Chain", "Tender Value"
            ])
            writer.writerows(rows_out)

        print(f"\nTEST COMPLETE: आज के {len(rows_out)} unique rows मिले.")
        print(f"CSV: {out.resolve()}")
        input("Result देखने के बाद Enter दबाएँ ताकि browser बंद हो...")

        browser.close()


if __name__ == "__main__":
    main()
