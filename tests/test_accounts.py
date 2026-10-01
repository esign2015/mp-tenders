import json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import server
import google_sheet_store as sheets

class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ADMIN_SESSION_SECRET':'account-tests','GOOGLE_SHEETS_WEBAPP_URL':'','GOOGLE_SHEETS_SHARED_SECRET':'','VISITOR_PROFILE_LEGACY_ALLOWED':'0','ALLOW_EPHEMERAL_ACCOUNTS':'1'});self.env.start()
        self.db=patch.object(server,'USER_DB_PATH',Path(self.tmp.name)/'users.db');self.db.start()
        self.pg=patch.object(server,'DATABASE_URL','');self.pg.start();self.client=server.app.test_client()
        self.password='Private1!'
        self.data={'name':'Bidder Test','mobile':'9876543210','district':'Dewas','password':self.password,'confirm_password':self.password}
    def tearDown(self):self.pg.stop();self.db.stop();self.env.stop();self.tmp.cleanup()
    def signup(self):
        r=self.client.post('/api/accounts/signup',json=self.data);self.assertEqual(r.status_code,200);return r.json
    def login(self,password=None):return self.client.post('/api/accounts/signin',json={'mobile':'9876543210','password':password or self.password})
    def test_signup_hash_and_duplicate_phone_and_failed_login(self):
        result=self.signup();record=server.account_service.get(user_id=result['visitor_id'])
        self.assertNotIn(self.password,json.dumps(record));self.assertTrue(record['password_hash'].startswith('scrypt:32768:8:3$'))
        self.assertNotIn(result['session_token'],json.dumps(record))
        self.assertEqual(self.client.post('/api/accounts/signup',json={**self.data,'mobile':'+919876543210'}).status_code,409)
        self.assertEqual(self.login('incorrect password').status_code,401)
        self.assertEqual(self.login().json['visitor_id'],result['visitor_id'])
        self.assertEqual(self.client.post('/api/accounts/signup',json={**self.data,'mobile':'9123456789','password':'short','confirm_password':'short'}).status_code,400)
    def test_password_policy_and_signup_routing(self):
        from account_access import AccountError
        for password in ('Abcde1!', 'abcdef1!', 'ABCDEF1!', 'Abcdefg!', 'Abcdef12', 'Abcdef1 '):
            with self.subTest(password=password),self.assertRaises(AccountError):server.account_service.password(password)
        self.assertTrue(server.account_service.password('Abcdef1!').startswith('scrypt:'))
        unknown=self.client.post('/api/accounts/signin',json={'mobile':self.data['mobile'],'password':self.password})
        self.assertEqual(unknown.status_code,404);self.assertEqual(unknown.json['code'],'signup_required')
        unknown=self.client.post('/api/accounts/forgot-check',json={'mobile':self.data['mobile']})
        self.assertEqual(unknown.status_code,404);self.assertEqual(unknown.json['code'],'signup_required')
        self.signup()
        duplicate=self.client.post('/api/accounts/signup',json={**self.data,'mobile':'+919876543210'})
        self.assertEqual(duplicate.status_code,409);self.assertEqual(duplicate.json['code'],'account_exists')
        self.assertEqual(self.client.post('/api/accounts/forgot-check',json={'mobile':self.data['mobile']}).status_code,200)
    def test_cross_device_affidavit_and_logout_only_this_session(self):
        first=self.signup();second=self.login().json
        profile={'bidderName':'Bidder Test','firmName':'Firm','status':'Proprietor','place':'Dewas','relative':'no'}
        saved=self.client.post('/api/visitors/affidavit',json={'session_token':first['session_token'],'profile':profile});self.assertEqual(saved.status_code,200)
        restored=self.client.post('/api/accounts/session',json={'session_token':second['session_token']})
        self.assertEqual(restored.json['affidavit_profile']['firmName'],'Firm')
        self.assertEqual(self.client.post('/api/accounts/logout',json={'session_token':first['session_token']}).status_code,200)
        self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':first['session_token']}).status_code,401)
        self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':second['session_token']}).status_code,200)
    def test_reset_admin_only_once_and_invalidates_existing_logins(self):
        account=self.signup();body={'mobile':'9876543210','identity_verified':True}
        self.assertEqual(self.client.post('/api/admin/accounts/reset-link',json=body).status_code,401)
        with patch.object(server,'require_admin',return_value=('admin@example.com','')):
            self.assertEqual(self.client.post('/api/admin/accounts/reset-link',json={'mobile':'9876543210'}).status_code,400)
            result=self.client.post('/api/admin/accounts/reset-link',json=body);self.assertEqual(result.status_code,200)
        token=result.json['reset_url'].split('#reset=')[1]
        self.assertNotIn(token,json.dumps(server.account_service.get(user_id=account['visitor_id'])))
        new='Different2!';reset={'reset_token':token,'password':new,'confirm_password':new}
        self.assertEqual(self.client.post('/api/accounts/reset-password',json=reset).status_code,200)
        self.assertEqual(self.client.post('/api/accounts/reset-password',json=reset).status_code,400)
        self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':account['session_token']}).status_code,401)
        self.assertEqual(self.login().status_code,401);self.assertEqual(self.login(new).status_code,200)
    def test_expired_reset_and_rate_limits_and_cas(self):
        account=self.signup()
        with patch.object(server,'require_admin',return_value=('admin@example.com','')):
            r=self.client.post('/api/admin/accounts/reset-link',json={'mobile':'9876543210','identity_verified':True})
        token=r.json['reset_url'].split('#reset=')[1];record=server.account_service.get(user_id=account['visitor_id']);revision=record['revision']
        record['reset']['expires']=int(time.time())-1;self.assertTrue(server.account_service.update(record,revision))
        self.assertFalse(server.account_service.update(record,revision))
        self.assertEqual(self.client.post('/api/accounts/reset-password',json={'reset_token':token,'password':self.password,'confirm_password':self.password}).status_code,400)
        for _ in range(15):response=self.login('wrong password')
        self.assertEqual(response.status_code,429)
    def test_idle_expiry_and_activity_cannot_revive_expired_session(self):
        account=self.signup();token=account['session_token'];record=server.account_service.get(user_id=account['visitor_id'])
        initial=record['sessions'][0]['last_active']
        with patch('account_access.time.time',return_value=initial+800):
            self.assertEqual(self.client.post('/api/accounts/activity',json={'session_token':token}).status_code,200)
        with patch('account_access.time.time',return_value=initial+1700):
            self.assertEqual(self.client.post('/api/accounts/activity',json={'session_token':token}).status_code,401)
            self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':token}).status_code,401)
    def test_self_profile_and_password_change_require_authenticated_owner(self):
        first=self.signup();second=self.login().json;token=first['session_token']
        self.assertEqual(self.client.post('/api/accounts/profile',json={'name':'Changed','district':'Harda'}).status_code,401)
        changed=self.client.post('/api/accounts/profile',json={'session_token':token,'name':'Changed','district':'Harda','mobile':'9123456789'})
        self.assertEqual(changed.status_code,200);self.assertEqual(changed.json['profile']['mobile'],'+919876543210')
        self.assertEqual(self.login().json['profile']['district'],'Harda')
        body={'session_token':token,'current_password':'wrong','password':'Newpass2!','confirm_password':'Newpass2!'}
        self.assertEqual(self.client.post('/api/accounts/change-password',json=body).json['code'],'wrong_password')
        body['current_password']=self.password
        self.assertEqual(self.client.post('/api/accounts/change-password',json=body).status_code,200)
        for old in (token,second['session_token']):self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':old}).status_code,401)
        self.assertEqual(self.login().status_code,401);self.assertEqual(self.login('Newpass2!').status_code,200)
    def test_sheet_failure_does_not_create_local_password_account(self):
        with patch.dict(os.environ,{'GOOGLE_SHEETS_WEBAPP_URL':'https://script.google.com/macros/s/example/exec','GOOGLE_SHEETS_SHARED_SECRET':'s'*48}),patch.object(sheets,'call',side_effect=sheets.SheetStoreError('Unavailable')):
            self.assertEqual(self.client.post('/api/accounts/signup',json=self.data).status_code,503)
        self.assertFalse(server.USER_DB_PATH.exists())
    def test_anonymous_profile_cannot_bypass_password_account(self):
        account=self.signup()
        self.assertEqual(self.client.post('/api/visitors/register',json={'registration_id':account['visitor_id'],'name':self.data['name'],'mobile':self.data['mobile'],'district':self.data['district']}).status_code,410)
        legacy=server.make_visitor_session(account['visitor_id'])
        self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':legacy}).status_code,401)
    def test_unconnected_ephemeral_storage_cannot_accept_password_accounts(self):
        with patch.dict(os.environ,{'ALLOW_EPHEMERAL_ACCOUNTS':'0'}):
            self.assertEqual(self.client.post('/api/accounts/signup',json=self.data).status_code,503)
        self.assertFalse(server.USER_DB_PATH.exists())

if __name__=='__main__':unittest.main()
