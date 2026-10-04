import csv,io,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from data_branch import runtime_path
import publish_data_checkpoint as publisher
from tender_history import changes
class RuntimeBranchTests(unittest.TestCase):
    def test_data_checkpoint_moves_only_data_branch_and_hydration_preserves_code(self):
        import prepare_data
        def git(cwd,*args):return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.DEVNULL,text=True).strip()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);remote=root/'remote.git';local=root/'repo';git(root,'init','--bare','--initial-branch=main',str(remote));git(root,'clone',str(remote),str(local));git(local,'config','user.name','test');git(local,'config','user.email','test@example.test')
            (local/'index.html').write_text('Protected application code');(local/'data').mkdir();(local/'data/schedule_config.json').write_text('{"keep":"configuration"}')
            path=local/'all_tenders_org_detailed.csv';path.write_text('Tender ID,Closing Date,Tested At\n2026_UAD_1_1,31-Dec-2099 05:30 PM,2026-10-04T12:00:00Z\n');git(local,'add','.');git(local,'commit','-m','initial');git(local,'push','origin','main');main=git(local,'rev-parse','HEAD');git(local,'push','origin','HEAD:refs/heads/tender-data')
            path.write_text(path.read_text()+'2026_UAD_2_1,31-Dec-2099 05:30 PM,2026-10-04T13:00:00Z\n')
            old=Path.cwd()
            try:
                os.chdir(local)
                with patch.dict(os.environ,{'RUNNER_TEMP':tmp}),patch.object(publisher,'DATA_BRANCH','tender-data'):
                    publisher.publish(['all_tenders_org_detailed.csv']);self.assertEqual(git(local,'ls-remote','origin','refs/heads/main').split()[0],main)
                    (local/'index.html').write_text('New local code');(local/'data/schedule_config.json').write_text('{"keep":"new configuration"}');prepare_data.prepare()
                self.assertEqual((local/'index.html').read_text(),'New local code');self.assertEqual(json.loads((local/'data/schedule_config.json').read_text()),{'keep':'new configuration'});self.assertIn('2026_UAD_2_1',path.read_text())
            finally:os.chdir(old)
    def test_history_records_verified_changes_and_prunes_closed_tenders(self):
        def raw(fee,stamp,closing):return ('Tender ID,Tender Fee,Tested At,Closing Date\n2026_UAD_1_1,'+fee+','+stamp+','+closing+'\n').encode()
        old=raw('500','2026-10-04T12:00:00Z','31-Dec-2099 05:30 PM');new=raw('1000','2026-10-04T13:00:00Z','31-Dec-2099 05:30 PM')
        data=json.loads(changes(old,new,b'',now=1800000000));self.assertEqual(data['tenders']['2026_UAD_1_1'][0]['before'],'500')
        closed=raw('1000','2026-10-04T13:00:00Z','01-Jan-2000 05:30 PM');self.assertEqual(json.loads(changes(new,closed,json.dumps(data),now=1800000000))['tenders'],{})
        self.assertEqual(json.loads(changes(new,old,b'',now=1800000000))['tenders'],{})
    def test_protected_and_private_paths_cannot_be_published(self):
        for path in ('index.html','../data/status.json','/tmp/backup.json','data/schedule_config.json','users.csv','data/users.db','data/private_backup.json','data/accounts.json'):
            self.assertFalse(runtime_path(path),path)
