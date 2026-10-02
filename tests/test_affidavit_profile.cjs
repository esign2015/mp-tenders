const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('index.html','utf8');
const source=html.match(/<script id="affidavit-generator-script">([\s\S]*?)<\/script>/)[1];
const local=new Map(),nodes={};
const node=id=>nodes[id]??={value:'',hidden:false,textContent:'',innerHTML:'',style:{},classList:{values:new Set(),add(v){this.values.add(v)},remove(v){this.values.delete(v)},contains(v){return this.values.has(v)}},setAttribute(){},addEventListener(event,fn){this[event]=fn},scrollIntoView(){}};
let profileOpens=0;node('accountProfileBtn').onclick=()=>profileOpens++;
const row={'Tender ID':'TENDER-1',Title:'Work title',Organisation:'PWD',Department:'Dewas'};
const ctx=vm.createContext({Date,window:{dashboardVisitorProfile:{visitor_id:'USER-1',name:'Full Name',district:'Dewas'}},allTenders:[row],filteredTenders:[row],localStorage:{getItem:k=>local.get(k)||null,setItem:(k,v)=>local.set(k,v)},document:{getElementById:node,readyState:'complete',body:{style:{}},addEventListener(){}},escapeHtml:s=>String(s),alert:()=>{},navigator:{userAgent:''},clearTimeout(){},setTimeout(){}});
vm.runInContext(source,ctx);
ctx.window.dashboardAffidavitProfile={bidderName:'Full Name',firmName:'Firm',status:'Proprietor',place:'Kannod',relative:'no'};
ctx.window.openAffidavitForTender('TENDER-1');assert.equal(profileOpens,1);assert.equal(ctx.window.pendingAffidavitTenderId,'TENDER-1');assert(!node('affidavitModal').classList.contains('open'));
const complete={...ctx.window.dashboardAffidavitProfile,parentName:'Parent Name',parentRelation:'D/o',address:'Ward 2, Kannod'};
ctx.window.cacheDashboardAffidavitProfile(complete);
// A legacy response must not erase previously saved parent/address details.
ctx.window.dashboardAffidavitProfile={bidderName:'Full Name',firmName:'Firm',status:'Proprietor',place:'Kannod',relative:'no'};
ctx.window.openAffidavitForTender('TENDER-1');assert.equal(profileOpens,1);assert(node('affidavitModal').classList.contains('download-mode'));
for(const [id,value] of [['affParentName','Parent Name'],['affParentRelation','D/o'],['affAddress','Ward 2, Kannod'],['affTenderRef','TENDER-1']])assert.equal(node(id).value,value);
assert.equal(node('affidavitDownloadSummary').hidden,false);
node('affNoticeDate').value='2026-10-01';node('affNoticeDate').change();ctx.window.openAffidavitForTender('TENDER-1');assert.equal(node('affNoticeDate').value,'2026-10-01');
node('affidavitEditProfile').click();assert.equal(profileOpens,2);assert(!node('affidavitModal').classList.contains('open'));assert.equal(ctx.window.pendingAffidavitTenderId,'TENDER-1');
// A deliberate edit saved on the server overrides an older browser value.
ctx.window.dashboardAffidavitProfileCanonical=true;ctx.window.dashboardAffidavitProfile={...complete,address:'Updated Address'};ctx.window.openAffidavitForTender('TENDER-1');assert.equal(node('affAddress').value,'Updated Address');
assert.equal((source.match(/if\(!validateDoc\(d\)\)return;saveNoticeDate\(\);/g)||[]).length,2);
console.log('PASS: incomplete profiles route to My Profile, saved profiles open Word/PDF options, parent/address survive legacy responses, editing resumes the selected tender and downloads do not resave basic details.');
