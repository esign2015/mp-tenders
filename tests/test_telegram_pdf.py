import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfReader
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import telegram_alerts as alerts
from pdf_promotions import SERVICE_LINKS, COMMUNITY_LINKS


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class TelegramPdfTests(unittest.TestCase):
    def test_table_second_page_cards_short_and_long_reports_keep_all_tenders(self):
        for count in (1,100):
            with self.subTest(count=count),tempfile.TemporaryDirectory() as temporary:
                rows=[{'Tender ID':f'2026_TEST_{500000+i}_1','Title':'Sample tender work','Closing Date':'01-Jan-2099 06:00 PM'} for i in range(count)]
                path=alerts.make_pdf(rows,str(Path(temporary)/'table.pdf'),'Table Report',filter_live=False)
                reader=PdfReader(path)
                self.assertGreaterEqual(len(reader.pages),2)
                second=reader.pages[1]
                self.assertIn('ADVERTISEMENT',second.extract_text())
                self.assertIn('MP Tender Alerts',second.extract_text())
                links={str(a.get_object().get('/A',{}).get('/URI','')) for a in second.get('/Annots',[])}
                self.assertTrue({url for _,url in (*SERVICE_LINKS,*COMMUNITY_LINKS)}<=links)
                text='\n'.join(p.extract_text() for p in reader.pages)
                for row in rows:self.assertEqual(text.count(row['Tender ID']),1)

    def test_merges_real_reference_and_fees_without_importing_detail_only_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tid = '2026_MPTAX_534364_2'
            write_csv(root/'all_tenders_org_detailed.csv', [{'Tender ID':tid,'Reference Number':'CTD/DC-2/STORE/2026/393','EMD Fee':'1000'}])
            write_csv(root/'organisation_tenders.csv', [{'Tender ID':tid,'Reference Number':tid,'Closing Date':'01-Jan-2099 06:00 PM'}])
            write_csv(root/'tender_details.csv', [{'Tender ID':tid,'Tender Fee':'2000','Processing Fee':'295'}, {'Tender ID':'history','Tender Fee':'3000'}])
            rows = alerts.load_report_rows(root/'all_tenders_org_detailed.csv')
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['Reference Number'],'CTD/DC-2/STORE/2026/393')
            self.assertEqual(alerts.total_tender_fee(rows[0]),Decimal('3295'))

    def test_evening_verified_portal_membership_matches_dashboard(self):
        now = datetime(2026,9,30,21,30,tzinfo=alerts.IST)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'data').mkdir()
            rows=[{'Tender ID':tid,'Closing Date':'01-Oct-2026 05:00 PM'} for tid in ('live','old-master')]
            snapshot={'verified':True,'snapshot_at':'2026-09-30T20:35:00+05:30','tender_ids':['live']}
            path=root/'data/live_snapshot.json'
            path.write_text(json.dumps(snapshot))
            write_csv(root/'all_tenders_org_detailed.csv',rows)
            self.assertEqual([r['Tender ID'] for r in alerts.load_report_rows(root/'all_tenders_org_detailed.csv',now)],['live'])
            self.assertEqual([r['Tender ID'] for r in alerts.live_rows(rows,now,root)],['live'])
            for change in ({'verified':False},{'snapshot_at':'2026-09-29T20:35:00+05:30'}, {'snapshot_at':'2026-09-30T18:35:00+05:30'}):
                path.write_text(json.dumps({**snapshot,**change}))
                self.assertEqual(len(alerts.live_rows(rows,now,root)),2 if change.get('verified') is False else 1)

    def test_fee_text_always_retains_two_decimal_places(self):
        for value in ('1000','1000.0','1000.00'):
            self.assertEqual(alerts.fee_text(Decimal(value)), '1,000.00')
        self.assertEqual(alerts.fee_text(Decimal('123.4')), '123.40')
        self.assertEqual(alerts.fee_text(None), 'Checking')

    def test_closing_instant_excludes_expired_equal_cancelled_and_unknown(self):
        now = datetime(2026,9,30,12,45,tzinfo=alerts.IST)
        rows = [{'Tender ID':name,'Closing Date':date} for name,date in (
            ('2026_AICTS_531643_1','28-Sep-2026 06:55 PM'),('equal','30-Sep-2026 12:45 PM'),
            ('future','30-Sep-2026 12:46 PM'),('unknown',''))]
        rows.append({'Tender ID':'cancelled','Closing Date':'01-Oct-2026 06:55 PM','Status':'Cancelled'})
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual([r['Tender ID'] for r in alerts.live_rows(rows,now,Path(temporary))],['future'])

    def test_total_uses_all_three_components_and_preserves_explicit_zero(self):
        self.assertEqual(alerts.total_tender_fee({'EMD Fee':'35,400','Tender Fee':'1000','Processing Fee':'295'}),Decimal('36695'))
        self.assertEqual(alerts.total_tender_fee({'EMD Fee':'0','Tender Fee':'0','Processing Fee':'0'}),Decimal(0))
        self.assertIsNone(alerts.total_tender_fee({'EMD Fee':'1000','Tender Fee':'','Processing Fee':'295'}))
        self.assertEqual(alerts.valid_reference('2026_MPTAX_534364_2'),'')

    def test_pdf_prints_total_and_removes_expired_rows_even_at_render_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            rows=[{'Tender ID':'2026_TEST_123456_1','Closing Date':'01-Jan-2099 06:00 PM','Title':'Work & materials',
                   'Reference Number':'NIT/42','PAC Amount':'NA','EMD Fee':'35400','Tender Fee':'1000','Processing Fee':'295'},
                  {'Tender ID':'2026_AICTS_531643_1','Closing Date':'28-Sep-2026 06:55 PM'}]
            with patch.object(alerts, 'CSV_PATH', Path(temporary)/'all_tenders_org_detailed.csv'):
                path=alerts.make_pdf(rows,str(Path(temporary)/'checked.pdf'),'All Live Tenders')
            text='\n'.join(page.extract_text() for page in PdfReader(path).pages)
            for value in ('Total Fee','EMD Fee','Form Fee','Processing Fee','36,695','NIT/42','NA'):
                self.assertIn(value,text)
            self.assertNotIn('2026_AICTS_531643_1',text)


if __name__ == '__main__':
    unittest.main()
