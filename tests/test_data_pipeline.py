import csv
import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import scraper
import existing_id_detail as worker
import publish_data_checkpoint as publisher
import import_rsp_live
import apply_verified_details
import home_latest
import nightly_cleanup as cleanup
from datetime import datetime
from bs4 import BeautifulSoup
from inventory_summary import REQUIRED_FIELDS, build_summary
from admin_mismatch_alert import resolve_private_admin, send_alert

def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def completed(tid):
    return {**{key: "value" for key in REQUIRED_FIELDS}, "Tender ID": tid,
            "Processing Fee": "295", "Detail Extracted": "YES"}

class OrganisationAndParallelTests(unittest.TestCase):
    def test_live_count_uses_exact_cell_not_ancestor_table(self):
        class Locator:
            def __init__(self, items): self.items = items
            def count(self): return len(self.items)
            def nth(self, i): return self.items[i]
        class Cell:
            def __init__(self, text, anchors=()): self.text, self.anchors = text, anchors
            def inner_text(self): return self.text
            def locator(self, selector): return Locator(self.anchors)
        class Row:
            def __init__(self, cells): self.cells = cells
            def locator(self, selector):
                self.assert_selector = selector
                return Locator(self.cells)
        first = Cell("3")
        target = Cell("2,993")
        rows = [Row([Cell("Atal 3 Directorate Urban 2993", [first, target])]),
                Row([Cell("1"), Cell("Atal"), Cell("3", [first])]),
                Row([Cell("22"), Cell("Directorate Urban"), Cell("2993", [target])])]
        page = SimpleNamespace(locator=lambda selector: Locator(rows))
        org = {"name": "Directorate Urban", "count": 3000}
        with patch.object(scraper, "open_organisation_page_from_home"), \
             patch.object(scraper, "click_live_anchor", return_value="detail") as click:
            self.assertEqual(scraper.open_organisation_list_by_click(page, org), "detail")
        click.assert_called_once_with(page, target)
        self.assertEqual(org["count"], 2993)

    def test_parallel_sessions_keep_id_association_and_isolate_failure(self):
        from threading import Barrier
        barrier = Barrier(3)
        pages = []
        def playwright():
            page = object()
            pages.append(page)
            context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
            browser = SimpleNamespace(new_context=lambda **kw: context, close=lambda: None)
            return nullcontext(SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser)))
        def search(page, tid):
            barrier.wait(timeout=5)
            if tid == "bad": raise ValueError("failed tender")
            return {"Tender ID": tid}
        with patch.object(worker, "sync_playwright", side_effect=playwright), \
             patch.object(worker, "do_search", side_effect=search):
            results = list(worker.iter_id_search_results([{"Tender ID": tid} for tid in ("one", "bad", "two")], 3))
        self.assertEqual(len({id(page) for page in pages}), 3)
        self.assertEqual(len(results), 3)
        for base, detail, error in results:
            if base["Tender ID"] == "bad": self.assertIsInstance(error, ValueError)
            else: self.assertEqual(base["Tender ID"], detail["Tender ID"])

class CleanupTests(unittest.TestCase):
    def test_cleanup_expires_even_portal_listed_rows_but_keeps_extensions_and_unknown_dates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = datetime(2026, 9, 30, 19, 0, tzinfo=cleanup.IST)
            rows = [{"Tender ID": tid, "Closing Date": closing} for tid, closing in (
                ("expired", "30-Sep-2026 06:55 PM"), ("exact", "30-Sep-2026 07:00 PM"),
                ("extended", "01-Oct-2026 06:00 PM"), ("unknown", "unavailable"))]
            for name in cleanup.DATA_FILES:
                write_csv(root / name, rows)
            (root / "data").mkdir()
            (root / "data/live_snapshot.json").write_text(json.dumps({"tender_ids": [r["Tender ID"] for r in rows]}))
            cleanup.cleanup(root, now)
            for name in cleanup.DATA_FILES:
                with (root / name).open(encoding="utf-8-sig") as stream:
                    remaining = list(csv.DictReader(stream))
                self.assertEqual([r["Tender ID"] for r in remaining], ["extended", "unknown"])
            self.assertEqual(json.loads((root / "data/live_snapshot.json").read_text())["tender_ids"], ["extended", "unknown"])
            report = json.loads((root / "data/cleanup_status.json").read_text())
            self.assertEqual(report["retention_hours"], 0)

    def test_all_expired_is_valid_header_only_csv(self):
        data = b'Tender ID,Closing Date\nexpired,30-Sep-2026 06:00 PM\n'
        result, removed = cleanup.purge_csv(data, datetime(2026,9,30,19,tzinfo=cleanup.IST))
        self.assertEqual(removed, ["expired"])
        self.assertEqual(list(csv.DictReader(result.decode("utf-8-sig").splitlines())), [])

class PipelineTests(unittest.TestCase):
    def test_next_fourteen_days_and_nearest_bid_deadline_come_before_missing_pincode(self):
        from datetime import timezone
        now = datetime(2026,9,30,8,0,tzinfo=timezone.utc)
        rows = [
            {"Tender ID":"missing-pin", "Pincode":"", "Closing Date":"20-Oct-2026 05:00 PM"},
            {"Tender ID":"soon", "Pincode":"482001", "Closing Date":"02-Oct-2026 05:00 PM"},
            {"Tender ID":"bid-soonest", "Pincode":"462001", "Closing Date":"25-Oct-2026 05:00 PM", "Bid Submission End Date":"01-Oct-2026 05:00 PM"},
            {"Tender ID":"unknown", "Pincode":""},
            {"Tender ID":"fourteen-day-boundary", "Closing Date":"14-Oct-2026 01:30 PM"},
        ]
        ordered = sorted(rows,key=lambda row:worker.extraction_priority(row,now))
        self.assertEqual([row["Tender ID"] for row in ordered], ["bid-soonest","soon","fourteen-day-boundary","missing-pin","unknown"])
        self.assertEqual(worker.extraction_priority(ordered[2],now)[0],0)

    def test_verified_pincode_repair_preserves_other_tenders_and_survives_stale_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = {**completed("reported"), "Pincode":"", "Tested At":"2026-09-29T00:00:00+00:00"}
            other = {**completed("other"), "Tested At":"2026-09-29T00:00:00+00:00"}
            for name in ("all_tenders_org_detailed.csv","tender_details.csv"):
                write_csv(root/name,[old,other])
            fix = {**completed("reported"), "Pincode":"482001", "Tested At":"2026-09-30T08:00:00+00:00"}
            apply_verified_details.apply(root,[fix])
            for name in ("all_tenders_org_detailed.csv","tender_details.csv"):
                with (root/name).open(encoding="utf-8-sig") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(rows[0]["Pincode"],"482001")
                self.assertEqual(rows[1],other)
            with (root/"all_tenders_org_detailed.csv").open(encoding="utf-8-sig") as stream:
                corrected = stream.read().encode("utf-8-sig")
            write_csv(root/"stale.csv",[old,other])
            merged = publisher.merge_details(corrected,(root/"stale.csv").read_bytes())
            self.assertEqual(list(csv.DictReader(merged.decode("utf-8-sig").splitlines()))[0]["Pincode"],"482001")

    def test_sale_and_bid_start_refresh_runs_once_per_start_and_respects_expiry(self):
        from datetime import timezone
        row = {"Published Date":"29-Sep-2026 09:00 AM", "Document Download Start Date":"30-Sep-2026 10:00 AM",
               "Bid Submission Start Date":"01-Oct-2026 10:00 AM", "Closing Date":"10-Oct-2026 05:00 PM"}
        before = datetime(2026,9,30,4,29,tzinfo=timezone.utc)
        sale = datetime(2026,9,30,4,30,tzinfo=timezone.utc)
        bid = datetime(2026,10,1,4,30,tzinfo=timezone.utc)
        self.assertIsNone(worker.start_date_refresh_due(row,before))
        self.assertEqual(worker.start_date_refresh_due(row,sale),sale)
        row["Start Date Refreshed Through"] = sale.isoformat()
        self.assertIsNone(worker.start_date_refresh_due(row,sale))
        self.assertEqual(worker.start_date_refresh_due(row,bid),bid)
        row["Start Date Refreshed Through"] = bid.isoformat()
        self.assertIsNone(worker.start_date_refresh_due(row,bid))
        self.assertIsNone(worker.start_date_refresh_due({**row,"Start Date Refreshed Through":"", "Published Date":row["Document Download Start Date"]},bid))
        self.assertIsNone(worker.start_date_refresh_due({**row,"Start Date Refreshed Through":"", "Closing Date":"30-Sep-2026 05:00 PM"},bid))

    def test_complete_tender_is_selected_again_when_start_date_is_due(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = {**completed("due"), "Published Date":"01-Jan-2020 09:00 AM",
                   "Document Download Start Date":"02-Jan-2020 09:00 AM", "Bid Submission Start Date":"03-Jan-2020 09:00 AM",
                   "Closing Date":"01-Jan-2099 03:00 PM"}
            write_csv(root/"master.csv",[row])
            write_csv(root/"listing.csv",[{"Tender ID":"due"}])
            captured = []
            def fast(targets,*args):
                captured.extend(targets)
                return [],[]
            with patch.dict(os.environ,{"PRIMARY_ID_SEARCH":"0","PRIORITY_TENDER_IDS":"","EXTRACT_ALL_INVENTORY":"1"}), \
                 patch.object(worker,"CSV",root/"master.csv"), patch.object(worker,"SNAPSHOT",root/"listing.csv"), \
                 patch.object(worker,"DETAIL_CSV",root/"details.csv"), patch.object(worker,"STATUS",root/"data/status.json"), \
                 patch.object(worker,"rsp_style_extract_targets",side_effect=fast):
                worker.main()
            self.assertEqual([r["Tender ID"] for r in captured],["due"])
            merged = worker.merge_extracted_detail(row,{"EMD Fee":"", "Pincode":"462001"})
            self.assertEqual(merged["EMD Fee"],row["EMD Fee"])
            self.assertEqual(merged["Pincode"],"462001")
            self.assertIsNone(worker.start_date_refresh_due(merged))
            self.assertTrue(merged["Start Date Refreshed At"])

    def test_batch_status_keeps_last_success_and_does_not_count_partial_details(self):
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_csv(root/"master.csv", [{"Tender ID": "a", "Closing Date": "01-Jan-2099 03:00 PM"}])
                write_csv(root/"listing.csv", [{"Tender ID": "a"}])
                page = SimpleNamespace()
                context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
                browser = SimpleNamespace(new_context=lambda **kw: context, close=lambda: None)
                pw = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser))
                result = completed("a") if valid else {"Tender ID":"a", "Detail Extracted":"YES", "Processing Fee":"295"}
                with patch.dict(os.environ, {"PRIMARY_ID_SEARCH":"1", "PRIORITY_TENDER_IDS":"", "EXTRACT_ALL_INVENTORY":"1"}), \
                     patch.object(worker,"CSV",root/"master.csv"), \
                     patch.object(worker,"SNAPSHOT",root/"listing.csv"), \
                     patch.object(worker,"DETAIL_CSV",root/"details.csv"), \
                     patch.object(worker,"STATUS",root/"data/status.json"), \
                     patch.object(worker,"sync_playwright",lambda: nullcontext(pw)), \
                     patch.object(worker,"do_search",return_value=result):
                    worker.main()
                status = json.loads((root/"data/status.json").read_text())
                self.assertEqual(status["batch_attempted"],1)
                self.assertEqual(status["batch_complete"],int(valid))
                self.assertEqual(status["last_successful_tender_id"],"a" if valid else "")
                self.assertEqual(status["success"],int(valid))
                self.assertEqual(status["failed_ids"],[] if valid else ["a"])

    def test_imported_listing_cannot_claim_complete_details(self):
        row = import_rsp_live.map_row({"tender_id": "2026_MIDCL_529130_2", "nit_ref": "24 Bhopal of 2026-27"}, "now")
        self.assertEqual(row["Detail Extracted"], "")
        self.assertEqual(row["Total Fee"], "")
        fees = import_rsp_live.map_row({"tender_id": "example", "emd": "1000", "tender_fee": "2000", "processing_fee": "295"}, "now")
        self.assertEqual(fees["Total Fee"], "3295")

    def test_cleanup_does_not_remove_extended_live_id_for_stale_detail_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'data').mkdir()
            write_csv(root/'organisation_tenders.csv',[{'Tender ID':'extended','Closing Date':'05-Oct-2026 03:00 PM'}])
            write_csv(root/'tender_details.csv',[{'Tender ID':'extended','Closing Date':'28-Sep-2026 03:00 PM'}])
            (root/'data/live_snapshot.json').write_text(json.dumps({'tender_ids':['extended']}))
            cleanup.cleanup(root,datetime(2026,9,30,20,25,tzinfo=cleanup.IST))
            self.assertEqual(json.loads((root/'data/live_snapshot.json').read_text())['tender_ids'],['extended'])

    def test_import_uses_portal_deadline_even_when_summary_keeps_later_time(self):
        record = {"tender_id": "2026_MPPGC_519250_1", "bid_end": "30-Sep-2026 10:19 PM",
                  "portal_fields": {"Bid Submission End Date": "30-Sep-2026 03:00 PM"}}
        row = import_rsp_live.map_row(record, "now")
        self.assertEqual(row["Closing Date"], "30-Sep-2026 03:00 PM")
        self.assertEqual(row["Bid Submission End Date"], row["Closing Date"])
        record["portal_fields"] = {}
        self.assertEqual(import_rsp_live.imported_bid_end(record), "30-Sep-2026 10:19 PM")
        record["bid_end"] = "not a date"
        self.assertEqual(import_rsp_live.imported_bid_end(record), "")

    def test_reported_live_tender_missing_from_partial_snapshot_gets_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tid = "2026_MIDCL_529130_2"
            write_csv(root/"master.csv", [{"Tender ID": tid, "Closing Date": "01-Jan-2099 03:00 PM", "Detail Extracted": "YES"}])
            write_csv(root/"listing.csv", [{"Tender ID": "other", "Closing Date": "01-Jan-2099 03:00 PM"}])
            captured=[]
            def fast(targets,*args):
                captured.extend(targets)
                return [],[]
            with patch.dict(os.environ, {"EXTRACT_ALL_INVENTORY": "1", "PRIORITY_TENDER_IDS": tid, "PRIMARY_ID_SEARCH": "0"}), \
                 patch.object(worker,"CSV",root/"master.csv"), \
                 patch.object(worker,"SNAPSHOT",root/"listing.csv"), \
                 patch.object(worker,"DETAIL_CSV",root/"details.csv"), \
                 patch.object(worker,"STATUS",root/"data/status.json"), \
                 patch.object(worker,"rsp_style_extract_targets",side_effect=fast):
                worker.main()
            self.assertEqual([r["Tender ID"] for r in captured],[tid])

    def test_tender_id_is_never_a_reference_number(self):
        tid = "2026_MPTAX_534364_2"
        self.assertEqual(scraper.valid_reference_number(tid), "")
        self.assertEqual(scraper.valid_reference_number(f"[{tid}]"), "")
        self.assertEqual(scraper.valid_reference_number("CTD/DC-2/STORE/2026/393"), "CTD/DC-2/STORE/2026/393")
        soup = BeautifulSoup(f'<table><tr><td><a href="/nicgep/app?page=FrontEndViewTender">[Record destruction] [{tid}] [{tid}]</a></td><td>01-Oct-2026</td></tr></table>', 'html.parser')
        row = scraper.parse_tender_rows(soup, scraper.PORTAL)[0]
        self.assertEqual(row["tender_id"], tid)
        self.assertEqual(row["reference"], "")
        detail = scraper.parse_detail(BeautifulSoup(f'<table><tr><td>Tender ID</td><td>{tid}</td></tr><tr><td>Tender Reference Number</td><td>{tid}</td></tr></table>', 'html.parser'), scraper.PORTAL)
        self.assertEqual(detail["Reference Number"], "")

    def test_portal_gst_label_is_processing_amount_not_gst_percentage(self):
        soup = BeautifulSoup('''<table><tr><td>Tender Fee in ₹</td><td>2,000.50</td></tr>
          <tr><td>Processing Fee in ₹ (18.00% GST Incl.)</td><td>295.25</td></tr>
          <tr><td>EMD Amount in ₹</td><td>3,290</td></tr>
          <tr><td>Bid Validity(Days)</td><td>180</td></tr></table>''', 'html.parser')
        row = scraper.parse_detail(soup, scraper.PORTAL)
        self.assertEqual(row['Tender Fee'], '2000.50')
        self.assertEqual(row['Processing Fee'], '295.25')
        self.assertEqual(row['Total Fee'], '5585.75')
        self.assertEqual(row['Bid Validity'], '180')

    def test_home_copy_caps_each_table_and_preserves_reference_punctuation(self):
        html = ''.join('<table id="'+table+'">'+''.join(
            f'<tr><td><a href="session-only">{i}. Work</a></td><td>A/{i}</td><td>close</td><td>open</td></tr>'
            for i in range(12))+'</table>' for table in ('activeTenders','activeCorrigendums'))
        entries = home_latest.home_entries(BeautifulSoup(html, 'html.parser'))
        self.assertEqual(len(entries), 20)
        self.assertNotIn('href', entries[0])
        self.assertNotEqual(home_latest.reference_key('A/12'), home_latest.reference_key('A-12'))
        self.assertIsNone(home_latest.matching_row(entries[0], [
            {'Reference Number':'A/0','Tender ID':'a'}, {'Reference Number':'A/0','Tender ID':'b'}]))

    def test_full_copy_paths_and_current_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tid = "2026_UAD_123456_1"
            previous = "2026_UAD_111111_1"
            write_csv(root / "all_tenders_org_detailed.csv", [completed(tid), completed(previous)])
            page = SimpleNamespace(url=scraper.PORTAL)
            browser = SimpleNamespace(new_page=lambda **kwargs: page, close=lambda: None)
            pw = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: browser))
            orgs = [{"name": "Example Org", "count": 1, "url": "session-only"}]
            listing = [{"tender_id": tid, "title": "A real tender", "reference": "NIT-1"}]
            with patch.dict(os.environ, {"COPY_ONLY": "1", "RUNNER_TEMP": temporary}), \
                 patch.object(scraper, "sync_playwright", lambda: nullcontext(pw)), \
                 patch.object(scraper, "open_organisation_page_from_home", return_value=None), \
                 patch.object(scraper, "parse_organisation_rows", return_value=orgs), \
                 patch.object(scraper, "browser_get_all_tender_rows", return_value=(listing, 1)), \
                 patch.object(scraper, "parse_list_dates", return_value=("", "", "")):
                result = scraper.scrape_mp_tenders(root / "all_tenders_org_detailed.csv")
            self.assertTrue(result["copy_only"])
            self.assertTrue((root / "mp-tenders-working/organisations.csv").exists())
            status = json.loads((root / "data/status.json").read_text())
            self.assertEqual((status["detail_complete"], status["detail_remaining"]), (1, 0))
            self.assertTrue(status["snapshot_published"])

    def test_summary_deduplicates_and_excludes_history_and_recovered_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_csv(root / "organisations.csv", [{"Tender Count": "3", "Retrieved At": "today"}])
            write_csv(root / "organisation_tenders.csv", [{"Tender ID": tid} for tid in ("a", "a", "b", "c")])
            write_csv(root / "all_tenders_org_detailed.csv", [completed("a"), completed("old")])
            (root / "data").mkdir()
            (root / "data/existing_id_detail_status.json").write_text(json.dumps({"failed_ids": ["a", "b", "old"], "skipped_ids": ["a"]}))
            summary = build_summary(root)
            self.assertEqual([summary[key] for key in ("copied_tenders", "detail_complete", "detail_failed", "detail_skipped", "detail_pending")], [3, 1, 1, 0, 1])

    def test_fast_route_resolves_link_and_saves_before_status(self):
        tid = "2026_UAD_123456_1"
        html = f'<table><tr><td>{tid}</td><td><a href="/nicgep/app?page=FrontEndViewTender">Work</a></td></tr></table>'
        response = SimpleNamespace(text=html)
        calls = []
        inventory = {tid: {"Tender ID": tid}}
        ids = set()
        with patch.dict(os.environ, {"RSP_FAST": "1"}), \
             patch.object(worker, "portal_request", return_value=response), \
             patch.object(worker, "parse_organisation_rows", return_value=[{"name": "Org", "url": "https://example.test/org"}]), \
             patch.object(worker, "parse_detail", return_value=completed(tid)):
            fallback, failed = worker.rsp_style_extract_targets([inventory[tid]], inventory,
                lambda *args: calls.append("status"), lambda: calls.append("csv"),
                lambda: calls.append("details"), ids)
        self.assertFalse(fallback or failed)
        self.assertEqual(ids, {tid})
        self.assertEqual(calls[:3], ["csv", "details", "status"])

    def test_discovery_failure_reaches_id_search_fallback(self):
        tid = "2026_UAD_123456_1"
        base = {"Tender ID": tid}
        with patch.dict(os.environ, {"RSP_FAST": "0"}), \
             patch.object(worker, "portal_request", side_effect=RuntimeError("portal unavailable")):
            fallback, _ = worker.rsp_style_extract_targets([base], {tid: base}, lambda *a: None, lambda: None, lambda: None, set())
        self.assertEqual(fallback, [base])

    def test_recovered_id_missing_in_master_reaches_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tid = "2026_UAD_123456_1"
            write_csv(root / "master.csv", [{"Tender ID": "old", "Title": "Old"}])
            write_csv(root / "listing.csv", [{"Tender ID": tid, "Title": "Recovered", "Organisation Name": "Org"}])
            captured = []
            def fast(targets, *args):
                captured.extend(targets)
                return [], []
            with patch.object(worker, "CSV", root / "master.csv"), \
                 patch.object(worker, "STATUS", root / "data/status.json"), \
                 patch.object(worker, "SNAPSHOT", root / "listing.csv"), \
                 patch.object(worker, "DETAIL_CSV", root / "details.csv"), \
                 patch.object(worker, "rsp_style_extract_targets", side_effect=fast):
                worker.main()
            self.assertEqual(captured[0]["Tender ID"], tid)
            self.assertEqual(captured[0]["Organisation"], "Org")

class PrivateAlertTests(unittest.TestCase):
    def test_exact_handle_is_verified_as_private_before_sending(self):
        calls = []
        def api(method, **params):
            calls.append((method, params))
            if method == "getChatAdministrators":
                return [{"user": {"id": 123, "username": "rdgyan"}}, {"user": {"id": 456, "username": "other"}}]
            if method == "getChat":
                return {"id": 123, "type": "private", "username": "rdgyan"}
            return {"message_id": 1}
        result = send_alert({"status": "mismatch"}, api)
        self.assertEqual(result["destination"], "private_admin")
        self.assertEqual(calls[-1][1]["chat_id"], 123)

    def test_channel_or_unresolved_person_never_receives_alert(self):
        for value in ("-100123", "@mptendersalert"):
            with self.assertRaises(ValueError):
                resolve_private_admin(lambda *a, **k: self.fail("API must not be called"), value)
        with self.assertRaises(ValueError):
            resolve_private_admin(lambda *a, **k: [])
        with self.assertRaises(ValueError):
            resolve_private_admin(lambda *a, **k: {"id": 123, "type": "channel"}, "123")

    def test_verified_snapshot_does_not_send(self):
        self.assertEqual(send_alert({"status": "verified"}, lambda *a, **k: self.fail("no calls expected")), {"status": "not_needed"})

class PublisherTests(unittest.TestCase):
    def test_publisher_does_not_restore_expired_record_after_cleanup(self):
        import subprocess
        def run(directory, *args):
            return subprocess.run(["git", *args], cwd=directory, check=True, capture_output=True).stdout.decode().strip()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote, local = root / "remote.git", root / "local"
            run(root, "init", "--bare", "--initial-branch=main", str(remote))
            run(root, "clone", str(remote), str(local))
            run(local, "config", "user.name", "test")
            run(local, "config", "user.email", "test@example.invalid")
            future = {**completed("extended"), "Closing Date": "01-Oct-2026 06:00 PM"}
            write_csv(local / "all_tenders_org_detailed.csv", [future])
            (local / "data").mkdir()
            (local / "data/cleanup_status.json").write_text(json.dumps({
                "policy": "expired-at-evening", "cleaned_at": "2026-09-30T19:00:00+05:30"}))
            run(local, "add", ".")
            run(local, "commit", "-m", "7 PM cleanup")
            run(local, "push", "origin", "main")
            expired = {**completed("expired"), "Closing Date": "30-Sep-2026 06:00 PM"}
            write_csv(local / "all_tenders_org_detailed.csv", [future, expired])
            before = Path.cwd()
            try:
                os.chdir(local)
                with patch.dict(os.environ, {"RUNNER_TEMP": temporary, "GITHUB_REF_NAME": "main"}):
                    publisher.publish(["all_tenders_org_detailed.csv"])
                run(local, "fetch", "origin", "main")
                data = run(local, "show", "origin/main:all_tenders_org_detailed.csv")
                self.assertIn("extended", data)
                self.assertNotIn("expired", data)
            finally:
                os.chdir(before)

    def test_concurrent_remote_record_and_code_change_survive_publication(self):
        import subprocess
        def run(directory, *args):
            return subprocess.run(["git", *args], cwd=directory, check=True, capture_output=True).stdout.decode().strip()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote, local, other = root / "remote.git", root / "local", root / "other"
            run(root, "init", "--bare", "--initial-branch=main", str(remote))
            run(root, "clone", str(remote), str(local))
            for directory in (local,):
                run(directory, "config", "user.name", "test")
                run(directory, "config", "user.email", "test@example.invalid")
            write_csv(local / "all_tenders_org_detailed.csv", [completed("initial")])
            (local / "code.txt").write_text("original")
            run(local, "add", ".")
            run(local, "commit", "-m", "initial")
            run(local, "push", "origin", "main")
            original_head = run(local, "rev-parse", "HEAD")
            run(root, "clone", str(remote), str(other))
            run(other, "config", "user.name", "test")
            run(other, "config", "user.email", "test@example.invalid")
            write_csv(other / "all_tenders_org_detailed.csv", [completed("initial"), completed("remote-new")])
            (other / "code.txt").write_text("new working code")
            run(other, "add", ".")
            run(other, "commit", "-m", "concurrent update")
            run(other, "push", "origin", "main")
            write_csv(local / "all_tenders_org_detailed.csv", [completed("initial"), completed("local-new")])
            before = Path.cwd()
            try:
                os.chdir(local)
                with patch.dict(os.environ, {"RUNNER_TEMP": temporary, "GITHUB_REF_NAME": "main"}):
                    publisher.publish(["all_tenders_org_detailed.csv"])
                self.assertEqual(run(local, "rev-parse", "HEAD"), original_head)
                run(local, "fetch", "origin", "main")
                self.assertEqual(run(local, "show", "origin/main:code.txt"), "new working code")
                data = run(local, "show", "origin/main:all_tenders_org_detailed.csv")
                self.assertIn("remote-new", data)
                self.assertIn("local-new", data)
            finally:
                os.chdir(before)

if __name__ == "__main__":
    unittest.main()
