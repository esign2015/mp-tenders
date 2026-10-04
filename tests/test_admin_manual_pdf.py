import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import server
import telegram_alerts as alerts

class ManualPdfTests(unittest.TestCase):
    def test_admin_dispatch_allows_three_table_reports_only(self):
        client=server.app.test_client()
        with patch('bidder_tools.queue_system_audit'),patch.object(server,'require_admin',return_value=('admin@example.com',None)),patch.object(server,'github_dispatch') as dispatch:
            for report in ('closing_today','closing_tomorrow','new_today'):
                response=client.post('/api/admin/action',json={'action':'telegram_pdf','report':report,'view':'table'})
                self.assertEqual(response.status_code,200)
                dispatch.assert_called_once_with('telegram_manual_pdf.yml',{'report':report,'view':'table'})
                dispatch.reset_mock()
            for report,view in (('all','table'),('closing_today','card'),('new_today','invalid')):
                self.assertEqual(client.post('/api/admin/action',json={'action':'telegram_pdf','report':report,'view':view}).status_code,400)
                dispatch.assert_not_called()

    def test_manual_only_sends_table_pdf_including_zero_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'list.csv';path.write_text('Tender ID\n')
            for rows in ([],[{'Tender ID':'sample','Closing Date':'01-Jan-2099 05:00 PM'}]):
                with self.subTest(rows=rows),patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':'manual','MANUAL_REPORT':'closing_tomorrow','MANUAL_VIEW':'table'}),patch.object(alerts,'CSV_PATH',path),patch.object(alerts,'load_report_rows',return_value=rows),patch.object(alerts,'live_rows',return_value=rows),patch.object(alerts,'telegram_message') as message,patch.object(alerts,'telegram_document') as document,patch.object(alerts,'make_pdf',return_value=Path('table.pdf')) as table:
                    self.assertEqual(alerts.main(),0)
                    self.assertEqual(document.call_count,1);message.assert_not_called()
                    table.assert_called_once()
                    self.assertTrue(document.call_args.args[3].startswith('🔔 एमपी टेंडर्स अलर्ट'))
                    self.assertNotIn('manual',document.call_args.args[3].lower())

    def test_legacy_manual_all_and_card_cannot_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'list.csv';path.write_text('Tender ID\n')
            for report,view in (('all','table'),('closing_today','card')):
                with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':'manual','MANUAL_REPORT':report,'MANUAL_VIEW':view}),patch.object(alerts,'CSV_PATH',path),patch.object(alerts,'load_report_rows',return_value=[]),patch.object(alerts,'telegram_document') as document:
                    with self.assertRaises(RuntimeError):alerts.main()
                    document.assert_not_called()

if __name__=='__main__':unittest.main()
