import sys,json,tempfile,unittest
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from inventory_summary import verify_live_counts
from nightly_cleanup import IST

class LiveCounts(unittest.TestCase):
    def test_overnight_master_only_ids_remain_excluded_and_expiry_is_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            (root/'data/live_snapshot.json').write_text(json.dumps({'verified':True,'snapshot_at':'2026-10-01T19:05:00+05:30','tender_ids':['open','closed']}))
            orgs=[{'Organisation Name':'Test','Tender Count':'2'}]
            listed=[{'Tender ID':'open','Organisation Name':'Test','Closing Date':'02-Oct-2026 03:00 PM'},{'Tender ID':'closed','Organisation Name':'Test','Closing Date':'01-Oct-2026 09:00 PM'}]
            details={'old':{'Closing Date':'14-Oct-2026 03:00 PM'}}
            for hour in (0,8,9):
                result=verify_live_counts(root,orgs,listed,details,datetime(2026,10,2,hour,0,tzinfo=IST))
                self.assertTrue(result['counts_match']);self.assertEqual(result['dashboard_live_count'],1)
                self.assertEqual(result['closed_or_cancelled_count'],1);self.assertEqual(result['excluded_master_only_live_ids'],['old'])
            listed.append({'Tender ID':'extra','Organisation Name':'Test','Closing Date':'02-Oct-2026 03:00 PM'})
            self.assertFalse(verify_live_counts(root,orgs,listed,details)['counts_match'])
    def test_partial_snapshot_and_invalid_dates_are_not_reported_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            (root/'data/live_snapshot.json').write_text(json.dumps({'verified':False,'tender_ids':['a']}))
            result=verify_live_counts(root,[{'Organisation Name':'Test','Tender Count':'1'}],[{'Organisation Name':'Test','Tender ID':'a','Closing Date':'bad'}],{})
            self.assertFalse(result['counts_match']);self.assertEqual(result['invalid_closing_ids'],['a'])

class Retirement(unittest.TestCase):
    def test_retired_id_can_reenter_after_new_official_inventory(self):
        import csv
        from nightly_cleanup import cleanup
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            def write(name,ids):
                with (root/name).open('w',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=['Tender ID','Closing Date']);writer.writeheader()
                    writer.writerows({'Tender ID':tid,'Closing Date':'10-Oct-2026 03:00 PM'} for tid in ids)
            write('organisation_tenders.csv',['active']);write('all_tenders_org_detailed.csv',['active','retired'])
            (root/'data/live_snapshot.json').write_text(json.dumps({'verified':True,'snapshot_at':'2026-10-01T19:05:00+05:30','copied_unique_tender_ids':1,'tender_ids':['active']}))
            report=cleanup(root,datetime(2026,10,1,20,0,tzinfo=IST));self.assertEqual(report['retired_tender_ids'],['retired'])
            write('organisation_tenders.csv',['active','retired']);write('all_tenders_org_detailed.csv',['active','retired'])
            (root/'data/live_snapshot.json').write_text(json.dumps({'verified':True,'snapshot_at':'2026-10-02T19:05:00+05:30','copied_unique_tender_ids':2,'tender_ids':['active','retired']}))
            report=cleanup(root,datetime(2026,10,2,20,0,tzinfo=IST));self.assertEqual(report['retired_tender_ids'],[])
            self.assertIn('retired',(root/'all_tenders_org_detailed.csv').read_text())
