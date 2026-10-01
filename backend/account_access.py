"""Password accounts: scrypt hashes, revocable sessions and manual reset links."""
import hashlib,hmac,json,os,re,secrets,time,uuid,threading,logging,unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from flask import jsonify,request
from werkzeug.security import generate_password_hash,check_password_hash
import google_sheet_store as sheets

logger=logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_detail_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='account-details')
_detail_slots=threading.BoundedSemaphore(16)
_detail_lock=threading.Lock()
_detail_jobs={}
_forgot_lock=threading.Lock()
_forgot_limits={}

def account_mobile(server,value):
    text=str(value or '').strip()
    return server.normalise_mobile(text) if re.fullmatch(r'(?:\+91)?[6-9][0-9]{9}',text) else ''

def queue_details(visitor_id,token):
    if not _detail_slots.acquire(blocking=False):return None
    def load():
        try:return sheets.call('session',visitor_id=visitor_id).get('affidavit_profile',{})
        finally:_detail_slots.release()
    try:future=_detail_pool.submit(load)
    except Exception:
        _detail_slots.release();raise
    key=secrets.token_urlsafe(24)
    with _detail_lock:
        for old,item in list(_detail_jobs.items()):
            if time.monotonic()-item[2]>300:del _detail_jobs[old]
        _detail_jobs[key]=(hashlib.sha256(token.encode()).hexdigest(),future,time.monotonic())
    return key

PASSWORD_METHOD='scrypt:32768:8:3'
_DUMMY_HASH=generate_password_hash('no-account-'+secrets.token_hex(24),method=PASSWORD_METHOD)
SESSION_SECONDS=30*24*3600
IDLE_SECONDS=15*60

def name_fields(server,p):
    first=server.clean(p.get('first_name',p.get('name')))
    middle=server.clean(p.get('middle_name'));last=server.clean(p.get('last_name'))
    if not last:raise AccountError('Surname / उपनाम भरना जरूरी है।')
    if any(unicodedata.category(char)[0] not in ('L','M') and char not in " .'’-" for char in middle):
        raise AccountError('Middle Name में केवल नाम लिखें, अंक या Mobile Number नहीं।')
    if not 2<=len(first)<=15 or len(middle)>10 or len(last)>15:
        raise AccountError('Name अधिकतम 15, Middle Name 10 और Surname 15 characters का रखें।')
    return dict(first_name=first,middle_name=middle,last_name=last,name=' '.join(x for x in (first,middle,last) if x))

def profile_fields(server,p):
    names=name_fields(server,p)
    district=server.clean(p.get('district'));tehsil=server.clean(p.get('tehsil'))
    directory=json.loads((server.ROOT/'data/mp_tehsils.json').read_text())['districts']
    if district not in directory or tehsil not in directory[district]:
        raise AccountError('अपने District की सूची से Tehsil चुनें।')
    return {**names,'district':district,'tehsil':tehsil}

def profile_corrections(record):
    corrections=[]
    if not str(record.get('last_name') or '').strip():corrections.append('last_name')
    middle=str(record.get('middle_name') or '').strip()
    if any(unicodedata.category(char)[0] not in ('L','M') and char not in " .'’-" for char in middle):corrections.append('middle_name')
    return corrections

def public_profile(record):
    return {key:record.get(key,'') for key in ('name','first_name','middle_name','last_name','mobile','district','tehsil')}

class AccountError(RuntimeError):
    def __init__(self,message,status=400,code=None):super().__init__(message);self.status=status;self.code=code

class Accounts:
    def __init__(self,server):self.server=server
    def operation(self,action,**data):
        if sheets.enabled():
            started=time.monotonic()
            try:return sheets.call('account_'+action,**data)
            finally:logger.info('Account storage action=%s elapsed=%.2fs',action,time.monotonic()-started)
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
    def rate_checks(self,mobile=''):
        # Capture Flask request information before starting remote requests.
        keys=[('ip:'+str(request.remote_addr),120)]
        if mobile:keys.append(('mobile:'+mobile,12))
        secret=self.server.admin_session_secret()
        if not secret:raise AccountError('Account service is not configured.',503)
        return [{'key':hmac.new(secret.encode(),value.encode(),hashlib.sha256).hexdigest(),'limit':limit} for value,limit in keys]
    def check_rate_results(self,results):
        if any(not result['allowed'] for result in results):
            raise AccountError('कई प्रयास हुए हैं। 15 मिनट बाद फिर प्रयास करें।',429)
    def limit(self,mobile=''):
        self.check_rate_results([self.operation('rate',**data) for data in self.rate_checks(mobile)])
    def limit_forgot(self,mobile):
        # This read-only existence check needs one Sheet lookup, not remote
        # rate-limit writes. Keep phone/IP throttling locally on the API worker.
        checks=self.rate_checks(mobile);window=int(time.time())//900
        with _forgot_lock:
            for key,item in list(_forgot_limits.items()):
                if item[0]!=window:del _forgot_limits[key]
            if len(_forgot_limits)+len(checks)>10000:raise AccountError('कई प्रयास हुए हैं। थोड़ी देर बाद प्रयास करें।',429)
            results=[]
            for data in checks:
                count=_forgot_limits.get(data['key'],(window,0))[1]+1
                _forgot_limits[data['key']]=(window,count)
                results.append({'allowed':count<=data['limit']})
        self.check_rate_results(results)
    def lookup_limited(self,mobile):
        if not sheets.enabled() or not mobile:
            self.limit(mobile)
            return self.get(mobile=mobile) if mobile else None
        # Independent remote calls overlap their network/startup time. The Sheet
        # still serializes mutations; every rate check must pass before login.
        checks=self.rate_checks(mobile)
        with ThreadPoolExecutor(max_workers=3,thread_name_prefix='account-check') as pool:
            limits=[pool.submit(self.operation,'rate',**data) for data in checks]
            lookup=pool.submit(self.get,mobile=mobile)
            self.check_rate_results([future.result() for future in limits])
            return lookup.result()
    def password(self,value):
        if (not isinstance(value,str) or not 8<=len(value)<=128 or
                not all(re.search(pattern,value) for pattern in (r'[A-Z]',r'[a-z]',r'[0-9]',r'[^A-Za-z0-9\s]'))):
            raise AccountError('Password 8 से 128 characters का रखें: एक capital, एक small letter, एक number और एक special character ज़रूरी है।')
        return generate_password_hash(value,method=PASSWORD_METHOD)
    def issue(self,record):
        raw='acct_'+record['user_id']+'.'+secrets.token_urlsafe(32)
        now=int(time.time());sessions=[]
        sessions.append({'hash':self.digest(raw),'expires':now+SESSION_SECONDS,'last_active':now})
        record['sessions']=sessions;return raw
    def authenticate(self,token):
        if not isinstance(token,str) or not token.startswith('acct_') or len(token)>160:raise AccountError('Session समाप्त है। Sign in करें।',401)
        try:user_id=str(uuid.UUID(token[5:].split('.',1)[0]))
        except ValueError:raise AccountError('Session invalid.',401) from None
        record=self.get(user_id=user_id)
        if record and record.get('blocked'):raise AccountError('आपका account Admin द्वारा block किया गया है।',401,'account_blocked')
        digest=self.digest(token);now=int(time.time())
        if record and not any(hmac.compare_digest(s['hash'],digest) for s in record.get('sessions',[])):
            raise AccountError('यह login समाप्त हो गया है। दूसरे device पर login होने पर पुराना session बंद हो जाता है। फिर Sign in करें।',401,'session_replaced')
        if not record or not any(hmac.compare_digest(s['hash'],digest) and s['expires']>now and now-s.get('last_active',s['expires']-SESSION_SECONDS)<IDLE_SECONDS for s in record.get('sessions',[])):raise AccountError('15 मिनट inactivity के बाद session समाप्त है। Sign in करें।',401)
        return record
    def response(self,record,token):
        details_id=None
        if sheets.enabled():
            aff={}
            details_id=queue_details(record['user_id'],token)
        else:
            conn=self.server.visitor_db()
            try:
                row=conn.execute('SELECT profile_json FROM visitor_affidavit_profiles WHERE visitor_id=?',(record['user_id'],)).fetchone()
                aff=json.loads(row['profile_json']) if row else {}
                now=self.server.now_ist().isoformat()
                conn.execute('UPDATE visitor_registrations SET last_visit_at=?,visit_count=visit_count+1 WHERE visitor_id=?',(now,record['user_id']))
                conn.execute('INSERT INTO visitor_events (event_id,visitor_id,visited_at) VALUES (?,?,?)',(str(uuid.uuid4()),record['user_id'],now));conn.commit()
            finally:conn.close()
        response=jsonify({'ok':True,'session_token':token,'visitor_id':record['user_id'],'profile':public_profile(record),'profile_corrections':profile_corrections(record),'affidavit_profile':aff,'details_id':details_id,'storage':'google_sheets' if sheets.enabled() else self.server.user_db_backend()})
        response.headers['Cache-Control']='no-store';return response

def install(server):
    accounts=Accounts(server);app=server.app
    @app.errorhandler(AccountError)
    def account_error(error):return jsonify({'ok':False,'message':str(error),'code':error.code}),error.status
    @app.get('/api/accounts/ready')
    def ready():
        response=jsonify({'ok':True});response.headers['Cache-Control']='no-store';return response
    @app.post('/api/accounts/signup')
    def signup():
        p=request.get_json(silent=True) or {};mobile=account_mobile(server,p.get('mobile'))
        record=accounts.lookup_limited(mobile)
        profile=profile_fields(server,p)
        if not mobile:raise AccountError('सही Mobile भरें।')
        if record:raise AccountError('आप पहले से Sign up हैं। Sign in में password भरें।',409,'account_exists')
        if p.get('password')!=p.get('confirm_password'):raise AccountError('दोनों passwords एक समान रखें।')
        record={'user_id':str(uuid.uuid4()),'mobile':mobile,**profile,'password_hash':accounts.password(p.get('password')),'sessions':[],'reset':None,'signup_at':server.now_ist().isoformat()}
        token=accounts.issue(record);record=accounts.operation('create',record=record)['record']
        return accounts.response(record,token)
    @app.post('/api/accounts/signin')
    def signin():
        p=request.get_json(silent=True) or {};mobile=account_mobile(server,p.get('mobile'))
        record=accounts.lookup_limited(mobile);password=p.get('password')
        valid= isinstance(password,str) and len(password)<=128 and check_password_hash(record['password_hash'] if record else _DUMMY_HASH,password)
        if not mobile:raise AccountError('सही Mobile Number भरें।')
        if not record:raise AccountError('इस mobile से Sign up नहीं हुआ है। कृपया Sign up form पूरा करें।',404,'signup_required')
        if not valid:raise AccountError('Mobile या password सही नहीं है।',401)
        if record.get('blocked'):raise AccountError('आपका account Admin द्वारा block किया गया है।',401,'account_blocked')
        original_hash=record['password_hash']
        for _ in range(3):
            revision=record['revision'];token=accounts.issue(record)
            if accounts.update(record,revision):return accounts.response(record,token)
            record=accounts.get(user_id=record['user_id'])
            if record.get('blocked'):raise AccountError('Account blocked.',401,'account_blocked')
            if record['password_hash']!=original_hash:raise AccountError('Password बदल गया है। फिर Sign in करें।',401)
        raise AccountError('फिर प्रयास करें।',409)
    @app.post('/api/accounts/forgot-check')
    def forgot_check():
        mobile=account_mobile(server,(request.get_json(silent=True) or {}).get('mobile'))
        accounts.limit_forgot(mobile)
        if not mobile:raise AccountError('सही Mobile Number भरें।')
        record=accounts.get(mobile=mobile)
        if not record:raise AccountError('पहले Sign up करें। इसके बाद password reset request भेज सकेंगे।',404,'signup_required')
        response=jsonify({'ok':True});response.headers['Cache-Control']='no-store';return response
    @app.post('/api/accounts/session')
    def session():
        token=(request.get_json(silent=True) or {}).get('session_token');record=accounts.authenticate(token)
        return accounts.response(record,token)
    @app.post('/api/accounts/session-check')
    def session_check():
        accounts.authenticate((request.get_json(silent=True) or {}).get('session_token'))
        response=jsonify({'ok':True});response.headers['Cache-Control']='no-store';return response
    @app.post('/api/accounts/details')
    def details():
        p=request.get_json(silent=True) or {};token=p.get('session_token')
        accounts.authenticate(token)
        with _detail_lock:item=_detail_jobs.get(p.get('details_id'))
        if not item or time.monotonic()-item[2]>300 or not hmac.compare_digest(item[0],accounts.digest(token)):
            raise AccountError('Profile details request expired.',404)
        if not item[1].done():
            response=jsonify({'ok':True,'pending':True});response.headers['Cache-Control']='no-store';return response
        aff=item[1].result()
        response=jsonify({'ok':True,'affidavit_profile':aff});response.headers['Cache-Control']='no-store';return response
    @app.post('/api/accounts/activity')
    def activity():
        token=(request.get_json(silent=True) or {}).get('session_token')
        for _ in range(3):
            record=accounts.authenticate(token);revision=record['revision']
            for session in record['sessions']:
                if hmac.compare_digest(session['hash'],accounts.digest(token)):session['last_active']=int(time.time())
            if accounts.update(record,revision):return jsonify({'ok':True})
        raise AccountError('Session update फिर प्रयास करें।',409)
    @app.post('/api/accounts/profile')
    def edit_profile():
        p=request.get_json(silent=True) or {}
        accounts.authenticate(p.get('session_token'))
        profile=profile_fields(server,p);name=profile['name'];district=profile['district']
        for _ in range(3):
            record=accounts.authenticate(p.get('session_token'));revision=record['revision'];record.update(profile)
            if accounts.update(record,revision):
                if not sheets.enabled():
                    conn=server.visitor_db()
                    try:conn.execute('UPDATE visitor_registrations SET name=?,district=? WHERE visitor_id=?',(name,district,record['user_id']));conn.commit()
                    finally:conn.close()
                return jsonify({'ok':True,'profile':public_profile(record)})
        raise AccountError('Profile save फिर प्रयास करें।',409)
    @app.post('/api/accounts/profile-names')
    def edit_profile_names():
        p=request.get_json(silent=True) or {};token=p.get('session_token')
        accounts.authenticate(token);names=name_fields(server,p)
        for _ in range(3):
            record=accounts.authenticate(token);revision=record['revision'];record.update(names)
            if accounts.update(record,revision):
                if not sheets.enabled():
                    conn=server.visitor_db()
                    try:conn.execute('UPDATE visitor_registrations SET name=? WHERE visitor_id=?',(record['name'],record['user_id']));conn.commit()
                    finally:conn.close()
                return jsonify({'ok':True,'profile':public_profile(record),'profile_corrections':profile_corrections(record)})
        raise AccountError('Profile save फिर प्रयास करें।',409)
    @app.post('/api/accounts/change-password')
    def change_password():
        p=request.get_json(silent=True) or {};token=p.get('session_token');record=accounts.authenticate(token);accounts.limit(record['mobile'])
        current=p.get('current_password')
        if not isinstance(current,str) or len(current)>128 or not check_password_hash(record['password_hash'],current):raise AccountError('Current password सही नहीं है।',401,'wrong_password')
        if p.get('password')!=p.get('confirm_password'):raise AccountError('दोनों passwords एक समान रखें।')
        new_hash=accounts.password(p.get('password'));original_hash=record['password_hash']
        for _ in range(3):
            record=accounts.authenticate(token)
            if record['password_hash']!=original_hash:raise AccountError('Password बदल चुका है। फिर Sign in करें।',401)
            revision=record['revision'];record.update(password_hash=new_hash,sessions=[],reset=None)
            if accounts.update(record,revision):return jsonify({'ok':True,'message':'Password बदल गया। नए password से Sign in करें।'})
        raise AccountError('Password save फिर प्रयास करें।',409)
    @app.post('/api/accounts/logout')
    def logout():
        token=(request.get_json(silent=True) or {}).get('session_token');record=accounts.authenticate(token)
        for _ in range(3):
            revision=record['revision'];record['sessions']=[s for s in record['sessions'] if not hmac.compare_digest(s['hash'],accounts.digest(token))]
            if accounts.update(record,revision):return jsonify({'ok':True})
            record=accounts.get(user_id=record['user_id'])
        raise AccountError('Logout save नहीं हुआ। फिर प्रयास करें।',409)
    @app.post('/api/admin/accounts/access')
    def admin_account_access():
        email,_=server.require_admin()
        if not email:raise AccountError('Google Admin login required.',401)
        p=request.get_json(silent=True) or {};mobile=account_mobile(server,p.get('mobile'))
        record=accounts.get(mobile=mobile) if mobile else None
        if not record:raise AccountError('Registered account नहीं मिला।',404)
        if 'blocked' in p:
            if type(p['blocked']) is not bool:raise AccountError('Invalid block status.')
            for _ in range(3):
                revision=record['revision'];record.update(blocked=p['blocked'],access_updated_by=email,access_updated_at=server.now_ist().isoformat())
                if p['blocked']:record.update(sessions=[],reset=None)
                if accounts.update(record,revision):break
                record=accounts.get(user_id=record['user_id'])
            else:raise AccountError('Status save फिर प्रयास करें।',409)
        response=jsonify({'ok':True,'profile':public_profile(record),'blocked':bool(record.get('blocked'))})
        response.headers['Cache-Control']='no-store';return response

    @app.post('/api/admin/accounts/reset-link')
    def reset_link():
        email,_=server.require_admin()
        if not email:raise AccountError('Google Admin login required.',401)
        p=request.get_json(silent=True) or {}
        if p.get('identity_verified') is not True:raise AccountError('Registered WhatsApp number और account ownership verify करें।')
        mobile=account_mobile(server,p.get('mobile'));record=accounts.get(mobile=mobile) if mobile else None
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
