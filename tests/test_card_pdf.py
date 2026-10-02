import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from pypdf import PdfReader
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from telegram_card_pdf import make_card_pdf, AD_LINKS
from pdf_promotions import COMMUNITY_LINKS
import telegram_alerts as alerts

class CardPdfTests(unittest.TestCase):
    def test_all_ids_once_ads_do_not_change_count_and_links_are_clickable(self):
        rows=[{'Tender ID':f'2026_UAD_{500000+i}_1','Title':'A long title with an organisation '+'word '*150,'Closing Date':'01-Jan-2099 05:00 PM','Organisation':'Very long organisation '*50,'PAC Amount':'5000000','EMD Fee':'1000','Tender Fee':'500','Processing Fee':'295'} for i in range(13)]
        with tempfile.TemporaryDirectory() as tmp:
            p=make_card_pdf(rows,Path(tmp)/'cards.pdf','New Published Today',total_available=99,filter_detail='New Published Today',filter_live=False)
            reader=PdfReader(p);text='\n'.join(p.extract_text() for p in reader.pages)
            self.assertEqual(len(reader.pages),4)
            self.assertEqual(reader.pages[0].extract_text().count("2026_UAD_"),4)
            self.assertEqual(reader.pages[1].extract_text().count("2026_UAD_"),4)
            self.assertIn('Total Records: 13 out of 99',text)
            for row in rows:self.assertEqual(text.count(row['Tender ID']),1)
            self.assertEqual(text.count('ADVERTISEMENT'),4)
            self.assertIn('1,000.00',text);self.assertIn('1,795.00',text)
            links={str(a.get_object().get('/A',{}).get('/URI','')) for page in reader.pages for a in page.get('/Annots',[])}
            self.assertTrue({url for _,url in AD_LINKS}<=links)
            self.assertIn('Rs 1,000.00 per tender',' '.join(text.split()))
            for page in reader.pages:
                self.assertIn('MP Tender Alerts',page.extract_text())
                page_links={str(a.get_object().get('/A',{}).get('/URI','')) for a in page.get('/Annots',[])}
                self.assertTrue({url for _,url in (*AD_LINKS,*COMMUNITY_LINKS)}<=page_links)

    def test_evening_sends_matching_table_and_card_selection(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,1,20,45,tzinfo=alerts.IST)
        rows=[{'Tender ID':'today','Published Date':'01-Oct-2026 10:00 AM','Closing Date':'05-Oct-2026 05:00 PM'},{'Tender ID':'old','Published Date':'30-Sep-2026 10:00 AM','Closing Date':'05-Oct-2026 05:00 PM'}]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'master.csv';p.write_text('Tender ID\n')
            with patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':'evening_new'}),patch.object(alerts,'CSV_PATH',p),patch.object(alerts,'datetime',Clock),patch.object(alerts,'load_report_rows',return_value=rows),patch.object(alerts,'telegram_message'),patch.object(alerts,'telegram_document') as send,patch.object(alerts,'make_pdf',return_value=Path('table.pdf')) as table,patch('telegram_card_pdf.make_card_pdf',return_value=Path('card.pdf')) as cards:
                self.assertEqual(alerts.main(),0)
                self.assertEqual(send.call_count,2)
                self.assertEqual(table.call_args.args[0],cards.call_args.args[0])
                self.assertEqual([r['Tender ID'] for r in cards.call_args.args[0]],['today'])
                self.assertEqual(cards.call_args.kwargs['filter_live'],False)

    def test_second_pdf_failure_does_not_mark_evening_success(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,1,20,45,tzinfo=alerts.IST)
        rows=[{'Tender ID':'today','Published Date':'01-Oct-2026 10:00 AM','Closing Date':'05-Oct-2026 05:00 PM'}]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'master.csv';p.write_text('Tender ID\n')
            with patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','NOTIFY_MODE':'evening_new'}),patch.object(alerts,'CSV_PATH',p),patch.object(alerts,'datetime',Clock),patch.object(alerts,'load_report_rows',return_value=rows),patch.object(alerts,'telegram_message'),patch.object(alerts,'telegram_document'),patch.object(alerts,'make_pdf',return_value=Path('table.pdf')),patch('telegram_card_pdf.make_card_pdf',side_effect=RuntimeError('card PDF failed')):
                with self.assertRaises(RuntimeError):alerts.main()

if __name__=='__main__':unittest.main()
