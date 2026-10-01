import hashlib,hmac,json,os,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import server
import google_sheet_store as store

class SheetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'GOOGLE_SHEETS_WEBAPP_URL':'https://script.google.com/macros/s/example/exec','GOOGLE_SHEETS_SHARED_SECRET':'s'*48,'VISITOR_SESSION_SECRET':'session-secret'})
        self.env.start()
        self.db=patch.object(server,'USER_DB_PATH',Path(self.tmp.name)/'users.db');self.db.start()
        self.pg=patch.object(server,'DATABASE_URL','');self.pg.start()
        self.client=server.app.test_client()
    def tearDown(self):
        self.pg.stop();self.db.stop();self.env.stop();self.tmp.cleanup()
    def data(self):return {'registration_id':str(uuid.uuid4()),'name':'Test user','mobile':'9876543210','district':'Dewas'}
    def affidavit(self):return {'bidderName':'Test user','firmName':'Firm','status':'Proprietor','place':'Dewas','relative':'no','relativeName':'','relativePost':'','relativePosting':''}
    def test_remote_requests_are_signed_and_retry_uses_same_request_id(self):
        import requests
        response=Mock(ok=True);response.json.return_value={'ok':True,'profile':{}}
        with patch.object(store.requests,'post',side_effect=[requests.Timeout(),response]) as post:
            store.call('read_affidavit',visitor_id=str(uuid.uuid4()))
        first=post.call_args_list[0].kwargs['json'];second=post.call_args_list[1].kwargs['json']
        self.assertEqual(first,second)
        expected=hmac.new(b's'*48,(first['timestamp']+'\n'+first['payload']).encode(),hashlib.sha256).hexdigest()
        self.assertEqual(first['signature'],expected)
        self.assertNotIn('s'*48,json.dumps(first))
    def test_registration_session_and_affidavit_use_sheet_and_never_local_db(self):
        data=self.data();visitor={'visitor_id':data['registration_id'],**{k:data[k] for k in ('name','mobile','district')}}
        visitor['mobile']='+919876543210'
        with patch.object(store,'call',return_value={'ok':True,'visitor':visitor,'affidavit_profile':self.affidavit()}) as remote:
            saved=self.client.post('/api/visitors/register',json=data)
            self.assertEqual(saved.status_code,200);self.assertEqual(saved.json['storage'],'google_sheets')
            token=saved.json['session_token']
            restored=self.client.post('/api/visitors/session',json={'session_token':token})
            self.assertEqual(restored.json['affidavit_profile'],self.affidavit())
            self.assertEqual([call.args[0] for call in remote.call_args_list],['register','session'])
        with patch.object(store,'call',return_value={'ok':True,'profile':self.affidavit()}) as remote:
            self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':self.affidavit()}).status_code,200)
            self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':token}).status_code,200)
            self.assertEqual([call.args[0] for call in remote.call_args_list],['save_affidavit','read_affidavit'])
        self.assertFalse(server.USER_DB_PATH.exists())
    def test_remote_failure_never_claims_success_or_falls_back(self):
        with patch.object(store,'call',side_effect=store.SheetStoreError('Retry')):
            response=self.client.post('/api/visitors/register',json=self.data())
            self.assertEqual(response.status_code,503);self.assertFalse(response.json['ok'])
        self.assertFalse(server.USER_DB_PATH.exists())
    def test_partial_configuration_and_profile_access_are_rejected(self):
        with patch.dict(os.environ,{'GOOGLE_SHEETS_SHARED_SECRET':''}):
            self.assertTrue(store.enabled())
            self.assertEqual(self.client.post('/api/visitors/register',json=self.data()).status_code,503)
        self.assertEqual(self.client.post('/api/visitors/affidavit',json={'visitor_id':str(uuid.uuid4())}).status_code,401)
        token=server.make_visitor_session(str(uuid.uuid4()))
        with patch.object(store,'call') as remote:
            self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':{'bidderName':'bad'}}).status_code,400)
            remote.assert_not_called()

if __name__=='__main__':unittest.main()
