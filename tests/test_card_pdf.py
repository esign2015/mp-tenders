import os,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import telegram_alerts as alerts
class EveningTableTests(unittest.TestCase):
    def test_evening_sends_only_the_table_selection(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,1,20,45,tzinfo=alerts.IST)
        rows=[{'Tender ID':'today','Published Date':'01-Oct-2026 10:00 AM','Closing Date':'05-Oct-2026 05:00 PM'},{'Tender ID':'old','Published Date':'30-Sep-2026 10:00 AM','Closing Date':'05-Oct-2026 05:00 PM'}]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'master.csv';p.write_text('Tender ID\n')
            with patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':'evening_new'}),patch.object(alerts,'CSV_PATH',p),patch.object(alerts,'datetime',Clock),patch.object(alerts,'load_report_rows',return_value=rows),patch.object(alerts,'telegram_message'),patch.object(alerts,'telegram_document') as send,patch.object(alerts,'make_pdf',return_value=Path('table.pdf')) as table:
                self.assertEqual(alerts.main(),0)
                self.assertEqual(send.call_count,1)
                self.assertEqual([r['Tender ID'] for r in table.call_args.args[0]],['today'])
                self.assertEqual(table.call_args.kwargs['filter_live'],False)

    def test_table_pdf_failure_does_not_mark_evening_success(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,1,20,45,tzinfo=alerts.IST)
        rows=[{'Tender ID':'today','Published Date':'01-Oct-2026 10:00 AM','Closing Date':'05-Oct-2026 05:00 PM'}]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'master.csv';p.write_text('Tender ID\n')
            with patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':'evening_new'}),patch.object(alerts,'CSV_PATH',p),patch.object(alerts,'datetime',Clock),patch.object(alerts,'load_report_rows',return_value=rows),patch.object(alerts,'telegram_message'),patch.object(alerts,'telegram_document'),patch.object(alerts,'make_pdf',side_effect=RuntimeError('table PDF failed')):
                with self.assertRaises(RuntimeError):alerts.main()

if __name__=='__main__':unittest.main()
