import copy
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import server
import signup_alerts
import welcome_backfill as batch


class Accounts:
    def __init__(self):
        self.rows = {str(i):{'user_id':str(i),'revision':1,'name':'User '+str(i),'mobile':'+91987654321'+str(i),
                            'signup_at':'2026-10-03T10:30:00+05:30','signup_alert':{'state':'pending'}} for i in (0,1)}
    def get(self, **fields):
        row = self.rows.get(fields.get('user_id')) if 'user_id' in fields else next((r for r in self.rows.values() if r['mobile']==fields.get('mobile')), None)
        return copy.deepcopy(row)
    def update(self, row, revision):
        if self.rows[row['user_id']]['revision'] != revision:return False
        row['revision']=revision+1;self.rows[row['user_id']]=copy.deepcopy(row);return True


class WelcomeBackfillTests(unittest.TestCase):
    def test_batch_is_scoped_and_sends_once_per_account_with_independent_receipts(self):
        accounts=Accounts();calls=[];progress=[]
        def sender(row):calls.append(row['user_id']);return 100+len(calls)
        mobiles=[r['mobile'] for r in accounts.rows.values()]
        result=batch.send_records(accounts,mobiles,lambda status:progress.append(dict(status)),sender=sender,pause=lambda _:None)
        self.assertEqual(result,{'total':2,'sent':2,'failed':0,'skipped':0})
        result=batch.send_records(accounts,mobiles,lambda _:None,sender=sender,pause=lambda _:None)
        self.assertEqual(result['sent'],2);self.assertEqual(calls,['0','1'])
        for row in accounts.rows.values():
            self.assertEqual(row['signup_alert']['state'],'pending')
            self.assertEqual(row[batch.OUTBOX]['state'],'sent')
            self.assertTrue(row[batch.OUTBOX]['message_id'])
        self.assertFalse(any('mobile' in status for status in progress))
    def test_later_signup_is_excluded_and_failed_recipient_can_resume(self):
        accounts=Accounts();accounts.rows['1']['signup_at']='2026-10-03T11:38:00+05:30'
        mobiles=[r['mobile'] for r in accounts.rows.values()]
        result=batch.send_records(accounts,mobiles,lambda _:None,sender=lambda _:100,pause=lambda _:None)
        self.assertEqual(result['sent'],1);self.assertEqual(result['skipped'],1)
        accounts.rows['1']['signup_at']='2026-10-03T11:30:00+05:30'
        def fail(row):raise RuntimeError('offline')
        result=batch.send_records(accounts,mobiles,lambda _:None,sender=fail,pause=lambda _:None)
        self.assertEqual(result['sent'],1);self.assertEqual(result['failed'],1)
        accounts.rows['1'][batch.OUTBOX]['retry_at']=0
        result=batch.send_records(accounts,mobiles,lambda _:None,sender=lambda _:101,pause=lambda _:None)
        self.assertEqual(result['sent'],2)
    def test_invalid_dates_are_not_included(self):
        for value in (None,'DD/MM/YYYY','invalid','2026-10-03T11:37:08+05:30'):
            self.assertFalse(batch.before_cutoff(value))
        self.assertTrue(batch.before_cutoff(batch.CUTOFF))
    def test_endpoint_requires_signature_and_exact_requested_campaign(self):
        client=server.app.test_client()
        self.assertEqual(client.post('/api/internal/welcome-backfill',json={}).status_code,401)
        secret='test-only-secret';stamp=str(int(time.time()))
        def call(payload):
            raw=json.dumps(payload,separators=(',',':')).encode()
            signature=hmac.new(secret.encode(),b'mp-welcome-backfill\n'+stamp.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
            return client.post('/api/internal/welcome-backfill',data=raw,content_type='application/json',headers={'X-Alert-Timestamp':stamp,'X-Alert-Signature':signature})
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':secret,'SIGNUP_ALERTS_ENABLED':'1'}),patch.object(signup_alerts,'private_destination',return_value=123),patch.object(batch,'start',return_value={'state':'complete','total':33,'sent':33,'failed':0,'skipped':0}) as start:
            self.assertEqual(call({'campaign':'other','cutoff':batch.CUTOFF}).status_code,400);start.assert_not_called()
            response=call({'campaign':batch.CAMPAIGN,'cutoff':batch.CUTOFF})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json['sent'],33)
            self.assertNotIn('mobile',response.get_data(as_text=True));self.assertNotIn('123',response.get_data(as_text=True))


if __name__=='__main__':unittest.main()
