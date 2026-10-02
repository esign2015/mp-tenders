import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from telegram_scheduler import IST, due_alert
from scheduled_telegram_delivery import deliver
import telegram_alerts as alerts

CFG = {'morning_telegram_ist':'10:20', 'evening_new_telegram_ist':'20:45', 'telegram_max_delay_minutes':45}

class MorningTelegramTests(unittest.TestCase):
    def test_first_morning_extraction_success_and_failure_send_before_clock_alert(self):
        now = datetime(2026,10,1,9,10,tzinfo=IST)
        self.assertIsNone(due_alert(CFG, {}, now))
        for result in ('success','failure','cancelled'):
            self.assertEqual(due_alert(CFG, {}, now, result)[1:], ('morning','2026-10-01:morning'))

    def test_completion_delay_and_fixed_eight_am_morning(self):
        cfg={'morning_telegram_ist':'08:00','morning_send_after_extraction':False,'evening_new_after_detail_minutes':15,'evening_total_after_detail_minutes':30}
        sent={'morning':'2026-10-01:morning'}
        complete={'completed_at':'2026-10-01T14:00:00+00:00','run_id':'123'}
        self.assertIsNone(due_alert(cfg,{},datetime(2026,10,1,7,59,tzinfo=IST),'success'))
        self.assertEqual(due_alert(cfg,{},datetime(2026,10,1,8,0,tzinfo=IST))[1],'morning')
        self.assertIsNone(due_alert(cfg,sent,datetime(2026,10,1,20,0,tzinfo=IST)))
        self.assertIsNone(due_alert(cfg,sent,datetime(2026,10,1,19,44,tzinfo=IST),completion=complete))
        self.assertEqual(due_alert(cfg,sent,datetime(2026,10,1,19,45,tzinfo=IST),completion=complete)[1],'evening_new')
        sent['evening_new']='2026-10-01:evening_new'
        self.assertIsNone(due_alert(cfg,sent,datetime(2026,10,1,19,59,tzinfo=IST),completion=complete))
        self.assertEqual(due_alert(cfg,sent,datetime(2026,10,1,20,0,tzinfo=IST),completion=complete)[1],'evening_total')
        sent['morning']='2026-10-02:morning'
        self.assertIsNone(due_alert(cfg,sent,datetime(2026,10,2,20,0,tzinfo=IST),completion=complete))

    def test_delayed_run_still_sends_and_sent_date_is_not_repeated(self):
        now = datetime(2026,10,1,11,19,tzinfo=IST)
        self.assertEqual(due_alert(CFG, {'morning':'2026-09-29:morning'}, now)[1], 'morning')
        self.assertIsNone(due_alert(CFG, {'morning':'2026-10-01:morning'}, now))
        next_day = datetime(2026,10,2,10,30,tzinfo=IST)
        self.assertEqual(due_alert(CFG, {'morning':'2026-10-01:morning'}, next_day)[2], '2026-10-02:morning')

    def test_late_evening_still_sends_without_duplicate_morning(self):
        now = datetime(2026,10,1,23,10,tzinfo=IST)
        self.assertEqual(due_alert(CFG, {'morning':'2026-10-01:morning'}, now)[1], 'evening_new')

    def test_pdf_failure_is_retried_without_resending_confirmed_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {'text':0,'document':0}
            def text(*args):
                calls['text'] += 1
                return {'ok':True,'result':{'message_id':101}}
            def document(*args):
                calls['document'] += 1
                if calls['document'] == 1: raise RuntimeError('PDF delivery failed')
                return {'ok':True,'result':{'message_id':102}}
            module = SimpleNamespace(telegram_message=text, telegram_document=document)
            def main():
                module.telegram_message('token','channel','Closing today')
                module.telegram_document('token','channel','pdf','caption')
                return 0
            module.main = main
            with self.assertRaises(RuntimeError): deliver(root,'morning','2026-10-01:morning','failure',module)
            self.assertFalse((root/'data/telegram_schedule.json').exists())
            attempt=json.loads((root/'data/telegram_last_attempt.json').read_text())
            self.assertEqual(attempt['send_outcome'],'failure')
            deliver(root,'morning','2026-10-01:morning','failure',module)
            self.assertEqual(calls,{'text':1,'document':2})
            self.assertEqual(json.loads((root/'data/telegram_schedule.json').read_text())['morning'],'2026-10-01:morning')
            self.assertEqual(json.loads((root/'data/telegram_last_attempt.json').read_text())['message_ids'],[101,102])

    def test_card_retry_keeps_already_confirmed_table_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls={'text':0,'table':0,'card':0}
            def text(*args):
                calls['text']+=1
                return {'ok':True,'result':{'message_id':101}}
            def document(kind):
                calls[kind]+=1
                if kind=='card' and calls[kind]==1:raise RuntimeError('card failed')
                return {'ok':True,'result':{'message_id':102 if kind=='table' else 103}}
            module=SimpleNamespace(telegram_message=text,telegram_document=document)
            def main():
                module.telegram_message('message')
                module.telegram_document('table')
                module.telegram_document('card')
                return 0
            module.main=main
            with self.assertRaises(RuntimeError):deliver(Path(tmp),'evening_new','2026-10-01:evening_new','',module)
            deliver(Path(tmp),'evening_new','2026-10-01:evening_new','',module)
            self.assertEqual(calls,{'text':1,'table':1,'card':2})

    def test_unconfirmed_response_does_not_mark_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            module=SimpleNamespace(telegram_message=lambda *a:{'ok':True},telegram_document=lambda *a:{'ok':True})
            module.main=lambda:module.telegram_message('t','c','message')
            with self.assertRaises(RuntimeError):deliver(Path(tmp),'morning','2026-10-01:morning','',module)
            self.assertFalse((Path(tmp)/'data/telegram_schedule.json').exists())

    def test_failed_extraction_sends_today_list_from_available_data(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,1,9,30,tzinfo=IST)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'all_tenders_org_detailed.csv';path.write_text('Tender ID\n')
            (Path(tmp)/'organisation_tenders.csv').write_text('Tender ID\ntoday\ntomorrow\n')
            rows=[{'Tender ID':'today','Closing Date':'01-Oct-2026 05:00 PM'},{'Tender ID':'tomorrow','Closing Date':'02-Oct-2026 05:00 PM'},{'Tender ID':'old-master-only','Closing Date':'01-Oct-2026 05:00 PM'}]
            with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':'morning','MORNING_EXTRACTION_RESULT':'failure'}),patch.object(alerts,'CSV_PATH',path),patch.object(alerts,'datetime',Clock),patch.object(alerts,'load_report_rows',return_value=rows),patch.object(alerts,'telegram_message') as message,patch.object(alerts,'telegram_document') as document,patch.object(alerts,'make_pdf',return_value=Path('pdf')) as pdf,patch('telegram_card_pdf.make_card_pdf',return_value=Path('cardpdf')) as card_pdf:
                self.assertEqual(alerts.main(),0)
                self.assertIn('उपलब्ध पिछले data',message.call_args.args[2])
                self.assertEqual([r['Tender ID'] for r in pdf.call_args.args[0]],['today'])
                self.assertEqual(document.call_count,2)
                self.assertEqual([r['Tender ID'] for r in card_pdf.call_args.args[0]],['today'])

    def test_morning_alert_is_independent_of_extraction(self):
        root=Path(__file__).resolve().parents[1]
        workflow=(root/'.github/workflows/scrape.yml').read_text()
        scrape=workflow.split('  scrape_full:',1)[1].split('  morning_telegram_after_extraction:',1)[0]
        self.assertIn("github.event.schedule == '25 3 * * *'",scrape)
        after=workflow.split('  morning_telegram_after_extraction:',1)[1].split('  corrigendum_watch:',1)[0]
        self.assertIn('if: false',after)
        config=json.loads((root/'data/schedule_config.json').read_text())
        self.assertEqual(config['morning_telegram_ist'],'08:00')
        self.assertFalse(config['morning_send_after_extraction'])
        independent=(root/'.github/workflows/telegram_scheduled_v2.yml').read_text()
        self.assertIn('cron: "30 2 * * *"',independent)
        self.assertIn('data/telegram_daily.trigger',independent)
        self.assertNotIn('needs: scrape_full',independent)

if __name__ == '__main__': unittest.main()
