import hashlib,hmac,json,os,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import server
import google_sheet_store as store

class SheetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'GOOGLE_SHEETS_WEBAPP_URL':'https://script.google.com/macros/s/example/exec','GOOGLE_SHEETS_SHARED_SECRET':'s'*48,'ADMIN_SESSION_SECRET':'session-secret','VISITOR_PROFILE_LEGACY_ALLOWED':'1'})
        self.env.start()
        self.db=patch.object(server,'USER_DB_PATH',Path(self.tmp.name)/'users.db');self.db.start()
        self.pg=patch.object(server,'DATABASE_URL','');self.pg.start()
        self.client=server.app.test_client()
    def tearDown(self):
        self.pg.stop();self.db.stop();self.env.stop();self.tmp.cleanup()
    def data(self):return {'registration_id':str(uuid.uuid4()),'name':'Test user','mobile':'9876543210','district':'Dewas'}
    def affidavit(self):return {'bidderName':'Test user','firmName':'Firm','status':'Proprietor','place':'Dewas','email':'office@example.test','relative':'no','relativeName':'','relativePost':'','relativePosting':''}
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
        self.assertEqual(post.call_args_list[0].kwargs['timeout'],(10,60))
    def test_signup_lost_reply_recovers_only_the_same_saved_account(self):
        import requests
        record={'user_id':str(uuid.uuid4()),'mobile':'+919876543210','password_hash':'scrypt:test','sessions':[{'hash':'original','expires':123}]}
        response=Mock(ok=True);response.json.return_value={'ok':True,'record':{**record,'revision':1}}
        with patch.object(store.requests,'post',side_effect=[requests.Timeout(),response]) as post:
            result=store.call('account_create',record=record)
        self.assertEqual(result['record']['sessions'],record['sessions'])
        self.assertEqual([json.loads(c.kwargs['json']['payload'])['action'] for c in post.call_args_list],['account_create','account_get'])
    def test_signup_recovery_cannot_accept_a_different_session(self):
        import requests
        record={'user_id':str(uuid.uuid4()),'sessions':[{'hash':'original'}]}
        read=Mock(ok=True);read.json.return_value={'ok':True,'record':{**record,'sessions':[{'hash':'different'}]}}
        retry=Mock(ok=True);retry.json.return_value={'ok':False,'status':409,'message':'Already registered'}
        with patch.object(store.requests,'post',side_effect=[requests.Timeout(),read,retry]):
            with self.assertRaises(store.SheetStoreError):store.call('account_create',record=record)
    def test_obsolete_registration_is_gone_and_canonical_account_profile_is_remote(self):
        from copy import deepcopy
        from account_access import next_daily_logout
        import time
        key=str(uuid.uuid4());token='acct_'+key+'.example';record={'user_id':key,'mobile':'+919876543210','password_hash':'scrypt:test','revision':1,'sessions':[{'hash':server.account_service.digest(token),'expires':int(time.time())+3600,'issued_at':int(time.time())}],'affidavit_profile':self.affidavit()}
        with patch.object(store,'call',return_value={'record':deepcopy(record)}) as remote,patch.object(server,'visitor_db',side_effect=AssertionError('No SQLite fallback')):
            self.assertEqual(self.client.post('/api/visitors/register',json=self.data()).status_code,404)
            response=self.client.post('/api/visitors/affidavit',json={'session_token':token})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json['profile'],self.affidavit());remote.assert_called_once_with('account_get',user_id=key)
        self.assertFalse(server.USER_DB_PATH.exists())
    def test_remote_failure_never_claims_success_or_falls_back(self):
        with patch.object(store,'call',side_effect=store.SheetStoreError('Retry')):
            response=self.client.post('/api/accounts/signin',json={'mobile':'9876543210','password':'Private1!'})
            self.assertEqual(response.status_code,503);self.assertFalse(response.json['ok'])
        self.assertFalse(server.USER_DB_PATH.exists())
    def test_partial_configuration_and_profile_access_are_rejected(self):
        with patch.dict(os.environ,{'GOOGLE_SHEETS_SHARED_SECRET':''}):
            self.assertTrue(store.enabled())
            self.assertEqual(self.client.post('/api/accounts/signin',json={'mobile':'9876543210','password':'Private1!'}).status_code,503)
        self.assertEqual(self.client.post('/api/visitors/affidavit',json={'visitor_id':str(uuid.uuid4())}).status_code,401)
        token='legacy_'+str(uuid.uuid4())
        with patch.object(store,'call') as remote:
            self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':{'bidderName':'bad'}}).status_code,401)
            remote.assert_not_called()

if __name__=='__main__':unittest.main()
