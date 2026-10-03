import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from bs4 import BeautifulSoup
import existing_id_detail as worker
import publish_data_checkpoint as publisher
from corrigendum_state import merge_state, recheck_due
from scraper import parse_latest_corrigendum


class CorrigendumStateTests(unittest.TestCase):
    old = {'Tender ID': '2026_HED_535900_1', 'Corrigendum': 'Corrigendum',
           'Corrigendum Type': 'Date Extension', 'Corrigendum Detected At': 'Corrigendum',
           'Corrigendum Last Checked': '2026-09-22T13:33:52+00:00', 'EMD Fee': '15000'}
    fresh = {'Tender ID': '2026_HED_535900_1', 'Corrigendum': '', 'Corrigendum Type': '',
             'Corrigendum Last Checked': '2026-10-03T10:00:00+00:00'}

    def test_official_absence_clears_old_flag_and_preserves_fees(self):
        merged = worker.merge_extracted_detail(self.old, self.fresh)
        self.assertEqual(merged['Corrigendum'], 'No')
        self.assertEqual(merged['Corrigendum Type'], '')
        self.assertEqual(merged['Corrigendum Detected At'], '')
        self.assertEqual(merged['EMD Fee'], '15000')
        self.assertFalse(recheck_due(merged))

    def test_stale_writer_cannot_restore_a_removed_flag(self):
        remote = merge_state(dict(self.fresh), self.fresh)
        fields = list(dict.fromkeys([*self.old, *remote]))
        def data(row):
            stream = io.StringIO(); writer = csv.DictWriter(stream, fields)
            writer.writeheader(); writer.writerow(row); return stream.getvalue().encode()
        for first, second in [(remote, self.old), (self.old, remote)]:
            rows = list(csv.DictReader(io.StringIO(publisher.merge_details(data(first), data(second)).decode('utf-8-sig'))))
            self.assertEqual(rows[0]['Corrigendum'], 'No')
            self.assertEqual(rows[0]['Corrigendum Type'], '')
        positive = {**self.fresh, 'Corrigendum': 'Revised BOQ', 'Corrigendum Type': 'Other',
                    'Corrigendum Last Checked': '2026-10-03T16:00:00+05:30'}
        self.assertEqual(merge_state({}, remote, positive)['Corrigendum'], 'Revised BOQ')

    def test_unverified_blank_does_not_clear_a_real_corrigendum(self):
        self.assertEqual(merge_state({}, self.old, {'Corrigendum': ''})['Corrigendum'], 'Corrigendum')
        self.assertEqual(merge_state({}, self.old, {**self.fresh, 'Corrigendum Last Checked': 'invalid'})['Corrigendum'], 'Corrigendum')

    def test_legacy_positive_is_rechecked_after_a_newer_detail(self):
        row = {**self.old, 'Tested At': '2026-09-30T21:51:49+05:30'}
        self.assertTrue(recheck_due(row))
        self.assertFalse(recheck_due({**row, 'Corrigendum Last Checked': '2026-10-03T10:00:00+00:00'}))
        self.assertFalse(recheck_due({'Corrigendum': ''}))

    def test_same_positive_keeps_detection_time_and_does_not_immediately_requeue(self):
        previous = {**self.old, 'Corrigendum Detected At': '2026-09-22T13:33:52+00:00'}
        fresh = {**self.fresh, 'Corrigendum': previous['Corrigendum'],
                 'Corrigendum Type': previous['Corrigendum Type']}
        merged = merge_state({}, previous, fresh)
        self.assertEqual(merged['Corrigendum Detected At'], previous['Corrigendum Detected At'])
        merged['Tested At'] = '2026-10-03T10:00:00.025+00:00'
        self.assertFalse(recheck_due(merged))

    def test_no_records_row_is_not_a_corrigendum(self):
        prefix = '<table><tr><td>S.No</td><td>Corrigendum Title</td><td>Corrigendum Type</td></tr>'
        absent = BeautifulSoup(prefix+'<tr><td colspan="3">No Corrigendum Found</td></tr></table>', 'html.parser')
        self.assertEqual(parse_latest_corrigendum(absent)['title'], '')
        real = BeautifulSoup(prefix+'<tr><td>1</td><td>Revised BOQ</td><td>Other</td></tr></table>', 'html.parser')
        self.assertEqual(parse_latest_corrigendum(real)['title'], 'Revised BOQ')

    def test_explicit_priority_seeds_missing_ids_without_duplicate_or_completed_flags(self):
        rows = [{'Tender ID': '2026_HED_535900_1', 'Detail Extracted': 'YES'}]
        ids = ['2026_HED_535900_1']
        priority = {'2026_HED_535900_1', '2026_UAD_540057_1'}
        worker.seed_priority_ids(rows, ['Tender ID', 'Detail Extracted'], ids, priority)
        worker.seed_priority_ids(rows, ['Tender ID', 'Detail Extracted'], ids, priority)
        self.assertEqual(len(rows), 2); self.assertEqual(len(ids), 2)
        self.assertEqual(rows[1]['Detail Extracted'], '')
        with self.assertRaises(ValueError):
            worker.seed_priority_ids(rows, ['Tender ID'], ids, {'https://session-url'})


if __name__ == '__main__':
    unittest.main()
