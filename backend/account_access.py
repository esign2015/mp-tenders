"""Password accounts: scrypt hashes, revocable sessions and manual reset links."""
import hashlib,hmac,json,os,re,secrets,time,uuid
from datetime import timedelta
from flask import jsonify,request
from werkzeug.security import generate_password_hash,check_password_hash
import google_sheet_store as sheets

PASSWORD_METHOD='scrypt:32768:8:3'
_DUMMY_HASH=generate_password_hash('no-account-'+secrets.token_hex(24),method=PASSWORD_METHOD)
SESSION_SECONDS=30*24*3600

class AccountError(RuntimeError):
    def __init__(self,message,status=400,code=None):super().__init__(message);self.status=status;self.code=code

class Accounts:
    def __init__(self,server):self.server=server
    def operation(self,action,**data):
        if sheets.enabled():return sheets.call('account_'+action,**data)
        if not self.server.DATABASE_URL and os.getenv('ALLOW_EPHEMERAL_ACCOUNTS','0')!='1':
            raise AccountError('Account storage अभी connect नहीं है। कृपया थोड़ी देर बाद प्रयास करें।',503)
        conn=self.server.visitor_db()
        try:
            conn.execute('CREATE TABLE IF NOT EXISTS password_accounts (user_id TEXT PRIMARY KEY,mobile TEXT NOT NULL UNIQUE,revision INTEGER NOT NULL,record_json TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS account_rate_limits (rate_key TEXT PRIMARY KEY,window_start INTEGER NOT NULL,attempts INTEGER NOT NULL)')
            if action in ('lookup','get'):
                field='mobile' if action=='lookup' else 'user_id'
                row=conn.execute('SELECT record_json FROM password_accounts WHERE '+field+'=?',(data[field],)).fetchone()
                return {'record':json.loads(row['record_json']) if row else None}
            if action=='create':
                record=data['record'];record['revision']=1
                inserted=conn.execute('INSERT INTO password_accounts (user_id,mobile,revision,record_json) VALUES (?,?,1,?) ON CONFLICT(mobile) DO NOTHING',(record['user_id'],record['mobile'],json.dumps(record)))
                if inserted.cursor.rowcount!=1:raise AccountError('यह mobile registered है। Sign in या Forgot password चुनें।',409)
                now=self.server.now_ist().isoformat()
                conn.execute('INSERT INTO visitor_registrations (visitor_id,name,mobile,district,signup_at,last_visit_at,visit_count) VALUES (?,?,?,?,?,?,1)',(record['user_id'],record['name'],record['mobile'],record['district'],now,now))
                conn.commit();return {'record':record}
            if action=='update':
                record=data['record'];expected=data['expected_revision'];record['revision']=expected+1
                result=conn.execute('UPDATE password_accounts SET revision=?,record_json=? WHERE user_id=? AND revision=?',(expected+1,json.dumps(record),record['user_id'],expected))
                conn.commit();return {'updated':result.cursor.rowcount==1}
            if action=='rate':
                # Atomic per-key increment; neither unknown mobiles nor new IPs bypass a phone/IP window.
                now=int(time.time());start=now//900*900
                conn.execute('INSERT INTO account_rate_limits (rate_key,window_start,attempts) VALUES (?,?,1) ON CONFLICT(rate_key) DO UPDATE SET attempts=CASE WHEN account_rate_limits.window_start=excluded.window_start THEN account_rate_limits.attempts+1 ELSE 1 END,window_start=excluded.window_start',(data['key'],start))
                row=conn.execute('SELECT attempts FROM account_rate_limits WHERE rate_key=?',(data['key'],)).fetchone();conn.commit()
                return {'allowed':row['attempts']<=data['limit']}
            raise AccountError('Unknown account operation.')
        finally:conn.close()
    def get(self,**kwargs):return self.operation('lookup' if 'mobile' in kwargs else 'get',**kwargs)['record']
    def update(self,record,revision):return self.operation('update',record=record,expected_revision=revision)['updated']
    def digest(self,value):return hashlib.sha256(value.encode()).hexdigest()
    def limit(self,mobile=''):
        # Render's immediate peer is used as a conservative IP limit; untrusted forwarding headers cannot bypass it.
        keys=[('ip:'+str(request.remote_addr),120)]
        if mobile:keys.append(('mobile:'+mobile,12))
        secret=self.server.admin_session_secret()
        if not secret:raise AccountError('Account service is not configured.',503)
        for value,limit in keys:
            key=hmac.new(secret.encode(),value.encode(),hashlib.sha256).hexdigest()
            if not self.operation('rate',key=key,limit=limit)['allowed']:raise AccountError('कई प्रयास हुए हैं। 15 मिनट बाद फिर प्रयास करें।',429)
    def password(self,value):
        if (not isinstance(value,str) or not 8<=len(value)<=128 or
                not all(re.search(pattern,value) for pattern in (r'[A-Z]',r'[a-z]',r'[0-9]',r'[^A-Za-z0-9\s]'))):
            raise AccountError('Password 8 से 128 characters का रखें: एक capital, एक small letter, एक number और एक special character ज़रूरी है।')
        return generate_password_hash(value,method=PASSWORD_METHOD)
    def issue(self,record):
        raw='acct_'+record['user_id']+'.'+secrets.token_urlsafe(32)
        now=int(time.time());sessions=[s for s in record.get('sessions',[]) if s['expires']>now][-9:]
        sessions.append({'hash':self.digest(raw),'expires':now+SESSION_SECONDS})
        record['sessions']=sessions;return raw
    def authenticate(self,token):
        if not isinstance(token,str) or not token.startswith('acct_') or len(token)>160:raise AccountError('Session समाप्त है। Sign in करें।',401)
        try:user_id=str(uuid.UUID(token[5:].split('.',1)[0]))
        except ValueError:raise AccountError('Session invalid.',401) from None
        record=self.get(user_id=user_id)
        digest=self.digest(token);now=int(time.time())
        if not record or not any(hmac.compare_digest(s['hash'],digest) and s['expires']>now for s in record.get('sessions',[])):raise AccountError('Session समाप्त है। Sign in करें।',401)
        return record
    def response(self,record,token):
        if sheets.enabled():aff=sheets.call('session',visitor_id=record['user_id'])['affidavit_profile']
        else:
            conn=self.server.visitor_db()
            try:
                row=conn.execute('SELECT profile_json FROM visitor_affidavit_profiles WHERE visitor_id=?',(record['user_id'],)).fetchone()
                aff=json.loads(row['profile_json']) if row else {}
                now=self.server.now_ist().isoformat()
                conn.execute('UPDATE visitor_registrations SET last_visit_at=?,visit_count=visit_count+1 WHERE visitor_id=?',(now,record['user_id']))
                conn.execute('INSERT INTO visitor_events (event_id,visitor_id,visited_at) VALUES (?,?,?)',(str(uuid.uuid4()),record['user_id'],now));conn.commit()
            finally:conn.close()
        response=jsonify({'ok':True,'session_token':token,'visitor_id':record['user_id'],'profile':{key:record[key] for key in ('name','mobile','district')},'affidavit_profile':aff,'storage':'google_sheets' if sheets.enabled() else self.server.user_db_backend()})
        response.headers['Cache-Control']='no-store';return response

def install(server):
    accounts=Accounts(server);app=server.app
    @app.errorhandler(AccountError)
    def account_error(error):return jsonify({'ok':False,'message':str(error),'code':error.code}),error.status
    @app.post('/api/accounts/signup')
    def signup():
        p=request.get_json(silent=True) or {};mobile=server.normalise_mobile(p.get('mobile'))
        accounts.limit(mobile)
        name=server.clean(p.get('name'));district=server.clean(p.get('district'))
        if not mobile or not 2<=len(name)<=120 or not 2<=len(district)<=100:raise AccountError('सही Name, Mobile और District भरें।')
        if accounts.get(mobile=mobile):raise AccountError('आप पहले से Sign up हैं। Sign in में password भरें।',409,'account_exists')
        if p.get('password')!=p.get('confirm_password'):raise AccountError('दोनों passwords एक समान रखें।')
        record={'user_id':str(uuid.uuid4()),'mobile':mobile,'name':name,'district':district,'password_hash':accounts.password(p.get('password')),'sessions':[],'reset':None,'signup_at':server.now_ist().isoformat()}
        token=accounts.issue(record);record=accounts.operation('create',record=record)['record']
        return accounts.response(record,token)
    @app.post('/api/accounts/signin')
    def signin():
        p=request.get_json(silent=True) or {};mobile=server.normalise_mobile(p.get('mobile'));accounts.limit(mobile)
        password=p.get('password');record=accounts.get(mobile=mobile) if mobile else None
        valid= isinstance(password,str) and len(password)<=128 and check_password_hash(record['password_hash'] if record else _DUMMY_HASH,password)
        if not mobile:raise AccountError('सही Mobile Number भरें।')
        if not record:raise AccountError('इस mobile से Sign up नहीं हुआ है। कृपया Sign up form पूरा करें।',404,'signup_required')
        if not valid:raise AccountError('Mobile या password सही नहीं है।',401)
        original_hash=record['password_hash']
        for _ in range(3):
            revision=record['revision'];token=accounts.issue(record)
            if accounts.update(record,revision):return accounts.response(record,token)
            record=accounts.get(user_id=record['user_id'])
            if record['password_hash']!=original_hash:raise AccountError('Password बदल गया है। फिर Sign in करें।',401)
        raise AccountError('फिर प्रयास करें।',409)
    @app.post('/api/accounts/forgot-check')
    def forgot_check():
        mobile=server.normalise_mobile((request.get_json(silent=True) or {}).get('mobile'));accounts.limit(mobile)
        if not mobile:raise AccountError('सही Mobile Number भरें।')
        if not accounts.get(mobile=mobile):raise AccountError('पहले Sign up करें। इसके बाद password reset request भेज सकेंगे।',404,'signup_required')
        response=jsonify({'ok':True});response.headers['Cache-Control']='no-store';return response
    @app.post('/api/accounts/session')
    def session():
        token=(request.get_json(silent=True) or {}).get('session_token');record=accounts.authenticate(token)
        return accounts.response(record,token)
    @app.post('/api/accounts/logout')
    def logout():
        token=(request.get_json(silent=True) or {}).get('session_token');record=accounts.authenticate(token)
        for _ in range(3):
            revision=record['revision'];record['sessions']=[s for s in record['sessions'] if not hmac.compare_digest(s['hash'],accounts.digest(token))]
            if accounts.update(record,revision):return jsonify({'ok':True})
            record=accounts.get(user_id=record['user_id'])
        raise AccountError('Logout save नहीं हुआ। फिर प्रयास करें।',409)
    @app.post('/api/admin/accounts/reset-link')
    def reset_link():
        email,_=server.require_admin()
        if not email:raise AccountError('Google Admin login required.',401)
        p=request.get_json(silent=True) or {}
        if p.get('identity_verified') is not True:raise AccountError('Registered WhatsApp number और account ownership verify करें।')
        mobile=server.normalise_mobile(p.get('mobile'));record=accounts.get(mobile=mobile) if mobile else None
        if not record:raise AccountError('Registered account नहीं मिला।',404)
        raw=record['user_id']+'.'+secrets.token_urlsafe(32)
        for _ in range(3):
            revision=record['revision'];record['reset']={'hash':accounts.digest(raw),'expires':int(time.time())+900,'issued_by':email}
            if accounts.update(record,revision):
                response=jsonify({'ok':True,'reset_url':'https://tenders.codinglms.xyz/#reset='+raw,'expires_minutes':15});response.headers['Cache-Control']='no-store';return response
            record=accounts.get(user_id=record['user_id'])
        raise AccountError('फिर प्रयास करें।',409)
    @app.post('/api/accounts/reset-password')
    def reset_password():
        p=request.get_json(silent=True) or {};accounts.limit()
        raw=p.get('reset_token')
        if not isinstance(raw,str) or len(raw)>160:raise AccountError('Reset link invalid या expired है।',400)
        try:user_id=str(uuid.UUID(raw.split('.',1)[0]))
        except ValueError:raise AccountError('Reset link invalid या expired है।') from None
        new_hash=None
        for _ in range(3):
            record=accounts.get(user_id=user_id);reset=(record or {}).get('reset')
            if not reset or reset['expires']<=int(time.time()) or not hmac.compare_digest(reset['hash'],accounts.digest(raw)):raise AccountError('Reset link invalid, used या expired है।')
            if p.get('password')!=p.get('confirm_password'):raise AccountError('दोनों passwords एक समान रखें।')
            new_hash=new_hash or accounts.password(p.get('password'));revision=record['revision']
            record.update(password_hash=new_hash,sessions=[],reset=None)
            if accounts.update(record,revision):return jsonify({'ok':True,'message':'Password बदल गया। नए password से Sign in करें।'})
        raise AccountError('फिर प्रयास करें।',409)
    return accounts
