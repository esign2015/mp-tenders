import csv,json,os,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from telegram_scheduler import due_alert,IST
import telegram_alerts as alerts
import finalize_portal_snapshot as finalizer

CFG={'morning_telegram_ist':'08:00','morning_send_after_extraction':False,
     'evening_new_after_detail_minutes':10,'evening_total_after_detail_minutes':20,
     'evening_fallback_ist':'20:00','evening_pdf_gap_minutes':10}
class EveningDelivery(unittest.TestCase):
    def test_success_failure_missing_marker_and_actual_delivery_gap(self):
        sent={'morning':'2026-10-02:morning'}
        done={'completed_at':'2026-10-02T19:30:00+05:30','run_id':'42','result':'failure'}
        clock=lambda h,m:datetime(2026,10,2,h,m,tzinfo=IST)
        self.assertIsNone(due_alert(CFG,sent,clock(19,39),completion=done))
        self.assertEqual(due_alert(CFG,sent,clock(19,40),completion=done)[1],'evening_new')
        self.assertEqual(due_alert(CFG,sent,clock(20,0))[1],'evening_new')
        # A previous-day completion is equivalent to a missing marker.
        yesterday={**done,'completed_at':'2026-10-01T19:30:00+05:30'}
        self.assertEqual(due_alert(CFG,sent,clock(20,0),completion=yesterday)[1],'evening_new')
        sent['evening_new']='2026-10-02:evening_new'
        self.assertIsNone(due_alert(CFG,sent,clock(22,0)))
        self.assertIsNone(due_alert(CFG,sent,clock(20,15),new_sent_at='2026-10-02T20:06:00+05:30'))
        self.assertEqual(due_alert(CFG,sent,clock(20,16),new_sent_at='2026-10-02T20:06:00+05:30')[1],'evening_total')

    def test_zero_rows_still_sends_pdf_and_all_messages_are_hindi(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'all_tenders_org_detailed.csv';path.write_text('Tender ID,Closing Date\n')
            for mode in ('morning','evening_new','evening_total','manual'):
                with self.subTest(mode=mode),patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':mode,'MANUAL_REPORT':'all','MORNING_EXTRACTION_RESULT':'failure'}),patch.object(alerts,'CSV_PATH',path),patch.object(alerts,'load_report_rows',return_value=[]),patch.object(alerts,'make_pdf',return_value=Path('zero.pdf')) as pdf,patch.object(alerts,'telegram_message') as message,patch.object(alerts,'telegram_document') as document:
                    self.assertEqual(alerts.main(),0)
                    self.assertEqual(pdf.call_args.args[0],[])
                    self.assertEqual(document.call_count,1)
                    for text in (message.call_args.args[2],document.call_args.args[3]):
                        self.assertIn('आधिकारिक टेंडर पोर्टल',text)
                        self.assertNotIn('Disclaimer',text)
                    if mode!='manual':self.assertIn('उपलब्ध पिछले data',message.call_args.args[2])

    def test_real_finalizer_publishes_exact_ids_including_zero_count_org(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            def write(name,rows,fields):
                with (root/name).open('w',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
            write('organisations.csv',[{'Organisation Name':'Example','Tender Count':'1'},{'Organisation Name':'Empty','Tender Count':'0'}],['Organisation Name','Tender Count'])
            write('organisation_tenders.csv',[{'Organisation Name':'Example','Tender ID':'2026_TEST_123456_1','Closing Date':'01-Jan-2099 05:00 PM'}],['Organisation Name','Tender ID','Closing Date'])
            with patch.object(finalizer,'ROOT',root),patch('nightly_cleanup.cleanup'),patch('inventory_summary.write_summary'),patch.dict(os.environ,{'ALLOW_RSP_RECOVERY':'0'}):
                finalizer.main()
            data=json.loads((root/'data/live_snapshot.json').read_text())
            self.assertTrue(data['verified']);self.assertEqual(data['portal_tender_count'],1)
            self.assertEqual(data['tender_ids'],['2026_TEST_123456_1'])

if __name__=='__main__':unittest.main()
