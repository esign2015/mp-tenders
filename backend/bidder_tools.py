"""Account-owned tender tools; contacts, backups and reports never enter Git."""
import base64,copy,hashlib,hmac,json,os,re,secrets,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet,InvalidToken
from flask import jsonify,request
import requests
import google_sheet_store as sheets
from account_access import AccountError,account_mobile

TENDER=re.compile(r'^\d{4}_[A-Za-z0-9-]+_\d+_\d+$')
FILTERS=('generalSearch','organisationSearch','districtSearch','closingExpire','pacFilter','statusSelect','changeFilter','sortSelect')
PUBLIC_ROOT='https://raw.githubusercontent.com/esign2015/mp-tenders/tender-data/data/'
_cache={};_cache_lock=threading.Lock();_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='private-tools');_jobs={};_jobs_lock=threading.Lock();_alert_index=set();_alert_refreshed=0;_alert_current=None

def public_json(name):
    with _cache_lock:cached=_cache.get(name)
    if cached and time.monotonic()-cached[0]<60:return cached[1]
    response=requests.get(PUBLIC_ROOT+name,timeout=20);response.raise_for_status();data=response.json()
    with _cache_lock:_cache[name]=(time.monotonic(),data)
    return data

def tools_state(record):
    saved=record.get('bidder_tools') or {};alert=saved.get('telegram') or {}
    return {'shortlist':saved.get('shortlist',[]),'presets':saved.get('presets',[]),'telegram':{'connected':bool(alert.get('chat_id')),'enabled':bool(alert.get('enabled'))}}

def snapshot(record):
    return {key:copy.deepcopy(record.get(key)) for key in ('name','first_name','middle_name','last_name','district','tehsil','affidavit_profile','bidder_tools') if key in record}

def keep_backup(record):
    value=snapshot(record);history=record.setdefault('private_backups',[])
    if not history or history[-1].get('data')!=value:
        history.append({'at':time.time(),'data':value});record['private_backups']=history[-7:]

def bound_record(record):
    # Google Sheets cells have a 50,000-character limit. Trim older private
    # history, never current account/profile or unresolved reports.
    def size():return len(json.dumps(record,ensure_ascii=False))
    while size()>47000 and record.get('private_backups'):record['private_backups'].pop(0)
    while size()>47000 and len(record.get('admin_history',[]))>10:record['admin_history'].pop(0)
    while size()>47000:
        reports=record.get('mistake_reports',[]);old=next((item for item in reports if item['status'] not in ('pending','reviewing')),None)
        if old:reports.remove(old)
        else:break
    if size()>48000:raise AccountError('Saved data सीमा तक पहुँच गया है। पुराने Saved Filters या रिपोर्ट हटाएँ।',413)

def audit(record,actor,action,**details):
    history=record.setdefault('admin_history',[])
    history.append({'id':str(uuid.uuid4()),'at':time.time(),'actor':actor,'action':action,**details});record['admin_history']=history[-100:]

def mutate(accounts,user_id,fn):
    for _ in range(4):
        record=accounts.get(user_id=user_id)
        if not record:raise AccountError('Account नहीं मिला।',404)
        revision=record['revision'];fn(record)
        if accounts.update(record,revision):
            alert=record.get('bidder_tools',{}).get('telegram',{})
            with _cache_lock:
                if alert.get('enabled') and alert.get('chat_id'):_alert_index.add(user_id)
                else:_alert_index.discard(user_id)
            return record
    raise AccountError('साथ में बदलाव हुआ है। फिर प्रयास करें।',409)

def all_records(server):
    if sheets.enabled():ids=[str(row.get('visitor_id','')) for row in sheets.call('list_visitors')['visitors']]
    else:
        conn=server.visitor_db()
        try:ids=[row['user_id'] for row in conn.execute('SELECT user_id FROM password_accounts').fetchall()]
        finally:conn.close()
    records=[]
    # Remote calls overlap, with a small limit to protect the Sheet service.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for record in pool.map(lambda key:server.account_service.get(user_id=key),sorted(set(ids))):
            if record:records.append(record)
    return records

def cipher(server):
    secret=server.admin_session_secret()
    if not secret:raise AccountError('Private backup configuration missing.',503)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('mp-private-backup-v1:'+secret).encode()).digest()))

def decode_backup(server,text):
    if not isinstance(text,str) or len(text)>8000000:raise AccountError('Backup file बहुत बड़ी या गलत है।')
    try:
        decoded=base64.b64decode(text,altchars=b'-_',validate=True)
        if base64.urlsafe_b64encode(decoded).decode()!=text:raise ValueError('Noncanonical backup')
        data=json.loads(cipher(server).decrypt(text.encode()))
    except (InvalidToken,ValueError,UnicodeError):raise AccountError('यह backup इस website का नहीं है या बदल दिया गया है।') from None
    if data.get('format')!='mp-private-accounts-v1' or not isinstance(data.get('records'),list):raise AccountError('Backup format गलत है।')
    if len(data['records'])>10000:raise AccountError('Backup records बहुत अधिक हैं।')
    for record in data['records']:
        try:uuid.UUID(record['user_id'])
        except (ValueError,KeyError,TypeError):raise AccountError('Backup user ID गलत है।') from None
        if not account_mobile(server,record.get('mobile')) or not isinstance(record.get('password_hash'),str):raise AccountError('Backup account गलत है।')
    return data

def job(key,fn):
    with _jobs_lock:
        for old,value in list(_jobs.items()):
            if time.monotonic()-value[1]>900:del _jobs[old]
        item=_jobs.get(key)
        if not item:item=(_pool.submit(fn),time.monotonic());_jobs[key]=item
    if not item[0].done():return jsonify({'ok':True,'pending':True}),202
    return jsonify({'ok':True,**item[0].result()})

def process_alerts(server,call=None):
    from signup_alerts import configured_call
    call=call or configured_call;accounts=server.account_service
    updates=call('getUpdates',limit=100,timeout=0,allowed_updates=['message']) or []
    bound=sent=uncertain=0
    for update in updates:
        message=update.get('message') or {};chat=message.get('chat') or {}
        match=re.fullmatch(r'/start(?:@mptenders_bot)? w_([0-9a-f]{32})_([A-Za-z0-9_-]{16})',message.get('text',''))
        if not match or chat.get('type')!='private' or chat.get('id')!=message.get('from',{}).get('id'):continue
        user_id=str(uuid.UUID(hex=match[1]));record=accounts.get(user_id=user_id)
        link=(record or {}).get('bidder_tools',{}).get('telegram_link') or {}
        if not record or record.get('blocked') or link.get('expires',0)<time.time() or not hmac.compare_digest(link.get('hash',''),accounts.digest(match[2])):continue
        def bind(current):
            state=current.setdefault('bidder_tools',{});fresh=state.get('telegram_link') or {}
            if fresh.get('hash')!=link['hash'] or fresh.get('expires',0)<time.time():raise AccountError('Link expired.')
            state['telegram']={'chat_id':chat['id'],'enabled':True,'connected_at':time.time(),'cursor':time.time()};state.pop('telegram_link',None)
        mutate(accounts,user_id,bind);bound+=1
        call('sendMessage',chat_id=chat['id'],text='✅ आपकी My Shortlist के private alerts शुरू हैं। अंतिम तिथि, शुल्क और शुद्धिपत्र के बदलाव यहीं आएँगे। बंद करने के लिए dashboard में Private Alerts खोलें।',disable_web_page_preview=True)
    update_ids=[item['update_id'] for item in updates if isinstance(item.get('update_id'),int)]
    if update_ids:call('getUpdates',offset=max(update_ids)+1,limit=1,timeout=0,allowed_updates=['message'])
    history=public_json('tender_changes.json').get('tenders',{})
    global _alert_refreshed
    if not _alert_refreshed or time.monotonic()-_alert_refreshed>1800:
        candidates=all_records(server)
        with _cache_lock:
            _alert_index.clear();_alert_index.update(record['user_id'] for record in candidates if record.get('bidder_tools',{}).get('telegram',{}).get('enabled'));_alert_refreshed=time.monotonic()
    else:
        with _cache_lock:keys=list(_alert_index)
        candidates=[accounts.get(user_id=key) for key in keys]
    for record in candidates:
        if not record:continue
        state=record.get('bidder_tools',{});alert=state.get('telegram',{})
        if record.get('blocked') or not alert.get('enabled') or not alert.get('chat_id'):continue
        events=[]
        for tid in state.get('shortlist',[]):
            events.extend({**event,'tender_id':tid} for event in history.get(tid,[]) if event.get('at',0)>alert.get('cursor',0))
        events.sort(key=lambda item:item['at']);events=events[:12]
        if not events:continue
        digest=hashlib.sha256(json.dumps(events,sort_keys=True).encode()).hexdigest()
        pending=alert.get('outbox') or {}
        # An uncertain send is held for admin review; do not resend blindly.
        if pending.get('state')=='sending':uncertain+=1;continue
        chat_id=alert['chat_id'];cursor=max(event['at'] for event in events)
        def reserve(current):
            fresh=current.get('bidder_tools',{}).get('telegram',{})
            if not fresh.get('enabled') or fresh.get('chat_id')!=chat_id or fresh.get('cursor',0)>=cursor or fresh.get('outbox',{}).get('state')=='sending':raise AccountError('Alert state changed.',409)
            fresh['outbox']={'state':'sending','digest':digest,'cursor':cursor,'at':time.time()}
        try:mutate(accounts,record['user_id'],reserve)
        except AccountError:continue
        lines=['🔔 आपकी Shortlist में बदलाव']
        for event in events:
            lines.extend(['',event['tender_id'],event.get('field','')+': '+str(event.get('before','—'))+' → '+str(event.get('after','—'))])
        lines.extend(['','🌐 https://tenders.codinglms.xyz/','⚠️ अंतिम तिथि, शुल्क और शुद्धिपत्र की पुष्टि आधिकारिक पोर्टल से करें।'])
        try:
            result=call('sendMessage',chat_id=chat_id,text='\n'.join(lines)[:4000],disable_web_page_preview=True)
            if not (result or {}).get('message_id'):raise RuntimeError('Telegram receipt missing')
        except Exception:uncertain+=1;continue
        def complete(current):
            fresh=current.get('bidder_tools',{}).get('telegram',{})
            if fresh.get('outbox',{}).get('digest')==digest:
                fresh.update(cursor=cursor,outbox={'state':'sent','digest':digest,'message_id':result['message_id'],'at':time.time()})
        mutate(accounts,record['user_id'],complete);sent+=1
    return {'bound':bound,'sent':sent,'uncertain':uncertain}

def safe_process_alerts(server):
    try:return process_alerts(server)
    except AccountError:raise
    except Exception:
        # Upstream HTTP exceptions may contain the bot token in their URL.
        raise AccountError('Private Telegram worker अभी उपलब्ध नहीं है। अगला run फिर प्रयास करेगा।',503) from None


def queue_system_audit(server,actor,action,**details):
    def save():
        records=all_records(server)
        if records:mutate(server.account_service,records[0]['user_id'],lambda record:audit(record,actor,action,**details))
    _pool.submit(save)

def install(server):
    app=server.app;accounts=server.account_service
    def auth():
        payload=request.get_json(silent=True) or {};return payload,accounts.authenticate(payload.get('session_token'))
    def admin():
        email,error=server.require_admin()
        if error:raise AccountError('Google Admin login required.',401)
        return email
    @app.after_request
    def private_cache(response):
        if request.path.startswith(('/api/accounts/tools','/api/admin/tools','/api/internal/bidder-alerts')):response.headers['Cache-Control']='no-store'
        return response
    @app.post('/api/accounts/tools')
    def state():
        _,record=auth();return jsonify({'ok':True,**tools_state(record)})
    @app.post('/api/accounts/tools/shortlist')
    def shortlist():
        payload,record=auth();tid=payload.get('tender_id');selected=payload.get('selected')
        if not isinstance(tid,str) or not TENDER.fullmatch(tid) or type(selected) is not bool:raise AccountError('Tender ID या चयन गलत है।')
        def update(current):
            keep_backup(current);state=current.setdefault('bidder_tools',{});ids=list(state.get('shortlist',[]))
            if selected and tid not in ids:ids.append(tid)
            if not selected:ids=[key for key in ids if key!=tid]
            if len(ids)>200:raise AccountError('अधिकतम 200 टेंडर Shortlist कर सकते हैं।')
            state['shortlist']=ids
        record=mutate(accounts,record['user_id'],update);return jsonify({'ok':True,**tools_state(record)})
    @app.post('/api/accounts/tools/presets')
    def presets():
        payload,record=auth();name=str(payload.get('name','')).strip();values=payload.get('filters',{})
        if not 1<=len(name)<=40 or not isinstance(values,dict) or any(key not in FILTERS or not isinstance(value,str) or len(value)>240 for key,value in values.items()):raise AccountError('Filter का नाम या विवरण गलत है।')
        def update(current):
            keep_backup(current);state=current.setdefault('bidder_tools',{});presets=[item for item in state.get('presets',[]) if item['name']!=name]
            if payload.get('delete') is not True:presets.append({'name':name,'filters':values})
            if len(json.dumps(presets,ensure_ascii=False))>6500:raise AccountError('Saved Filters का कुल आकार अधिक है। कुछ पुरानी खोज हटाएँ।')
            if len(presets)>20:raise AccountError('अधिकतम 20 Saved Filters रखें।')
            state['presets']=presets
        record=mutate(accounts,record['user_id'],update);return jsonify({'ok':True,**tools_state(record)})
    @app.post('/api/accounts/tools/report')
    def report():
        payload,record=auth();tid=payload.get('tender_id');text=str(payload.get('text','')).strip();field=payload.get('field')
        if not isinstance(tid,str) or not TENDER.fullmatch(tid) or field not in ('Closing Date','Fees','Corrigendum','Location','Other') or not 8<=len(text)<=1000:raise AccountError('गलती का सही विवरण (8–1000 अक्षर) भरें।')
        item={'id':str(uuid.uuid4()),'tender_id':tid,'field':field,'text':text,'at':time.time(),'status':'pending'}
        def update(current):
            reports=current.setdefault('mistake_reports',[])
            if sum(report['status']=='pending' for report in reports)>=20:raise AccountError('आपकी 20 रिपोर्ट जाँच में हैं। अभी नई रिपोर्ट नहीं भेज सकते।')
            current['mistake_reports']=(reports+[item])[-100:]
        mutate(accounts,record['user_id'],update);return jsonify({'ok':True,'report_id':item['id']})
    @app.post('/api/accounts/tools/telegram')
    def telegram():
        payload,record=auth()
        if payload.get('disable') is True:
            def disable(current):
                state=current.setdefault('bidder_tools',{});state.pop('telegram_link',None);state.setdefault('telegram',{})['enabled']=False
            record=mutate(accounts,record['user_id'],disable);return jsonify({'ok':True,**tools_state(record)})
        raw=secrets.token_urlsafe(12)
        def link(current):current.setdefault('bidder_tools',{})['telegram_link']={'hash':accounts.digest(raw),'expires':time.time()+900}
        mutate(accounts,record['user_id'],link)
        return jsonify({'ok':True,'url':'https://t.me/mptenders_bot?start=w_'+uuid.UUID(record['user_id']).hex+'_'+raw,'expires_minutes':15})
    @app.get('/api/admin/tools/review')
    def review():
        email=admin();key=('review',email,request.args.get('job',''))
        if not key[2]:return jsonify({'ok':True,'job':secrets.token_urlsafe(16)})
        def load():
            reports=[];history=[]
            for record in all_records(server):
                identity={'user_id':record['user_id'],'name':record.get('name',''),'mobile':record['mobile']}
                reports.extend({**item,**identity} for item in record.get('mistake_reports',[]))
                history.extend({**item,**identity} for item in record.get('admin_history',[]))
            return {'reports':sorted(reports,key=lambda x:x['at'],reverse=True)[:300],'history':sorted(history,key=lambda x:x['at'],reverse=True)[:300]}
        return job(key,load)
    @app.post('/api/admin/tools/report')
    def resolve_report():
        email=admin();payload=request.get_json(silent=True) or {};status=payload.get('status')
        if status not in ('reviewing','resolved','rejected'):raise AccountError('Report status गलत है।')
        def update(current):
            item=next((item for item in current.get('mistake_reports',[]) if item['id']==payload.get('report_id')),None)
            if not item:raise AccountError('Report नहीं मिली।',404)
            item.update(status=status,reviewed_by=email,reviewed_at=time.time(),note=str(payload.get('note',''))[:500]);audit(current,email,'mistake_report_'+status,report_id=item['id'],tender_id=item['tender_id'])
        mutate(accounts,str(payload.get('user_id','')),update);return jsonify({'ok':True})
    @app.get('/api/admin/tools/backup')
    def backup():
        email=admin();key=('backup',email,request.args.get('job',''))
        if not key[2]:return jsonify({'ok':True,'job':secrets.token_urlsafe(16)})
        def load():
            records=all_records(server)
            for record in records:
                record.pop('sessions',None);record.pop('reset',None)
                state=record.get('bidder_tools',{});state.pop('telegram_link',None)
            raw=json.dumps({'format':'mp-private-accounts-v1','created_at':time.time(),'records':records},ensure_ascii=False).encode()
            encrypted=cipher(server).encrypt(raw).decode()
            if records:mutate(accounts,records[0]['user_id'],lambda record:audit(record,email,'encrypted_backup_download',records=len(records)))
            return {'backup':encrypted,'count':len(records)}
        return job(key,load)
    @app.post('/api/admin/tools/restore')
    def restore():
        email=admin();payload=request.get_json(silent=True) or {};data=decode_backup(server,payload.get('backup'))
        if payload.get('confirm') is not True:return jsonify({'ok':True,'preview':True,'count':len(data['records']),'created_at':data['created_at'],'message':'मौजूदा password, mobile verification और block status सुरक्षित रहेंगे। Profile और Saved Filters वापस आएँगे; login sessions बंद होंगे।'})
        key=('restore',email,hashlib.sha256(payload['backup'].encode()).hexdigest())
        def perform():
            restored=0
            for saved in data['records']:
                existing=accounts.get(user_id=saved['user_id']);collision=accounts.get(mobile=saved['mobile'])
                if collision and collision['user_id']!=saved['user_id']:raise AccountError('Mobile दूसरे account में है। Restore रोक दिया गया।',409)
                if existing:
                    if existing['mobile']!=saved['mobile']:raise AccountError('Account mobile mismatch.',409)
                    def update(current):
                        keep_backup(current);current.update(snapshot(saved));current.update(sessions=[],reset=None)
                        state=current.get('bidder_tools',{});state.pop('telegram_link',None);state['telegram']={'enabled':False}
                        audit(current,email,'private_backup_restore')
                    mutate(accounts,saved['user_id'],update)
                else:
                    record=copy.deepcopy(saved);record.update(sessions=[],reset=None);record.pop('signup_alert',None)
                    record.get('bidder_tools',{}).update(telegram={'enabled':False});audit(record,email,'private_backup_restore_new');accounts.operation('create',record=record)
                restored+=1
            return {'restored':restored}
        return job(key,perform)
    @app.post('/api/internal/bidder-alerts')
    def alerts_job():
        raw=request.get_data();stamp=request.headers.get('X-Alert-Timestamp','');signature=request.headers.get('X-Alert-Signature','');secret=os.getenv('TELEGRAM_BOT_TOKEN','')
        if not secret or not stamp.isdigit() or abs(time.time()-int(stamp))>300 or len(raw)>256:raise AccountError('Signed worker required.',401)
        expected=hmac.new(secret.encode(),b'mp-bidder-alerts\n'+stamp.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise AccountError('Signed worker required.',401)
        global _alert_current
        slot=int(time.time())//300
        with _jobs_lock:
            if _alert_current is None or (_alert_current[0].done() and _alert_current[1]!=slot):_alert_current=(_pool.submit(lambda:safe_process_alerts(server)),slot)
            future=_alert_current[0]
        if not future.done():return jsonify({'ok':True,'pending':True}),202
        return jsonify({'ok':True,**future.result()})
