import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import telegram_alerts as alerts
from scheduled_telegram_delivery import deliver, evening_completion
from telegram_scheduler import IST, due_alert

CFG = {'morning_telegram_ist': '08:00', 'afternoon_telegram_ist': '16:00',
       'morning_send_after_extraction': False, 'evening_new_after_detail_minutes': 10,
       'evening_fallback_ist': '20:00', 'evening_total_after_detail_minutes': 20}


class ThreeDailyReportsTests(unittest.TestCase):
    def test_only_the_evening_run_changes_the_completion_marker(self):
        import record_evening_start as start
        import record_evening_completion as finish
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return cls(2026, 10, 4, 21, 0, tzinfo=IST)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(start, 'ROOT', root), patch.object(start, 'datetime', Clock), \
                    patch.dict(os.environ, {'GITHUB_RUN_ID': '42', 'SCRAPE_SCHEDULE': '35 13 * * *', 'SCRAPE_EVENT': 'schedule'}):
                start.main()
            path = root/'data/evening_detail_completion.json'
            with patch.object(finish, 'ROOT', root), patch.object(finish, 'datetime', Clock), \
                    patch.dict(os.environ, {'GITHUB_RUN_ID': '41', 'SCRAPE_SCHEDULE': '30 11 * * *'}):
                finish.main()
            self.assertFalse(path.exists())
            with patch.object(finish, 'ROOT', root), patch.object(finish, 'datetime', Clock), \
                    patch.dict(os.environ, {'GITHUB_RUN_ID': '42', 'SCRAPE_SCHEDULE': '35 13 * * *', 'COPY_JOB_RESULT': 'failure', 'DETAIL_JOB_RESULT': 'skipped'}):
                finish.main()
            self.assertEqual(json.loads(path.read_text())['result'], 'failure')

    def test_exactly_three_daily_slots_success_failure_and_catchup(self):
        clock = lambda h, m: datetime(2026, 10, 4, h, m, tzinfo=IST)
        sent = {}
        self.assertIsNone(due_alert(CFG, sent, clock(7, 59)))
        self.assertEqual(due_alert(CFG, sent, clock(8, 0))[1], 'morning')
        sent['morning'] = '2026-10-04:morning'
        self.assertIsNone(due_alert(CFG, sent, clock(15, 59)))
        self.assertEqual(due_alert(CFG, sent, clock(16, 0))[1], 'afternoon')
        sent['afternoon'] = '2026-10-04:afternoon'
        for result in ('success', 'failure', 'cancelled'):
            complete = {'run_id': '42', 'completed_at': '2026-10-04T19:30:00+05:30', 'result': result}
            self.assertIsNone(due_alert(CFG, sent, clock(19, 39), completion=complete))
            self.assertEqual(due_alert(CFG, sent, clock(19, 40), completion=complete)[1], 'evening_new')
        self.assertEqual(due_alert(CFG, sent, clock(20, 0))[1], 'evening_new')
        # A late real run is sent after its completion, not at the fallback clock.
        late = {'run_id': '42', 'completed_at': '2026-10-04T21:30:00+05:30'}
        self.assertIsNone(due_alert(CFG, sent, clock(20, 0), completion=late))
        self.assertIsNone(due_alert(CFG, sent, clock(21, 39), completion=late))
        self.assertEqual(due_alert(CFG, sent, clock(21, 40), completion=late)[1], 'evening_new')
        sent['evening_new'] = '2026-10-04:evening_new'
        self.assertIsNone(due_alert(CFG, sent, clock(23, 55)))
        self.assertIsNone(due_alert({**CFG, 'telegram_schedule_start_date': '2026-10-05'}, {}, clock(23, 55)))

    def test_running_marker_waits_and_lost_marker_eventually_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'data').mkdir()
            (root/'data/evening_run_start.json').write_text(json.dumps({
                'run_id': '42', 'started_at': '2026-10-04T19:05:00+05:30'}))
            now = datetime(2026, 10, 4, 20, 0, tzinfo=IST)
            current = evening_completion(root, now)
            self.assertEqual(current['result'], 'in_progress')
            sent = {'morning': '2026-10-04:morning', 'afternoon': '2026-10-04:afternoon'}
            self.assertIsNone(due_alert(CFG, sent, now, completion=current))
            later = now.replace(hour=23, minute=30)
            fallback = evening_completion(root, later)
            self.assertEqual(fallback['result'], 'failure')
            self.assertEqual(due_alert(CFG, sent, later, completion=fallback)[1], 'evening_new')
            (root/'data/evening_detail_completion.json').write_text(json.dumps({
                'run_id': '42', 'completed_at': '2026-10-04T20:10:00+05:30', 'result': 'failure'}))
            self.assertEqual(evening_completion(root, now)['result'], 'failure')

    def test_three_filters_and_single_pdf_messages_on_year_boundary(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return cls(2026, 12, 31, 16, 0, tzinfo=IST)
        rows = [
            {'Tender ID': 'today', 'Closing Date': '31-Dec-2026 06:00 PM', 'Published Date': '30-Dec-2026 10:00 AM'},
            {'Tender ID': 'tomorrow', 'Closing Date': '01-Jan-2027 06:00 PM', 'Published Date': '30-Dec-2026 10:00 AM'},
            {'Tender ID': 'new', 'Closing Date': '05-Jan-2027 06:00 PM', 'Published Date': '31-Dec-2026 10:00 AM'},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root/'all_tenders_org_detailed.csv'; path.write_text('Tender ID\n')
            (root/'organisation_tenders.csv').write_text('Tender ID\ntoday\ntomorrow\nnew\n')
            for mode, expected in (('morning', 'today'), ('afternoon', 'tomorrow'), ('evening_new', 'new')):
                with self.subTest(mode=mode), patch.dict(os.environ, {
                    'TELEGRAM_BOT_TOKEN':'test', 'TELEGRAM_CHAT_ID':'test', 'NOTIFY_MODE': mode,
                    'MORNING_EXTRACTION_RESULT': 'failure'}), patch.object(alerts, 'CSV_PATH', path), \
                    patch.object(alerts, 'datetime', Clock), patch.object(alerts, 'load_report_rows', return_value=rows), \
                    patch.object(alerts, 'make_pdf', return_value=Path('table.pdf')) as pdf, \
                    patch.object(alerts, 'telegram_message') as text, patch.object(alerts, 'telegram_document') as document, \
                    patch('telegram_card_pdf.make_card_pdf') as card:
                    self.assertEqual(alerts.main(), 0)
                    self.assertEqual([r['Tender ID'] for r in pdf.call_args.args[0]], [expected])
                    self.assertEqual(document.call_count, 1); text.assert_not_called(); card.assert_not_called()
                    caption = document.call_args.args[3]
                    self.assertLessEqual(len(caption.encode('utf-16-le')) // 2, 1024)
                    self.assertIn('उपलब्ध पिछले डेटा', caption)
                    self.assertIn(alerts.HINDI_DISCLAIMER, caption)
                    self.assertNotIn('manual', caption.lower())
                    if mode == 'afternoon': self.assertIn('01/01/2027', caption)

    def test_retired_all_and_card_entrypoints_send_nothing(self):
        import send_closing_card
        with patch.object(alerts, 'telegram_document') as document, patch.object(alerts, 'telegram_message') as message:
            for mode in ('evening_total', 'evening'):
                with patch.dict(os.environ, {'NOTIFY_MODE': mode}): self.assertEqual(alerts.main(), 0)
            self.assertEqual(send_closing_card.main(), 0)
            document.assert_not_called(); message.assert_not_called()

    def test_pdf_receipt_retry_does_not_duplicate_the_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            class Module:
                def telegram_message(self, *args): raise AssertionError('No separate text message')
                def telegram_document(self, *args):
                    calls.append(args)
                    if len(calls) == 1: raise RuntimeError('Telegram unavailable')
                    return {'ok': True, 'result': {'message_id': 123}}
                def main(self): self.telegram_document('token', 'channel', 'table.pdf', 'Hindi caption')
            module = Module()
            with self.assertRaises(RuntimeError): deliver(temporary, 'afternoon', '2026-10-04:afternoon', '', module)
            deliver(temporary, 'afternoon', '2026-10-04:afternoon', '', module)
            deliver(temporary, 'afternoon', '2026-10-04:afternoon', '', module)
            self.assertEqual(len(calls), 2)
            state = json.loads((Path(temporary)/'data/telegram_schedule.json').read_text())
            self.assertEqual(state['afternoon'], '2026-10-04:afternoon')


if __name__ == '__main__': unittest.main()
