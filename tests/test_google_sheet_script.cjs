const fs=require('fs'),vm=require('vm'),crypto=require('crypto'),assert=require('assert/strict');
const secret='s'.repeat(48),tabs=new Map();
class Sheet{
 constructor(name){this.name=name;this.rows=[]}
 getName(){return this.name}getLastRow(){return this.rows.length}
 getRange(row,col,height,width){const s=this;return{
  setValues(values){values.forEach((values,i)=>{s.rows[row-1+i]??=[];values.forEach((value,j)=>s.rows[row-1+i][col-1+j]=typeof value==='string'&&value.startsWith("'")?value.slice(1):value)});return this},
  getValues(){return Array.from({length:height},(_,i)=>Array.from({length:width},(_,j)=>s.rows[row-1+i]?.[col-1+j]??''))},
  getDisplayValues(){return this.getValues().map(row=>row.map(String))},setNumberFormat(){return this},setFontWeight(){return this},setBackground(){return this}
 }}
 setFrozenRows(){}autoResizeColumns(){}
}
const book={getSheetByName:name=>tabs.get(name),insertSheet:name=>{const s=new Sheet(name);tabs.set(name,s);return s}};
const ctx=vm.createContext({Date,console,SpreadsheetApp:{openById:id=>{assert.equal(id,'1VHILTCBB-CR0srqOTmaxf0b17wWJCpaOuMpVp_KphKw');return book},flush(){}},
 Utilities:{getUuid:()=>crypto.randomUUID(),formatDate:()=>new Date().toISOString(),Charset:{UTF_8:'utf8'},computeHmacSha256Signature:(text,key)=>[...crypto.createHmac('sha256',key).update(text).digest()]},
 PropertiesService:{getScriptProperties:()=>({getProperty:()=>secret})},
 LockService:{getScriptLock:()=>({held:false,waitLock(){this.held=true},hasLock(){return this.held},releaseLock(){this.held=false}})},
 ContentService:{MimeType:{JSON:'json'},createTextOutput:text=>({text,setMimeType(){return this}})}});
vm.runInContext(fs.readFileSync('integrations/google-sheets/Code.gs','utf8'),ctx);
function envelope(action,fields={}){
 const payload=JSON.stringify({action,spreadsheet_id:'1VHILTCBB-CR0srqOTmaxf0b17wWJCpaOuMpVp_KphKw',request_id:crypto.randomUUID(),...fields});
 const timestamp=String(Math.floor(Date.now()/1000));return{timestamp,payload,signature:crypto.createHmac('sha256',secret).update(timestamp+'\n'+payload).digest('hex')};
}
function send(e){return JSON.parse(ctx.doPost({postData:{contents:JSON.stringify(e)}}).text)}
function call(action,fields){return send(envelope(action,fields))}
const first=crypto.randomUUID(),second=crypto.randomUUID();
const registration={visitor_id:first,name:'Test user',mobile:'+919876543210',district:'Dewas'};
assert(call('register',registration).ok);assert(call('register',registration).ok);
assert.equal(tabs.get('Users').rows.length,2);assert.equal(tabs.get('VisitorSessions').rows.length,2);assert.equal(tabs.get('VisitEvents').rows.length,2);
assert.equal(call('register',{...registration,name:'Changed'}).status,409);
const profile={bidderName:'Test user',firmName:'=SUM(A1)',status:'Proprietor',place:'Dewas',relative:'no',relativeName:'',relativePost:'',relativePosting:''};
assert.deepEqual(call('save_affidavit',{visitor_id:first,profile}).profile,profile);
assert.deepEqual(call('session',{visitor_id:first}).affidavit_profile,profile);
assert(call('register',{...registration,visitor_id:second,name:'New browser'}).ok);
assert.equal(tabs.get('Users').rows.length,2,'one unique mobile contact');
assert.deepEqual(call('read_affidavit',{visitor_id:second}).profile,{},'mobile alone must not expose another browser profile');
assert.deepEqual(call('read_affidavit',{visitor_id:first}).profile,profile);
const before=call('list_visitors').visitors.find(v=>v.visitor_id===first).visit_count;
const retry=envelope('session',{visitor_id:first});assert(send(retry).ok);assert(send(retry).ok);
assert.equal(Number(call('list_visitors').visitors.find(v=>v.visitor_id===first).visit_count),Number(before)+1,'retry must not count twice');
const bad=envelope('status');bad.signature='bad';assert.equal(send(bad).status,401);
const expired=envelope('status');expired.timestamp=String(Number(expired.timestamp)-1000);expired.signature=crypto.createHmac('sha256',secret).update(expired.timestamp+'\n'+expired.payload).digest('hex');assert.equal(send(expired).status,401);
assert.equal(call('read_affidavit',{visitor_id:crypto.randomUUID()}).status,401);
const original=[...tabs.get('Users').rows[0]];tabs.get('Users').rows[0][0]='Existing custom data';assert.equal(call('status').status,503);assert.equal(tabs.get('Users').rows[0][0],'Existing custom data');tabs.get('Users').rows[0]=original;
console.log('PASS: authenticated requests, unique mobile contacts, isolated affidavit profiles, idempotent retries and existing header protection.');
const accountId=crypto.randomUUID(),record={user_id:accountId,mobile:'+919123456789',name:'Account user',district:'Dewas',password_hash:'scrypt:example$test$hash',sessions:[],reset:null};
const created=call('account_create',{record});assert(created.ok);assert.equal(created.record.revision,1);
assert.equal(call('account_create',{record:{...record,user_id:crypto.randomUUID()}}).status,409);
assert.equal(call('account_lookup',{mobile:record.mobile}).record.user_id,accountId);
const update={...created.record,sessions:[{hash:'hashed-token',expires:Date.now()/1000+900}]};
assert(call('account_update',{record:update,expected_revision:1}).updated);
assert(!call('account_update',{record:{...update,password_hash:'should-not-overwrite'},expected_revision:1}).updated);
assert.equal(call('account_get',{user_id:accountId}).record.password_hash,record.password_hash);
const key='a'.repeat(64);for(let i=1;i<=13;i++)assert.equal(call('account_rate',{key,limit:12}).allowed,i<=12);
console.log('PASS: one password account per phone, compare-and-swap guards concurrent changes and persistent login rate limit.');

const current=call('account_get',{user_id:accountId}).record;
const verified={...current,mobile_verified:'Yes',first_name:'Account',middle_name:'',last_name:'User',gender:'Male',name:'Account User',district:'Harda'};
assert(call('account_update',{record:verified,expected_revision:current.revision}).updated);
let contact=tabs.get('Users').rows.find(row=>row[2]===record.mobile);
assert.equal(contact[7],'Yes');assert.equal(contact[8],'Account');assert.equal(contact[10],'User');assert.equal(contact[11],'Male');assert.equal(contact[3],'Harda');
assert.equal(call('list_visitors').visitors.find(row=>row.visitor_id===accountId).mobile_verified,'Yes');
const restored=call('account_get',{user_id:accountId}).record;
assert(call('account_update',{record:{...restored,mobile_verified:'No'},expected_revision:restored.revision}).updated);
assert.equal(tabs.get('Users').rows.find(row=>row[2]===record.mobile)[7],'Yes','normal session updates cannot downgrade manual verification');
const savedHeaders=[...tabs.get('Users').rows[0]],savedUser=[...tabs.get('Users').rows[1]];
tabs.get('Users').rows[0]=savedHeaders.slice(0,8);assert(call('status').ok);assert.deepEqual(tabs.get('Users').rows[0],savedHeaders);assert.deepEqual(tabs.get('Users').rows[1],savedUser);
console.log('PASS: manual verification and separate name/gender fields sync to contacts/export; old headers migrate without losing users.');
