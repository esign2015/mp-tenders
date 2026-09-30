import {zipSync, strToU8} from 'fflate';

const text=v=>String(v??'').trim();
const encoder=new TextEncoder();
const seconds=()=>Math.floor(Date.now()/1000);
const ist=()=>new Date(Date.now()+19800000).toISOString().replace('Z','+05:30');
const emails=env=>text(env.ADMIN_GOOGLE_EMAILS).toLowerCase().split(',').map(text).filter(Boolean);
const sessionSecret=env=>text(env.TELEGRAM_SESSION_SECRET||env.TELEGRAM_BOT_TOKEN);
const adminSecret=env=>text(env.ADMIN_SESSION_SECRET||env.TELEGRAM_SESSION_SECRET||env.TELEGRAM_BOT_TOKEN);
const json=(data,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store'}});
class HttpError extends Error {constructor(status,message){super(message);this.status=status;}}
const fail=(status,message)=>{throw new HttpError(status,message);};
const hex=bytes=>Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
const unhex=value=>/^[a-f0-9]{64}$/i.test(value)?Uint8Array.from(value.match(/../g),b=>parseInt(b,16)):null;
const b64url=bytes=>btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
function decode64(value){return Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/').padEnd(Math.ceil(value.length/4)*4,'=')),c=>c.charCodeAt(0));}
async function key(secret){if(!secret)fail(503,'Authentication is not configured.');return crypto.subtle.importKey('raw',typeof secret==='string'?encoder.encode(secret):secret,{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);}
export async function makeSession(payload,secret){const body=b64url(encoder.encode(JSON.stringify(payload)));return body+'.'+hex(await crypto.subtle.sign('HMAC',await key(secret),encoder.encode(body)));}
export async function readSession(token,secret){
  if(!secret||typeof token!=='string'||token.length>4096)return null;
  try{const pieces=token.split('.');if(pieces.length!==2)return null;const [body,signature]=pieces;const bytes=unhex(signature);if(!bytes||!await crypto.subtle.verify('HMAC',await key(secret),bytes,encoder.encode(body)))return null;const payload=JSON.parse(new TextDecoder().decode(decode64(body)));return Number(payload.exp)>=seconds()?payload:null;}catch{return null;}
}
async function admin(request,env){const payload=await readSession(text(request.headers.get('Authorization')).replace(/^Bearer /,''),adminSecret(env));return payload&&emails(env).includes(text(payload.email).toLowerCase())?payload.email:null;}
async function user(payload,env){const session=await readSession(text(payload.session_token),sessionSecret(env));const uid=Number(session?.uid);return Number.isSafeInteger(uid)&&uid>0?uid:null;}
async function tg(env,method,payload){
  if(!env.TELEGRAM_BOT_TOKEN)fail(503,'Telegram bot is not configured.');
  const r=await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(20000)});
  if(!r.ok)fail(502,'Telegram service is unavailable.');return r.json();
}
async function membership(env,uid){const data=await tg(env,'getChatMember',{chat_id:env.TELEGRAM_CHANNEL||'@mptendersalert',user_id:uid});const m=data.result||{};return data.ok&&(['creator','administrator','member'].includes(m.status)||(m.status==='restricted'&&m.is_member));}
async function userAdmin(env,uid){if(!uid)return false;if(text(env.ADMIN_TELEGRAM_IDS).split(',').map(text).includes(String(uid)))return true;const data=await tg(env,'getChatMember',{chat_id:env.TELEGRAM_CHANNEL||'@mptendersalert',user_id:uid});return Boolean(data.ok&&data.result?.status==='creator');}
async function needAdmin(request,env,payload){if(await admin(request,env))return;const uid=await user(payload,env);if(!await userAdmin(env,uid))fail(403,'Admin access required.');}
const registered=row=>Boolean(row&&['name','mobile','email','state','district'].every(k=>text(row[k])));
const getUser=(env,uid)=>env.DB.prepare('SELECT * FROM users WHERE telegram_id=?').bind(uid).first();
export async function touchLogin(env,uid,p={}){
  const now=ist(),first=text(p.first_name),last=text(p.last_name),username=text(p.username);
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO users(telegram_id,first_name,last_name,username,name,signup_at,last_login_at,login_count)
      VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(telegram_id) DO UPDATE SET
      first_name=COALESCE(NULLIF(excluded.first_name,''),users.first_name),
      last_name=COALESCE(NULLIF(excluded.last_name,''),users.last_name),
      username=COALESCE(NULLIF(excluded.username,''),users.username),
      last_login_at=excluded.last_login_at,login_count=users.login_count+1`).bind(uid,first,last,username,[first,last].filter(Boolean).join(' '),now,now),
    env.DB.prepare('INSERT INTO login_events(telegram_id,login_at) VALUES(?,?)').bind(uid,now)
  ]);
  return getUser(env,uid);
}
export async function verifyTelegram(payload,env){
  const date=Number(payload.auth_date),uid=Number(payload.id),signature=unhex(text(payload.hash));
  if(!signature||!Number.isSafeInteger(date)||date<seconds()-86400||date>seconds()+60||!Number.isSafeInteger(uid)||uid<=0)return null;
  const message=Object.keys(payload).filter(k=>k!=='hash'&&payload[k]!==null).sort().map(k=>`${k}=${payload[k]}`).join('\n');
  const secret=await crypto.subtle.digest('SHA-256',encoder.encode(text(env.TELEGRAM_BOT_TOKEN)));
  if(!env.TELEGRAM_BOT_TOKEN||!await crypto.subtle.verify('HMAC',await key(secret),signature,encoder.encode(message)))return null;
  return uid;
}
export function profileValues(p){
  const name=text(p.name),email=text(p.email).toLowerCase(),state=text(p.state),district=text(p.district);
  let mobile=text(p.mobile).replace(/\D/g,'');if(mobile.startsWith('91')&&mobile.length===12)mobile=mobile.slice(2);
  if(name.length<2)fail(400,'कृपया अपना पूरा नाम दर्ज करें।');
  if(!/^[6-9]\d{9}$/.test(mobile))fail(400,'कृपया सही नंबर डालिए।');
  if(!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(email))fail(400,'कृपया सही ईमेल आईडी डालिए।');
  if(!state||district.length<2)fail(400,'कृपया राज्य और जिला चुनें।');
  return [name,'+91'+mobile,email,state,district];
}
const runMap={data_refresh:'scrape.yml',retry_pending:'targeted-pending-retry.yml',telegram_test:'telegram-test.yml',closing_today:'telegram_manual_pdf.yml',new_today:'telegram_manual_pdf.yml',all:'telegram_manual_pdf.yml'};
async function github(env,path,body){
  if(!env.GITHUB_ACTIONS_TOKEN)fail(503,'GitHub workflow access is not configured.');
  const r=await fetch(`https://api.github.com/repos/${env.GITHUB_REPO_OWNER||'esign2015'}/${env.GITHUB_REPO_NAME||'mp-tenders'}${path}`,{method:body?'POST':'GET',headers:{Authorization:`Bearer ${env.GITHUB_ACTIONS_TOKEN}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'mp-tenders-admin','Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(20000)});
  if(!r.ok)fail(502,`GitHub workflow request failed (HTTP ${r.status}).`);return r.status===204?{}:r.json();
}
const escape=v=>String(v??'').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function column(index){let value='';for(index++;index;index=Math.floor((index-1)/26))value=String.fromCharCode(65+(index-1)%26)+value;return value;}
function sheet(rows){return `<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>${rows.map((row,i)=>`<row r="${i+1}">${row.map((v,j)=>typeof v==='number'&&Number.isFinite(v)?`<c r="${column(j)}${i+1}"><v>${v}</v></c>`:`<c r="${column(j)}${i+1}" t="inlineStr"><is><t xml:space="preserve">${escape(v)}</t></is></c>`).join('')}</row>`).join('')}</sheetData></worksheet>`;}
export function userWorkbook(users,daily=[]){
  const headers=['S.No.','Name','Mobile','Email','State','District','Telegram Username','Telegram ID','First Signup (IST)','Last Login (IST)','Login Count','Mobile Verified'];
  const rows=[headers,...users.map((r,i)=>[i+1,r.name,r.mobile,r.email,r.state,r.district,r.username?'@'+r.username:'',r.telegram_id,r.signup_at,r.last_login_at,r.login_count,r.mobile_verified?'Yes':'No'])];
  const data={
    '[Content_Types].xml':'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    '_rels/.rels':'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml':'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Users" sheetId="1" r:id="rId1"/><sheet name="Daily Stats" sheetId="2" r:id="rId2"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels':'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>',
    'xl/worksheets/sheet1.xml':sheet(rows),
    'xl/worksheets/sheet2.xml':sheet([['Date (IST)','New Users','Unique Users','Total Logins'],...daily.map(r=>[r.day,r.new_users,r.unique_logins,r.total_logins])])
  };return zipSync(Object.fromEntries(Object.entries(data).map(([k,v])=>[k,strToU8(v)])),{level:1});
}
async function stats(env){
  const day=ist().slice(0,10),q=(sql,args=[])=>env.DB.prepare(sql).bind(...args);
  const result=await env.DB.batch([
    q('SELECT COUNT(*) AS total_users, SUM(CASE WHEN mobile<>\'\' THEN 1 ELSE 0 END) AS registered_mobile, SUM(CASE WHEN substr(signup_at,1,10)=? THEN 1 ELSE 0 END) AS today_new_users FROM users',[day]),
    q('SELECT COUNT(*) AS today_logins, COUNT(DISTINCT telegram_id) AS today_unique_users FROM login_events WHERE substr(login_at,1,10)=?',[day]),
    q('SELECT substr(signup_at,1,10) AS day, COUNT(*) AS new_users FROM users GROUP BY substr(signup_at,1,10) ORDER BY day DESC LIMIT 90')
  ]);return {...result[0].results[0],...result[1].results[0],daily_signups:result[2].results};
}
async function photo(env,uid){
  const pictures=await tg(env,'getUserProfilePhotos',{user_id:uid,offset:0,limit:1}),sizes=pictures.result?.photos?.[0];if(!pictures.ok||!sizes?.length)return new Response(null,{status:404});
  const file=await tg(env,'getFile',{file_id:sizes.reduce((a,b)=>a.width*a.height>b.width*b.height?a:b).file_id});if(!file.ok)return new Response(null,{status:404});
  const r=await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${file.result.file_path}`);if(!r.ok)return new Response(null,{status:404});return new Response(r.body,{headers:{'Content-Type':r.headers.get('Content-Type')||'image/jpeg','Cache-Control':'private,max-age=300'}});
}
async function route(request,env){
  const url=new URL(request.url),path=url.pathname,method=request.method;
  const params=Object.fromEntries(url.searchParams);
  let payload={};if(method==='POST'){try{payload=await request.json();}catch{fail(400,'Invalid request JSON.');}if(!payload||Array.isArray(payload)||typeof payload!=='object')fail(400,'Invalid request JSON.');}
  if(method==='GET'&&(path==='/'||path==='/health'))return json({status:'healthy',user_db_backend:'cloudflare-d1',source:'https://mptenders.gov.in/nicgep/app'});
  if(method==='GET'&&path==='/api/admin/config')return json({ok:true,google_client_id:text(env.GOOGLE_CLIENT_ID),allowed_domains:['google.com']});
  if(method==='POST'&&path==='/api/admin/google'){
    if(!env.GOOGLE_CLIENT_ID)fail(503,'Google Admin login is not configured.');if(!text(payload.credential))fail(400,'Google authentication token missing.');
    const r=await fetch('https://oauth2.googleapis.com/tokeninfo?id_token='+encodeURIComponent(payload.credential),{signal:AbortSignal.timeout(15000)});if(!r.ok)fail(401,'Google login verification failed.');
    const info=await r.json(),email=text(info.email).toLowerCase();if(info.aud!==env.GOOGLE_CLIENT_ID||Number(info.exp)<seconds())fail(401,'Google client verification failed.');if(String(info.email_verified)!=='true'||!emails(env).includes(email))fail(403,'इस Google account को Admin access नहीं दिया गया है।');
    return json({ok:true,email,session_token:await makeSession({email,exp:seconds()+Number(env.ADMIN_SESSION_TTL||28800)},adminSecret(env)),expires_in:Number(env.ADMIN_SESSION_TTL||28800)});
  }
  if(path.startsWith('/api/admin/')&&['/api/admin/session','/api/admin/logout','/api/admin/workflow-status','/api/admin/action'].includes(path)){
    const email=await admin(request,env);if(!email)fail(401,'Admin login required.');
    if(method==='GET'&&path==='/api/admin/session')return json({ok:true,email});
    if(method==='POST'&&path==='/api/admin/logout')return json({ok:true});
    if(method==='GET'&&path==='/api/admin/workflow-status'){
      const workflow=runMap[text(params.action)];if(!workflow)fail(400,'Unknown workflow status request.');const data=await github(env,`/actions/workflows/${workflow}/runs?event=workflow_dispatch&per_page=1`);return json({ok:true,action:params.action,workflow,run:data.workflow_runs?.[0]||{},requested_by:email});
    }
    if(method==='POST'&&path==='/api/admin/action'){
      const action=text(payload.action),report=text(payload.report||'closing_today');if(action==='telegram_pdf'&&!['closing_today','new_today','all'].includes(report))fail(400,'Invalid PDF report.');const workflow=action==='telegram_pdf'?'telegram_manual_pdf.yml':runMap[action];if(!workflow||!['telegram_pdf','telegram_test','data_refresh','retry_pending'].includes(action))fail(400,'Unknown admin command.');
      await github(env,`/actions/workflows/${workflow}/dispatches`,{ref:env.GITHUB_REPO_BRANCH||'main',...(action==='telegram_pdf'?{inputs:{report}}:{})});return json({ok:true,message:'Workflow started.',requested_by:email});
    }
  }
  if(method==='GET'&&path==='/api/telegram/config'){const data=await tg(env,'getMe',{});if(!data.ok)fail(502,'Telegram bot configuration failed.');return json({ok:true,username:data.result.username});}
  const photoMatch=path.match(/^\/api\/telegram\/photo\/(\d+)$/);if(method==='GET'&&photoMatch)return photo(env,Number(photoMatch[1]));
  if(method==='POST'&&(path==='/api/telegram/verify'||path==='/api/telegram/session')){
    const uid=path.endsWith('/verify')?await verifyTelegram(payload,env):await user(payload,env);if(!uid)fail(401,'Telegram session expired or verification failed. Please login again.');if(!await membership(env,uid))fail(403,'Please join the Telegram channel first.');
    const row=await touchLogin(env,uid,path.endsWith('/verify')?payload:{});return json({verified:true,id:uid,session_token:await makeSession({exp:seconds()+Number(env.TELEGRAM_SESSION_TTL||604800),uid},sessionSecret(env)),username:payload.username||'',first_name:payload.first_name||'',last_name:payload.last_name||'',photo_url:text(payload.photo_url)||`${url.origin}/api/telegram/photo/${uid}`,profile_registered:registered(row),is_admin:await userAdmin(env,uid),message:'Telegram membership verified.'});
  }
  if(method==='GET'&&path==='/api/users/profile'){
    const uid=await user(params,env);if(!uid)fail(401,'Valid Telegram session required.');const row=await getUser(env,uid);const profile=row?Object.fromEntries(['name','mobile','email','state','district','username','telegram_id'].map(k=>[k,row[k]])):undefined;return json({ok:true,registered:registered(row),is_admin:await userAdmin(env,uid),user:profile});
  }
  if(method==='POST'&&path==='/api/users/register'){
    const uid=await user(payload,env);if(!uid)fail(401,'Valid Telegram session required.');const values=profileValues(payload);if(!await getUser(env,uid))fail(404,'User record was not found. Please login again.');await env.DB.prepare('UPDATE users SET name=?,mobile=?,email=?,state=?,district=?,mobile_verified=0 WHERE telegram_id=?').bind(...values,uid).run();return json({ok:true,registered:true,message:'Profile saved successfully.',user:{name:values[0],mobile:values[1]}});
  }
  if(method==='GET'&&['/api/users/list','/api/users/stats'].includes(path)){await needAdmin(request,env,params);if(path.endsWith('/stats'))return json({ok:true,...await stats(env)});const result=await env.DB.prepare('SELECT name,mobile,email,state,district,username,telegram_id,signup_at,last_login_at,login_count FROM users ORDER BY signup_at DESC').all();return json({ok:true,users:result.results});}
  if(method==='POST'&&path==='/api/users/export'){
    await needAdmin(request,env,payload);const users=await env.DB.prepare('SELECT * FROM users ORDER BY signup_at DESC').all();const daily=await env.DB.prepare(`SELECT substr(u.signup_at,1,10) AS day,COUNT(*) AS new_users,
      (SELECT COUNT(DISTINCT telegram_id) FROM login_events WHERE substr(login_at,1,10)=substr(u.signup_at,1,10)) AS unique_logins,
      (SELECT COUNT(*) FROM login_events WHERE substr(login_at,1,10)=substr(u.signup_at,1,10)) AS total_logins
      FROM users u GROUP BY substr(u.signup_at,1,10) ORDER BY day DESC`).all();
    return new Response(userWorkbook(users.results,daily.results),{headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','Content-Disposition':'attachment; filename="MP_Tender_Users_Report.xlsx"','Cache-Control':'no-store'}});
  }
  if(method==='POST'&&path==='/api/telegram/send-pdf'){
    const uid=await verifyTelegram(payload.auth||{},env);if(!uid)fail(401,'Telegram login required.');if(!payload.pdf_base64)fail(400,'PDF data is missing.');if(payload.pdf_base64.length>60*1024*1024)fail(413,'PDF is too large.');let bytes;try{bytes=decode64(payload.pdf_base64);}catch{fail(400,'PDF data is invalid.');}
    const form=new FormData();form.append('chat_id',String(uid));form.append('caption','📄 MP Tender Dashboard PDF');form.append('document',new Blob([bytes],{type:'application/pdf'}),text(payload.filename)||'MP_Tender_Dashboard.pdf');
    const r=await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendDocument`,{method:'POST',body:form,signal:AbortSignal.timeout(45000)});const data=await r.json();if(!data.ok){const notStarted=/chat not found|initiate conversation|deactivated|forbidden/i.test(data.description||'');return json({ok:false,bot_not_started:notStarted,message:notStarted?'Telegram bot को पहले START करना जरूरी है।':'Telegram PDF delivery failed.'},notStarted?409:502);}return json({ok:true,message:'PDF Telegram पर भेज दी गई है।'});
  }
  if(method==='GET'&&path==='/api/tenders'){
    const r=await fetch(`https://raw.githubusercontent.com/${env.GITHUB_REPO_OWNER||'esign2015'}/${env.GITHUB_REPO_NAME||'mp-tenders'}/${env.GITHUB_REPO_BRANCH||'main'}/all_tenders_org_detailed.csv`,{cf:{cacheTtl:60}});if(!r.ok)fail(502,'Tender snapshot unavailable.');const rows=parseCsv(await r.text());return json({updated_at:new Date().toISOString(),count:rows.length,tenders:rows});
  }
  if(method==='POST'&&path==='/api/fetch'){if(!await admin(request,env))fail(401,'Admin login required.');await github(env,'/actions/workflows/scrape.yml/dispatches',{ref:env.GITHUB_REPO_BRANCH||'main'});return json({ok:true,message:'Refresh workflow started.'},202);}
  fail(404,'Endpoint not found.');
}
export function parseCsv(input){const rows=[];let row=[],value='',quoted=false;for(let i=0;i<input.length;i++){const c=input[i];if(c==='"'){if(quoted&&input[i+1]==='"'){value+='"';i++;}else quoted=!quoted;}else if(c===','&&!quoted){row.push(value);value='';}else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&input[i+1]==='\n')i++;row.push(value);if(row.some(Boolean))rows.push(row);row=[];value='';}else value+=c;}if(value||row.length){row.push(value);rows.push(row);}const fields=rows.shift()||[];if(fields[0])fields[0]=fields[0].replace(/^\uFEFF/,'');return rows.map(r=>Object.fromEntries(fields.map((k,i)=>[k,r[i]||''])));}
export default {async fetch(request,env){
  const origin=request.headers.get('Origin'),allowed=env.ALLOWED_ORIGIN||'https://tenders.codinglms.xyz';
  if(origin&&origin!==allowed)return json({ok:false,message:'Origin not allowed.'},403);
  let response;
  try{response=request.method==='OPTIONS'?new Response(null,{status:204}):await route(request,env);}catch(error){if(!(error instanceof HttpError))console.error('API request failed',error.name);response=json({ok:false,message:error instanceof HttpError?error.message:'Server request failed. Please retry.'},error.status||503);}
  const headers=new Headers(response.headers);headers.set('Access-Control-Allow-Origin',allowed);headers.set('Vary','Origin');headers.set('Access-Control-Allow-Headers','Authorization, Content-Type');headers.set('Access-Control-Allow-Methods','GET, POST, OPTIONS');headers.set('X-Content-Type-Options','nosniff');return new Response(response.body,{status:response.status,headers});
}};
