import sys,tempfile,uuid,unittest
from pathlib import Path
from unittest.mock import patch
from io import BytesIO
from openpyxl import load_workbook
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import server

class VisitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.database=patch.object(server,'DATABASE_URL','');self.database.start()
        self.path=patch.object(server,'USER_DB_PATH',Path(self.tmp.name)/'users.db');self.path.start()
        self.secret=patch.dict('os.environ',{'ADMIN_SESSION_SECRET':'visitor-test-secret','GOOGLE_SHEETS_WEBAPP_URL':'','GOOGLE_SHEETS_SHARED_SECRET':'','ALLOW_EPHEMERAL_ACCOUNTS':'1'});self.secret.start()
        self.clock=patch('account_access.time.time',return_value=1800000000);self.clock.start();self.number=0
        self.client=server.app.test_client()
    def tearDown(self):
        self.clock.stop();self.secret.stop();self.path.stop();self.database.stop();self.tmp.cleanup()
    def payload(self):
        self.number+=1
        return {'first_name':'Visitor','last_name':'Test','mobile':str(9876543210+self.number),'district':'Dewas','tehsil':'Kannod','password':'Private1!','confirm_password':'Private1!'}
    def test_removed_login_routes_are_404_and_legacy_rows_survive(self):
        conn=server.visitor_db();key=str(uuid.uuid4())
        conn.execute('INSERT INTO visitor_registrations (visitor_id,name,mobile,district,signup_at,last_visit_at,visit_count) VALUES (?,?,?,?,?,?,?)',(key,'=1+1','+919876543210','Dewas','2026-01-01','2026-01-01',1));conn.commit();conn.close()
        for path in ('/api/visitors/register','/api/visitors/session','/api/telegram/auth','/api/telegram/session'):
            self.assertEqual(self.client.post(path,json={}).status_code,404)
        workbook=load_workbook(BytesIO(server.build_user_excel()))
        self.assertEqual(workbook['Visitor Registrations']['B2'].value,'=1+1');self.assertEqual(workbook['Visitor Registrations']['B2'].data_type,'s')

    def test_document_profile_requires_email_and_validates_registration_dates(self):
        token=self.client.post('/api/accounts/signup',json=self.payload()).json['session_token']
        profile={'bidderName':'Bidder','firmName':'Firm','status':'Proprietor','place':'Dewas','relative':'no','email':'bidder@example.test'}
        for change in ({'email':''},{'email':'invalid'},{'representativeEmail':'bad address'},{'registrationDate':'2026-02-30'},{'pincode':'12345'},{'pincode':'000000'}):
            response=self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':{**profile,**change}})
            self.assertEqual(response.status_code,400)
        profile.update(pincode='455332',district='Dewas',tehsil='Kannod',pan='ABCDE1234F',gst='23ABCDE1234F1Z5',registrationNumber='REG-42',registrationDate='2025-01-01',registrationValidTill='2030-01-01',representativeName='Different Person',representativeEmail='rep@example.test')
        self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':profile}).status_code,200)
        restored=self.client.post('/api/visitors/affidavit',json={'session_token':token}).json['profile']
        for key in profile:self.assertEqual(restored[key],profile[key])

    def test_structured_address_and_gst_pan_validation(self):
        token=self.client.post('/api/accounts/signup',json=self.payload()).json['session_token']
        profile={'bidderName':'Bidder','firmName':'Firm','status':'Proprietor','place':'Kannod','relative':'no','email':'bidder@example.test','houseNumber':'12','roadStreet':'Main Road','locality':'Pipla','landmark':'Near Temple','gst':'23abcde1234f1z5','pan':''}
        saved=self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':profile})
        self.assertEqual(saved.status_code,200)
        self.assertEqual(saved.json['profile']['address'],'12, Main Road, Pipla, Near Temple')
        self.assertEqual(saved.json['profile']['gst'],'23ABCDE1234F1Z5')
        self.assertEqual(saved.json['profile']['pan'],'ABCDE1234F')
        restored=self.client.post('/api/visitors/affidavit',json={'session_token':token}).json['profile']
        for key in ('houseNumber','roadStreet','locality','landmark'):self.assertEqual(restored[key],profile[key])
        for change in ({'gst':'23ABCDE1234F1Z'},{'gst':'AAABCDE1234F1Z5'},{'gst':'231BCDE1234F1Z5'},{'pan':'ABCDE12345'},{'pan':'ABCD11234F'},{'houseNumber':'','roadStreet':'','locality':'','landmark':''}):
            self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':{**profile,**change}}).status_code,400)
        # Valid exceptions can keep a different PAN; old cached clients may still edit the full address.
        self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':{**profile,'pan':'PQRST6789Z'}}).status_code,200)
        old_client={k:v for k,v in profile.items() if k not in ('houseNumber','roadStreet','locality','landmark')}
        changed=self.client.post('/api/visitors/affidavit',json={'session_token':token,'profile':{**old_client,'address':'Updated legacy address'}})
        self.assertEqual(changed.status_code,200);self.assertEqual(changed.json['profile']['roadStreet'],'Updated legacy address')
        self.assertEqual(changed.json['profile']['houseNumber'],'')

    def test_affidavit_profile_restores_only_in_its_signed_session(self):
        first=self.client.post('/api/accounts/signup',json=self.payload()).json
        second=self.client.post('/api/accounts/signup',json=self.payload()).json
        profile={'bidderName':'Bidder','email':'bidder@example.test','firmName':'Firm','status':'Proprietor','place':'Dewas','relative':'no'}
        saved=self.client.post('/api/visitors/affidavit',json={'session_token':first['session_token'],'profile':profile})
        self.assertEqual(saved.status_code,200)
        restored=self.client.post('/api/accounts/session',json={'session_token':first['session_token']})
        self.assertEqual(restored.json['affidavit_profile']['firmName'],'Firm')
        isolated=self.client.post('/api/visitors/affidavit',json={'session_token':second['session_token']})
        self.assertEqual(isolated.json['profile'],{})
        self.assertEqual(self.client.post('/api/visitors/affidavit',json={'session_token':first['session_token']+'x'}).status_code,401)

if __name__=='__main__':unittest.main()
