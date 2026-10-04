import copy,json,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import server,bidder_tools as tools
import test_accounts as base
class BidderToolsTests(unittest.TestCase):
    setUp=base.AccountsTests.setUp
    tearDown=base.AccountsTests.tearDown
    signup=base.AccountsTests.signup
    def setUp(self):
        base.AccountsTests.setUp(self);self.account=self.signup();self.token=self.account['session_token'];self.tid='2026_UAD_540500_1';self.auth={'session_token':self.token};tools._alert_refreshed=0;tools._alert_index.clear()
    def post(self,path,**data):return self.client.post('/api/accounts/tools'+path,json={**self.auth,**data})
    def poll(self,path,body=None):
        response=self.client.get(path) if body is None else self.client.post(path,json=body)
        if response.json.get('job'):path+='?job='+response.json['job']
        for _ in range(100):
            if not response.json.get('pending') and not response.json.get('job'):return response
            time.sleep(.01);response=self.client.get(path) if body is None else self.client.post(path,json=body)
        self.fail('Background job timed out')
    def test_shortlist_persists_cross_session_and_does_not_leak_other_accounts(self):
        saved=self.post('/shortlist',tender_id=self.tid,selected=True);self.assertEqual(saved.json['shortlist'],[self.tid])
        self.post('/shortlist',tender_id=self.tid,selected=True);self.assertEqual(self.post('').json['shortlist'],[self.tid])
        self.data['mobile']='9876543211';other=self.signup()
        self.assertEqual(self.client.post('/api/accounts/tools',json={'session_token':other['session_token']}).json['shortlist'],[])
        self.assertEqual(self.post('/shortlist',tender_id='https://evil',selected=True).status_code,400)
        self.assertEqual(self.client.post('/api/accounts/tools',json={}).status_code,401)
    def test_saved_filters_validate_and_replace_by_name(self):
        self.post('/presets',name='Dewas',filters={'districtSearch':'Dewas','pacFilter':'above:2000000'})
        self.post('/presets',name='Dewas',filters={'districtSearch':'Indore'})
        self.assertEqual(self.post('').json['presets'],[{'name':'Dewas','filters':{'districtSearch':'Indore'}}])
        self.assertEqual(self.post('/presets',name='bad',filters={'session_token':'secret'}).status_code,400)
        self.post('/presets',name='Dewas',delete=True);self.assertEqual(self.post('').json['presets'],[])
    def test_report_is_private_and_review_is_admin_only(self):
        saved=self.post('/report',tender_id=self.tid,field='Fees',text='Portal shows fee 500 rupees');self.assertEqual(saved.status_code,200)
        self.assertNotIn('reports',self.post('').json)
        self.assertEqual(self.client.get('/api/admin/tools/review').status_code,401)
        with patch.object(server,'require_admin',return_value=('admin@example.test',None)):
            queue=self.poll('/api/admin/tools/review');self.assertEqual(queue.json['reports'][0]['tender_id'],self.tid)
            response=self.client.post('/api/admin/tools/report',json={'user_id':self.account['visitor_id'],'report_id':saved.json['report_id'],'status':'resolved'})
            self.assertEqual(response.status_code,200)
        record=server.account_service.get(user_id=self.account['visitor_id']);self.assertEqual(record['mistake_reports'][0]['status'],'resolved');self.assertEqual(record['admin_history'][-1]['action'],'mistake_report_resolved')
    def test_backup_is_encrypted_restore_preview_preserves_current_security(self):
        self.post('/shortlist',tender_id=self.tid,selected=True)
        with patch.object(server,'require_admin',return_value=('admin@example.test',None)):
            response=self.poll('/api/admin/tools/backup');encrypted=response.json['backup'];self.assertNotIn('9876543210',encrypted);self.assertNotIn('password_hash',encrypted)
            record=server.account_service.get(user_id=self.account['visitor_id']);original=record['password_hash'];record.update(blocked=True,mobile_verified=True);record['bidder_tools']['shortlist']=[];server.account_service.update(record,record['revision'])
            preview=self.client.post('/api/admin/tools/restore',json={'backup':encrypted});self.assertTrue(preview.json['preview']);self.assertEqual(server.account_service.get(user_id=self.account['visitor_id'])['bidder_tools']['shortlist'],[])
            restored=self.poll('/api/admin/tools/restore',{'backup':encrypted,'confirm':True});self.assertEqual(restored.json['restored'],1)
            self.assertEqual(self.client.post('/api/admin/tools/restore',json={'backup':encrypted[:-1]+'X'}).status_code,400)
        current=server.account_service.get(user_id=self.account['visitor_id']);self.assertEqual(current['password_hash'],original);self.assertTrue(current['blocked']);self.assertTrue(current['mobile_verified']);self.assertEqual(current['sessions'],[]);self.assertEqual(current['bidder_tools']['shortlist'],[self.tid]);self.assertFalse(current['bidder_tools']['telegram']['enabled'])
    def test_private_bot_link_requires_matching_private_chat_and_consumes_token(self):
        link=self.post('/telegram').json['url'];payload=link.split('start=')[1];self.assertLessEqual(len(payload),64)
        update={'message':{'chat':{'id':123,'type':'private'},'from':{'id':999},'text':'/start '+payload}}
        calls=[]
        def call(method,**params):
            calls.append((method,params));return [update] if method=='getUpdates' else {'message_id':17}
        with patch.object(tools,'public_json',return_value={'tenders':{}}):
            self.assertEqual(tools.process_alerts(server,call)['bound'],0)
            update['message']['from']['id']=123;update['message']['chat']['type']='channel';self.assertEqual(tools.process_alerts(server,call)['bound'],0)
            update['message']['chat']['type']='private';self.assertEqual(tools.process_alerts(server,call)['bound'],1);self.assertEqual(tools.process_alerts(server,call)['bound'],0)
        self.assertTrue(self.post('').json['telegram']['enabled']);self.assertEqual(sum(method=='sendMessage' for method,_ in calls),1)
        self.post('/telegram',disable=True);self.assertFalse(self.post('').json['telegram']['enabled'])
    def test_alert_receipt_prevents_repeat_and_uncertain_sends_are_held(self):
        record=server.account_service.get(user_id=self.account['visitor_id']);record['bidder_tools']={'shortlist':[self.tid],'telegram':{'chat_id':123,'enabled':True,'cursor':1799999990}};server.account_service.update(record,record['revision'])
        history={'tenders':{self.tid:[{'at':1799999999,'field':'शुल्क','before':'100','after':'500'}]}}
        calls=[]
        def call(method,**params):
            if method=='getUpdates':return []
            calls.append(params);return {'message_id':50}
        with patch.object(tools,'public_json',return_value=history):
            self.assertEqual(tools.process_alerts(server,call)['sent'],1);self.assertEqual(tools.process_alerts(server,call)['sent'],0)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['chat_id'],123)
        record=server.account_service.get(user_id=self.account['visitor_id']);record['bidder_tools']['telegram']['cursor']=1799999990;server.account_service.update(record,record['revision'])
        def failed(method,**params):
            if method=='getUpdates':return []
            raise TimeoutError('receipt unknown')
        with patch.object(tools,'public_json',return_value=history):
            self.assertEqual(tools.process_alerts(server,failed)['uncertain'],1);self.assertEqual(tools.process_alerts(server,call)['uncertain'],1)
        self.assertEqual(len(calls),1)
    def test_unsigned_worker_and_backup_cannot_access_records(self):
        with patch.object(tools,'all_records',side_effect=AssertionError('No reads')):
            self.assertEqual(self.client.post('/api/internal/bidder-alerts',json={}).status_code,401)
            self.assertEqual(self.client.get('/api/admin/tools/backup').status_code,401)
    def test_request_cache_does_not_outlive_request_or_cas_update(self):
        key=self.account['visitor_id'];accounts=server.account_service
        with patch.object(accounts,'operation',wraps=accounts.operation) as op:
            with server.app.test_request_context():
                first=accounts.get(user_id=key);accounts.get(user_id=key);self.assertEqual(op.call_count,1)
                accounts.update(first,first['revision']);accounts.get(user_id=key);self.assertEqual(op.call_count,3)
            with server.app.test_request_context():accounts.get(user_id=key)
            self.assertEqual(op.call_count,4)
