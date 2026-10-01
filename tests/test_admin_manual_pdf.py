import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import server
import telegram_alerts as alerts

class ManualPdfTests(unittest.TestCase):
    def test_admin_dispatch_validates_and_passes_card_view(self):
        client=server.app.test_client()
        with patch.object(server,'require_admin',return_value=('admin@example.com',None)),patch.object(server,'github_dispatch') as dispatch:
            response=client.post('/api/admin/action',json={'action':'telegram_pdf','report':'all','view':'card'})
            self.assertEqual(response.status_code,200)
            dispatch.assert_called_once_with('telegram_manual_pdf.yml',{'report':'all','view':'card'})
            dispatch.reset_mock()
            self.assertEqual(client.post('/api/admin/action',json={'action':'telegram_pdf','report':'all','view':'invalid'}).status_code,400)
            dispatch.assert_not_called()
    def test_manual_card_only_and_table_only_delivery(self):
        rows=[{'Tender ID':'sample','Closing Date':'01-Jan-2099 05:00 PM'}]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'list.csv';path.write_text('Tender ID\n')
            for view in ('card','table'):
                with self.subTest(view=view),patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':'manual','MANUAL_REPORT':'all','MANUAL_VIEW':view}),patch.object(alerts,'CSV_PATH',path),patch.object(alerts,'load_report_rows',return_value=rows),patch.object(alerts,'live_rows',return_value=rows),patch.object(alerts,'telegram_message'),patch.object(alerts,'telegram_document') as document,patch.object(alerts,'make_pdf',return_value=Path('table.pdf')) as table,patch('telegram_card_pdf.make_card_pdf',return_value=Path('card.pdf')) as card:
                    self.assertEqual(alerts.main(),0)
                    self.assertEqual(document.call_count,1)
                    self.assertEqual(card.call_count,int(view=='card'))
                    self.assertEqual(table.call_count,int(view=='table'))

if __name__=='__main__':unittest.main()
