import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {createHmac,createHash} from 'node:crypto';
import {readFileSync,writeFileSync} from 'node:fs';
import {unzipSync,strFromU8} from 'fflate';
import worker,{readSession,makeSession,touchLogin,verifyTelegram,userWorkbook,parseCsv} from '../src/worker.mjs';
function database(){const db=new DatabaseSync(':memory:');db.exec(readFileSync(new URL('../migrations/0001_users.sql',import.meta.url),'utf8'));const adapter={prepare(sql){return {bind(...args){return {sql,async first(){return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){return db.prepare(sql).run(...args);}};},async all(){return {results:db.prepare(sql).all()};}};},async batch(queries){db.exec('BEGIN');try{const results=[];for(const q of queries){if(q.sql&&!/^SELECT/i.test(q.sql)){await q.run();results.push({results:[]});}else results.push(await q.all());}db.exec('COMMIT');return results;}catch(e){db.exec('ROLLBACK');throw e;}}};return {db,adapter};}
const envBase={ALLOWED_ORIGIN:'https://tenders.codinglms.xyz',ADMIN_GOOGLE_EMAILS:'admin@example.test',TELEGRAM_BOT_TOKEN:'test-bot-secret',TELEGRAM_SESSION_SECRET:'old-secret',ADMIN_SESSION_SECRET:'admin-secret'};
const call=(path,env,{method='GET',payload,token,origin}={})=>worker.fetch(new Request('https://api.example.test'+path,{method,headers:{...(token?{Authorization:'Bearer '+token}:{}),...(origin?{Origin:origin}:{}),'Content-Type':'application/json'},body:payload?JSON.stringify(payload):undefined}),env);
test('Python session signatures remain valid, tampering and expiry fail',async()=>{
  const body=Buffer.from(JSON.stringify({exp:Math.floor(Date.now()/1000)+60,uid:123})).toString('base64url');const python=body+'.'+createHmac('sha256','old-secret').update(body).digest('hex');
  assert.equal((await readSession(python,'old-secret')).uid,123);assert.equal(await readSession(python,'wrong-secret'),null);assert.equal(await readSession(python+'a','old-secret'),null);assert.equal(await readSession(await makeSession({uid:123,exp:1},'old-secret'),'old-secret'),null);
});
test('Telegram widget signature validates without trusting a client ID or future date',async()=>{
  const p={id:123,auth_date:Math.floor(Date.now()/1000),first_name:'User'};const sign=p=>createHmac('sha256',createHash('sha256').update(envBase.TELEGRAM_BOT_TOKEN).digest()).update(Object.keys(p).sort().map(k=>`${k}=${p[k]}`).join('\n')).digest('hex');
  assert.equal(await verifyTelegram({...p,hash:sign(p)},envBase),123);assert.equal(await verifyTelegram({...p,id:456,hash:sign(p)},envBase),null);const future={...p,auth_date:p.auth_date+3600};assert.equal(await verifyTelegram({...future,hash:sign(future)},envBase),null);
});
test('D1 login upsert increments atomically and retains registered profile',async()=>{
  const {db,adapter}=database(),env={...envBase,DB:adapter};await touchLogin(env,123,{first_name:'First',username:'one'});db.prepare('UPDATE users SET name=?,mobile=?,email=?,state=?,district=? WHERE telegram_id=123').run('Registered','+919876543210','a@example.test','MP','Indore');
  await touchLogin(env,123,{});const row=db.prepare('SELECT * FROM users').get();assert.equal(row.name,'Registered');assert.equal(row.username,'one');assert.equal(row.login_count,2);assert.equal(db.prepare('SELECT COUNT(*) AS n FROM login_events').get().n,2);
});
test('user profile cannot be read or changed without a valid session; admin export requires verified admin',async()=>{
  const {db,adapter}=database(),env={...envBase,DB:adapter};await touchLogin(env,123,{first_name:'User'});
  assert.equal((await call('/api/users/profile',env)).status,401);assert.equal((await call('/api/users/register',env,{method:'POST',payload:{name:'Name'}})).status,401);assert.equal((await call('/api/users/export',env,{method:'POST',payload:{}})).status,403);
  const token=await makeSession({exp:Math.floor(Date.now()/1000)+60,uid:123},env.TELEGRAM_SESSION_SECRET);
  const r=await call('/api/users/register',env,{method:'POST',payload:{session_token:token,name:'Test नाम',mobile:'9876543210',email:'test@example.test',state:'MP',district:'Kannod'}});assert.equal(r.status,200);assert.equal(db.prepare('SELECT mobile FROM users').get().mobile,'+919876543210');
  const admin=await makeSession({email:'admin@example.test',exp:Math.floor(Date.now()/1000)+60},env.ADMIN_SESSION_SECRET);const excel=await call('/api/users/export',env,{method:'POST',payload:{},token:admin});assert.equal(excel.status,200);assert(excel.headers.get('Content-Disposition').includes('.xlsx'));assert(Object.keys(unzipSync(new Uint8Array(await excel.arrayBuffer()))).includes('xl/worksheets/sheet1.xml'));
  assert.equal((await call('/api/admin/session',env,{token:await makeSession({email:'other@example.test',exp:Math.floor(Date.now()/1000)+60},env.ADMIN_SESSION_SECRET)})).status,401);
});
test('XLSX uses literal escaped strings including formula-looking user names',()=>{
  const bytes=userWorkbook([{name:'=HYPERLINK("bad") & नाम',mobile:'+919876543210',email:'a@example.test',state:'MP',district:'Kannod',telegram_id:123,login_count:2,mobile_verified:0}]);const xml=strFromU8(unzipSync(bytes)['xl/worksheets/sheet1.xml']);assert(xml.includes('t="inlineStr"'));assert(xml.includes('&amp;'));assert(!xml.includes('<f>'));writeFileSync('/tmp/mp-cloudflare-test-users.xlsx',bytes);
});
test('CSV quoting, multiline description and response contract remain compatible',async()=>{
  assert.deepEqual(parseCsv('Tender ID,Title\r\na,"Line 1\nLine 2, quoted ""title"""\r\n'),[{'Tender ID':'a',Title:'Line 1\nLine 2, quoted "title"'}]);
  const original=globalThis.fetch;globalThis.fetch=async()=>new Response('Tender ID,Title\na,Test\n');try{const r=await call('/api/tenders',envBase);const data=await r.json();assert.equal(data.count,1);assert.equal(data.tenders[0]['Tender ID'],'a');}finally{globalThis.fetch=original;}
});
test('CORS denies foreign origins; preflight and health stay available',async()=>{assert.equal((await call('/health',envBase,{origin:'https://other.example'})).status,403);assert.equal((await call('/health',envBase,{method:'OPTIONS',origin:envBase.ALLOWED_ORIGIN})).status,204);assert.equal((await call('/health',envBase)).status,200);});
test('admin statistics use real SQLite aggregate queries and count current IST logins',async()=>{
  const {adapter}=database(),env={...envBase,DB:adapter};await touchLogin(env,123,{first_name:'User'});await touchLogin(env,123,{});
  const token=await makeSession({email:'admin@example.test',exp:Math.floor(Date.now()/1000)+60},env.ADMIN_SESSION_SECRET);
  const r=await call('/api/users/stats',env,{token});assert.equal(r.status,200);const data=await r.json();assert.equal(data.total_users,1);assert.equal(data.today_logins,2);assert.equal(data.today_unique_users,1);
});
