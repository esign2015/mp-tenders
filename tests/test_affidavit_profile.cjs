const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('index.html','utf8');
const source=html.match(/<script id="affidavit-generator-script">([\s\S]*?)<\/script>/)[1];
const local=new Map(),nodes={};
const node=id=>nodes[id]??={value:'',hidden:false,textContent:'',innerHTML:'',style:{},classList:{values:new Set(),add(v){this.values.add(v)},remove(v){this.values.delete(v)},contains(v){return this.values.has(v)}},setAttribute(){},addEventListener(event,fn){this[event]=fn},scrollIntoView(){}};
let profileOpens=0;node('accountProfileBtn').onclick=()=>profileOpens++;
const row={'Tender ID':'TENDER-1',Title:'Work title',Organisation:'PWD',Department:'Dewas'};
const ctx=vm.createContext({Date,window:{dashboardVisitorProfile:{visitor_id:'USER-1',name:'Full Name',district:'Dewas'}},allTenders:[row],filteredTenders:[row],localStorage:{getItem:k=>local.get(k)||null,setItem:(k,v)=>local.set(k,v)},document:{getElementById:node,readyState:'complete',body:{style:{}},addEventListener(){}},escapeHtml:s=>String(s),alert:()=>{},navigator:{userAgent:''},clearTimeout(){},setTimeout(){}});
ctx.clean=v=>String(v??'').trim();
vm.runInContext(html.slice(html.indexOf('function validReferenceNumber('),html.indexOf('\nfunction extractReferenceFromTitle(')),ctx);
vm.runInContext(source,ctx);
ctx.window.dashboardAffidavitProfile={bidderName:'Full Name',firmName:'Firm',status:'Proprietor',place:'Kannod',relative:'no'};
ctx.window.openAffidavitForTender('TENDER-1');assert.equal(profileOpens,1);assert.equal(ctx.window.pendingAffidavitTenderId,'TENDER-1');assert(!node('affidavitModal').classList.contains('open'));
const complete={...ctx.window.dashboardAffidavitProfile,email:'bidder@example.test',parentName:'Parent Name',parentRelation:'D/o',address:'Ward 2, Kannod'};
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
// Letterhead uses the currently saved profile and omits absent contact fields.
ctx.window.dashboardVisitorProfile.mobile='+919876543210';ctx.window.dashboardVisitorProfile.email='bidder@example.test';
ctx.window.openAffidavitForTender('TENDER-1');
for(const text of ['Firm','Full Name | Proprietor','Updated Address','Mobile: +919876543210','Email: bidder@example.test'])assert(node('affidavitPreview').innerHTML.includes(text),text);
assert(node('affidavitSavedDetails').textContent.includes('letterhead'));
delete ctx.window.dashboardVisitorProfile.mobile;delete ctx.window.dashboardVisitorProfile.email;
ctx.window.openAffidavitForTender('TENDER-1');
assert(!node('affidavitPreview').innerHTML.includes('Mobile:'));
assert(node('affidavitPreview').innerHTML.includes('Email: bidder@example.test'));
const opensBefore=profileOpens;
ctx.window.dashboardAffidavitProfile={...complete,email:''};
ctx.window.openAffidavitForTender('TENDER-1');
assert.equal(profileOpens,opensBefore+1);
assert(node('accountSettingsStatus').textContent.includes('email'));
ctx.window.dashboardAffidavitProfile={...complete,address:'Updated Address'};
ctx.window.openAffidavitForTender('TENDER-1');
node('affSelectAffidavit').checked=false;node('affSelectAnnexureH').checked=true;node('affSelectAnnexureH').change();
assert(!node('affidavitPreview').innerHTML.includes('|| AFFIDAVIT ||'));
assert(node('affidavitPreview').innerHTML.includes('No Relation Certificate'));
assert(node('affidavitPreview').innerHTML.includes('Annexure - H'));
assert(!node('affidavitPreview').innerHTML.includes('DECLARATION / UNDERTAKING'));
assert(!node('affidavitPreview').innerHTML.includes('EPF'));
assert(!node('affidavitPreview').innerHTML.includes('ESIC'));
ctx.window.openAffidavitForTender('TENDER-1');
assert.equal(node('affSelectAffidavit').checked,false);
node('affSelectNoRelation').checked=false;node('affSelectAnnexureH').checked=false;node('affSelectAnnexureH').change();
assert(node('affidavitWord').disabled&&node('affidavitPdf').disabled);
node('affidavitPdf').click();
assert(node('affidavitStatus').textContent.includes('कम से कम एक'));
node('affSelectAffidavit').checked=true;node('affSelectNoRelation').checked=true;node('affSelectNoRelation').change();
// Reference follows the Tender ID inside the same bold, underlined field.
const sample={'Tender ID':'2026_MPPHC_538538_1','Reference Number':'NIT_21/2026-27_1',Title:'Repair of police station',Organisation:'MP Police Housing'};
ctx.allTenders.push(sample);
ctx.window.openAffidavitForTender(sample['Tender ID']);
assert(node('affidavitPreview').innerHTML.includes('notice inviting e-tender No. <strong><u>2026_MPPHC_538538_1 (ref no. NIT_21/2026-27_1)</u></strong>'));
for(const missing of ['',sample['Tender ID'],'NA']){
 sample['Reference Number']=missing;ctx.window.openAffidavitForTender(sample['Tender ID']);
 assert(!node('affidavitPreview').innerHTML.includes('(ref no.'));
}
ctx.window.openAffidavitForTender('TENDER-1');assert(!node('affidavitPreview').innerHTML.includes('(ref no.'));
console.log('PASS: incomplete profiles route to My Profile, saved profiles open Word/PDF options, parent/address survive legacy responses, editing resumes the selected tender and downloads do not resave basic details.');
console.log('PASS: certificate preview includes the saved profile letterhead and available mobile and required profile email; selected document previews and empty-choice protection work.');
console.log('PASS: affidavit shows the selected tender reference in bold/underline, omits missing references and does not reuse another tender reference.');
