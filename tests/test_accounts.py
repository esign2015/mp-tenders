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
        self.data={'first_name':'Bidder','middle_name':'','last_name':'Test','tehsil':'Kannod','mobile':'9876543210','district':'Dewas','password':self.password,'confirm_password':self.password}
    def tearDown(self):self.pg.stop();self.db.stop();self.env.stop();self.tmp.cleanup()
    def signup(self):
        r=self.client.post('/api/accounts/signup',json=self.data);self.assertEqual(r.status_code,200);return r.json
    def login(self,password=None):return self.client.post('/api/accounts/signin',json={'mobile':'9876543210','password':password or self.password})
    def test_batched_sheet_login_keeps_both_limits_in_one_lookup(self):
        with patch.dict(os.environ,{'GOOGLE_SHEETS_BATCH_ACCOUNT_LOOKUP':'1'}),patch.object(sheets,'enabled',return_value=True),server.app.test_request_context():
            with patch.object(server.account_service,'operation',return_value={'record':{'user_id':'example'},'rate_results':[{'allowed':True},{'allowed':True}]}) as operation:
                self.assertEqual(server.account_service.lookup_limited('+919876543210')['user_id'],'example')
                self.assertEqual(operation.call_count,1)
                self.assertEqual([item['limit'] for item in operation.call_args.kwargs['rate_checks']],[120,12])
            from account_access import AccountError
            for result in ({'record':None},{'record':None,'rate_results':[{'allowed':True},{'allowed':False}]}):
                with patch.object(server.account_service,'operation',return_value=result),self.assertRaises(AccountError):
                    server.account_service.lookup_limited('+919876543210')
    def test_remote_login_checks_overlap_and_still_enforce_rates(self):
        import threading
        barrier=threading.Barrier(3)
        def operation(action,**fields):
            barrier.wait(timeout=3)
            return {'allowed':True} if action=='rate' else {'record':{'user_id':'example'}}
        with patch.object(sheets,'enabled',return_value=True),patch.object(server.account_service,'operation',side_effect=operation),server.app.test_request_context():
            self.assertEqual(server.account_service.lookup_limited('+919876543210')['user_id'],'example')
        from account_access import AccountError
        def denied(action,**fields):
            return {'allowed':fields['limit']!=12} if action=='rate' else {'record':{'user_id':'example'}}
        with patch.object(sheets,'enabled',return_value=True),patch.object(server.account_service,'operation',side_effect=denied),server.app.test_request_context(),self.assertRaises(AccountError) as error:
            server.account_service.lookup_limited('+919876543210')
        self.assertEqual(error.exception.status,429)
        with patch.object(sheets,'call',side_effect=AssertionError('Readiness must not access Sheet')):
            self.assertEqual(self.client.get('/api/accounts/ready').status_code,200)

    def test_middle_name_rejects_numbers_in_signup_and_profile(self):
        for value in ('9876543210','Kumar123','कुमार१२३','Ram@example'):
            response=self.client.post('/api/accounts/signup',json={**self.data,'middle_name':value})
            self.assertEqual(response.status_code,400)
            self.assertIn('Middle Name',response.json['message'])
        result=self.client.post('/api/accounts/signup',json={**self.data,'middle_name':'कुमार'}).json
        self.assertEqual(result['profile']['middle_name'],'कुमार')
        response=self.client.post('/api/accounts/profile',json={**self.data,'session_token':result['session_token'],'middle_name':'9876543210'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(server.account_service.get(user_id=result['visitor_id'])['middle_name'],'कुमार')

    def test_legacy_names_prompt_and_save_without_changing_other_profile_fields(self):
        result=self.signup();record=server.account_service.get(user_id=result['visitor_id'])
        record.update(last_name='',middle_name='9876543210',name='Bidder 9876543210')
        self.assertTrue(server.account_service.update(record,record['revision']))
        login=self.login().json
        self.assertEqual(login['profile_corrections'],['last_name','middle_name'])
        self.assertEqual(login['profile']['middle_name'],'9876543210')
        self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':login['session_token']}).json['profile_corrections'],['last_name','middle_name'])
        body={'session_token':login['session_token'],'first_name':'Bidder','middle_name':'9876543210','last_name':'Test'}
        self.assertEqual(self.client.post('/api/accounts/profile-names',json=body).status_code,400)
        self.assertEqual(self.client.post('/api/accounts/profile-names',json={**body,'session_token':'invalid'}).status_code,401)
        repaired=self.client.post('/api/accounts/profile-names',json={**body,'middle_name':''})
        self.assertEqual(repaired.status_code,200);self.assertEqual(repaired.json['profile_corrections'],[])
        updated=server.account_service.get(user_id=result['visitor_id'])
        for key in ('mobile','district','tehsil','password_hash'):self.assertEqual(updated[key],record[key])
        self.assertEqual(updated['name'],'Bidder Test')
        self.assertEqual(self.login().json['profile_corrections'],[])

    def test_forgot_uses_one_remote_lookup_and_local_throttling(self):
        import account_access
        with patch.dict(account_access._forgot_limits,{},clear=True),patch.object(sheets,'enabled',return_value=True),patch.object(sheets,'call',return_value={'record':{'user_id':'example'}}) as call:
            response=self.client.post('/api/accounts/forgot-check',json={'mobile':'9123456789'})
            self.assertEqual(response.status_code,200)
            call.assert_called_once_with('account_lookup',mobile='+919123456789')
            for _ in range(12):response=self.client.post('/api/accounts/forgot-check',json={'mobile':'9123456789'})
            self.assertEqual(response.status_code,429)
        with patch.dict(account_access._forgot_limits,{},clear=True),patch.object(sheets,'enabled',return_value=True),patch.object(sheets,'call',return_value={'record':None}):
            response=self.client.post('/api/accounts/forgot-check',json={'mobile':'9123456789'})
            self.assertEqual(response.status_code,404);self.assertEqual(response.json['code'],'signup_required')

    def test_surname_required_and_middle_name_optional(self):
        for value in ('','   '):
            self.assertEqual(self.client.post('/api/accounts/signup',json={**self.data,'last_name':value}).status_code,400)
        result=self.signup()
        self.assertEqual(result['profile']['middle_name'],'')
        response=self.client.post('/api/accounts/profile',json={**self.data,'session_token':result['session_token'],'last_name':''})
        self.assertEqual(response.status_code,400)
        self.assertEqual(server.account_service.get(user_id=result['visitor_id'])['last_name'],'Test')

    def test_mobile_range_and_numeric_validation(self):
        from account_access import account_mobile
        for value in ('6000000000','9999999999'):
            self.assertEqual(account_mobile(server,value),'+91'+value)
        for value in ('5999999999','10000000000','987654321','abc9876543210','98765-43210','९८७६५४३२१०'):
            self.assertEqual(account_mobile(server,value),'')
            self.assertEqual(self.client.post('/api/accounts/signup',json={**self.data,'mobile':value}).status_code,400)

    def test_remote_details_do_not_hold_login_and_are_session_bound(self):
        import account_access
        from concurrent.futures import Future
        result=self.signup();token=result['session_token'];record=server.account_service.get(user_id=result['visitor_id'])
        pending=Future()
        with patch.object(sheets,'enabled',return_value=True),patch.object(account_access._detail_pool,'submit',return_value=pending),server.app.test_request_context():
            response=server.account_service.response(record,token).json
        key=response['details_id'];self.assertTrue(key);self.assertFalse(pending.done())
        body={'session_token':token,'details_id':key}
        self.assertTrue(self.client.post('/api/accounts/details',json=body).json['pending'])
        other=self.client.post('/api/accounts/signup',json={**self.data,'mobile':'9123456789'}).json['session_token']
        self.assertEqual(self.client.post('/api/accounts/details',json={**body,'session_token':other}).status_code,404)
        pending.set_result({'firmName':'Saved Firm'})
        self.assertEqual(self.client.post('/api/accounts/details',json=body).json['affidavit_profile']['firmName'],'Saved Firm')
        self.client.post('/api/accounts/logout',json={'session_token':token})
        self.assertEqual(self.client.post('/api/accounts/details',json=body).status_code,401)
        account_access._detail_slots.release()
        with account_access._detail_lock:account_access._detail_jobs.pop(key,None)

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
    def test_new_login_revokes_old_session_and_preserves_affidavit(self):
        first=self.signup()
        profile={'bidderName':'Bidder Test','firmName':'Firm','status':'Proprietor','place':'Dewas','relative':'no','parentRelation':'W/o','parentName':'Spouse Test','address':'Ward 2, Dewas'}
        saved=self.client.post('/api/visitors/affidavit',json={'session_token':first['session_token'],'profile':profile});self.assertEqual(saved.status_code,200)
        second=self.login().json
        self.assertEqual(self.client.post('/api/accounts/session-check',json={'session_token':first['session_token']}).status_code,401)
        self.assertEqual(self.client.post('/api/accounts/session-check',json={'session_token':second['session_token']}).status_code,200)
        record=server.account_service.get(user_id=first['visitor_id']);self.assertEqual(len(record['sessions']),1)
        restored=self.client.post('/api/accounts/session',json={'session_token':second['session_token']})
        self.assertEqual(restored.json['affidavit_profile']['firmName'],'Firm')
        for key in ('parentRelation','parentName','address'):self.assertEqual(restored.json['affidavit_profile'][key],profile[key])
        self.assertEqual(self.client.post('/api/accounts/logout',json={'session_token':first['session_token']}).status_code,401)
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
    @patch('account_access.time.time', return_value=1800000000)
    def test_expired_reset_and_rate_limits_and_cas(self, _clock):
        account=self.signup()
        with patch.object(server,'require_admin',return_value=('admin@example.com','')):
            r=self.client.post('/api/admin/accounts/reset-link',json={'mobile':'9876543210','identity_verified':True})
        token=r.json['reset_url'].split('#reset=')[1];record=server.account_service.get(user_id=account['visitor_id']);revision=record['revision']
        record['reset']['expires']=int(time.time())-1;self.assertTrue(server.account_service.update(record,revision))
        self.assertFalse(server.account_service.update(record,revision))
        self.assertEqual(self.client.post('/api/accounts/reset-password',json={'reset_token':token,'password':self.password,'confirm_password':self.password}).status_code,400)
        for _ in range(15):response=self.login('wrong password')
        self.assertEqual(response.status_code,429)
    def test_daily_logout_and_maintenance_boundaries_in_ist(self):
        from datetime import datetime
        def stamp(value):return datetime.fromisoformat(value).timestamp()
        morning=stamp('2026-10-02T09:00:00+05:30')
        with patch('account_access.time.time',return_value=morning):
            account=self.signup();token=account['session_token']
            self.assertEqual(account['session_expires_at'],stamp('2026-10-02T23:30:00+05:30'))
        with patch('account_access.time.time',return_value=stamp('2026-10-02T23:29:59+05:30')):
            self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':token}).status_code,200)
        for value in ('2026-10-02T23:30:00+05:30','2026-10-03T00:00:00+05:30','2026-10-03T00:29:59+05:30'):
            with patch('account_access.time.time',return_value=stamp(value)):
                for route,body in (('/signin',{'mobile':self.data['mobile'],'password':self.password}),('/signup',self.data),('/session',{'session_token':token})):
                    response=self.client.post('/api/accounts'+route,json=body)
                    self.assertEqual(response.status_code,503);self.assertEqual(response.json['code'],'maintenance')
                self.assertTrue(self.client.get('/api/accounts/ready').json['maintenance'])
        with patch('account_access.time.time',return_value=stamp('2026-10-03T00:30:00+05:30')):
            self.assertFalse(self.client.get('/api/accounts/ready').json['maintenance'])
            self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':token}).status_code,401)
            self.assertEqual(self.client.post('/api/accounts/activity',json={'session_token':token}).status_code,401)
            self.assertEqual(self.login().status_code,200)

    def test_signin_that_crosses_maintenance_start_cannot_issue_a_session(self):
        from datetime import datetime
        from account_access import AccountError
        at=datetime.fromisoformat('2026-10-02T23:30:00+05:30').timestamp()
        with patch('account_access.time.time',return_value=at),self.assertRaises(AccountError) as error:
            server.account_service.issue({'user_id':'example','sessions':[]})
        self.assertEqual(error.exception.code,'maintenance')

    def test_old_long_lived_session_cannot_return_after_daily_cutoff(self):
        from datetime import datetime
        old=datetime.fromisoformat('2026-10-01T09:00:00+05:30').timestamp()
        with patch('account_access.time.time',return_value=old):account=self.signup()
        record=server.account_service.get(user_id=account['visitor_id']);revision=record['revision']
        record['sessions'][0].pop('issued_at');record['sessions'][0]['expires']=old+30*86400
        server.account_service.update(record,revision)
        with patch('account_access.time.time',return_value=datetime.fromisoformat('2026-10-02T00:30:00+05:30').timestamp()):
            self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':account['session_token']}).status_code,401)

    def test_refresh_preserves_session_and_never_counts_as_new_login(self):
        account=self.signup();token=account['session_token']
        record=server.account_service.get(user_id=account['visitor_id']);initial=record['sessions'][0]['last_active']
        def counts():
            conn=server.visitor_db()
            try:return (conn.execute('SELECT visit_count FROM visitor_registrations WHERE visitor_id=?',(account['visitor_id'],)).fetchone()['visit_count'],conn.execute('SELECT COUNT(*) AS total FROM visitor_events').fetchone()['total'])
            finally:conn.close()
        before=counts()
        with patch('account_access.time.time',return_value=initial+3600):
            for _ in range(3):
                response=self.client.post('/api/accounts/session',json={'session_token':token})
                self.assertEqual(response.status_code,200);self.assertEqual(response.json['session_token'],token);self.assertFalse(response.json['new_login'])
        self.assertEqual(counts(),before)
        self.assertEqual(server.account_service.get(user_id=account['visitor_id'])['sessions'],record['sessions'])
        self.assertTrue(self.login().json['new_login']);self.assertEqual(counts()[0],before[0]+1)

    def test_refresh_sheet_enrichment_is_read_only(self):
        import account_access
        class ImmediatePool:
            def submit(self,callback):
                from concurrent.futures import Future
                result=Future();result.set_result(callback());return result
        with patch.object(account_access,'_detail_pool',ImmediatePool()),patch.object(sheets,'call',return_value={'profile':{'firmName':'Firm'}}) as call:
            key=account_access.queue_details('visitor','token',record_visit=False)
            call.assert_called_once_with('read_affidavit',visitor_id='visitor')
            with account_access._detail_lock:account_access._detail_jobs.pop(key,None)
    def test_self_profile_and_password_change_require_authenticated_owner(self):
        first=self.signup();second=self.login().json;token=second['session_token']
        self.assertEqual(self.client.post('/api/accounts/profile',json={'first_name':'Changed','tehsil':'Harda','district':'Harda'}).status_code,401)
        changed=self.client.post('/api/accounts/profile',json={'session_token':token,'first_name':'Changed','last_name':'Test','tehsil':'Harda','district':'Harda','mobile':'9123456789'})
        self.assertEqual(changed.status_code,200);self.assertEqual(changed.json['profile']['mobile'],'+919876543210')
        self.assertEqual(changed.json['profile']['district'],'Harda')
        body={'session_token':token,'current_password':'wrong','password':'Newpass2!','confirm_password':'Newpass2!'}
        self.assertEqual(self.client.post('/api/accounts/change-password',json=body).json['code'],'wrong_password')
        body['current_password']=self.password
        self.assertEqual(self.client.post('/api/accounts/change-password',json=body).status_code,200)
        for old in (token,second['session_token']):self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':old}).status_code,401)
        self.assertEqual(self.login().status_code,401);self.assertEqual(self.login('Newpass2!').status_code,200)
    def test_name_limits_and_district_tehsil_validation(self):
        for fields in ({'first_name':'A'*16},{'middle_name':'B'*11},{'last_name':'C'*16},{'tehsil':'Indore'}):
            self.assertEqual(self.client.post('/api/accounts/signup',json={**self.data,**fields}).status_code,400)
        result=self.signup();self.assertEqual(result['profile']['tehsil'],'Kannod');self.assertEqual(result['profile']['last_name'],'Test')

    def test_admin_block_revokes_existing_sessions_and_unblock_requires_new_login(self):
        result=self.signup();body={'mobile':self.data['mobile'],'blocked':True}
        self.assertEqual(self.client.post('/api/admin/accounts/access',json=body).status_code,401)
        with patch.object(server,'require_admin',return_value=('admin@example.com','')):
            r=self.client.post('/api/admin/accounts/access',json=body);self.assertTrue(r.json['blocked']);self.assertNotIn('password_hash',str(r.json))
            self.assertEqual(self.login().json['code'],'account_blocked')
            self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':result['session_token']}).status_code,401)
            self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':result['session_token']}).status_code,401)
            self.client.post('/api/admin/accounts/access',json={**body,'blocked':False})
        self.assertEqual(self.login().status_code,200)
        self.assertEqual(self.client.post('/api/accounts/session',json={'session_token':result['session_token']}).status_code,401)

    def test_sheet_failure_does_not_create_local_password_account(self):
        with patch.dict(os.environ,{'GOOGLE_SHEETS_WEBAPP_URL':'https://script.google.com/macros/s/example/exec','GOOGLE_SHEETS_SHARED_SECRET':'s'*48}),patch.object(sheets,'call',side_effect=sheets.SheetStoreError('Unavailable')):
            self.assertEqual(self.client.post('/api/accounts/signup',json=self.data).status_code,503)
        self.assertFalse(server.USER_DB_PATH.exists())
    def test_anonymous_profile_cannot_bypass_password_account(self):
        account=self.signup()
        self.assertEqual(self.client.post('/api/visitors/register',json={'registration_id':account['visitor_id'],'name':'Bidder Test','mobile':self.data['mobile'],'district':self.data['district']}).status_code,410)
        legacy=server.make_visitor_session(account['visitor_id'])
        self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':legacy}).status_code,401)
    def test_unconnected_ephemeral_storage_cannot_accept_password_accounts(self):
        with patch.dict(os.environ,{'ALLOW_EPHEMERAL_ACCOUNTS':'0'}):
            self.assertEqual(self.client.post('/api/accounts/signup',json=self.data).status_code,503)
        self.assertFalse(server.USER_DB_PATH.exists())

if __name__=='__main__':unittest.main()
