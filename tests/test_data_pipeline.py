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
import home_latest
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

class PipelineTests(unittest.TestCase):
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
