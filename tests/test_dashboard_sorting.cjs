const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('index.html','utf8');
for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(m[1]);
// Read actual dashboard functions, bounded by the next top-level function.
function source(name){
  const start = html.indexOf('function '+name+'(');
  assert(start>=0,name);
  const end = html.indexOf('\nfunction ',start+10);
  return html.slice(start,end);
}
const controls = new Proxy({}, {get:(o,k)=>o[k] ||= {value:''}});
const context = vm.createContext({console,Date,Set,Map,requestAnimationFrame:fn=>fn(),document:{querySelectorAll:()=>[]},
  $:id=>controls[id], getDistrictInfo:r=>({name:r.District||''}),
  renderCurrentView(){},applyColumnVisibility(){},updateVirtualPager(){},updateCounts(){},
  normalizeSearchText:s=>s, archivedMode:false, quickFilterMode:'',tableSortKey:'',tableSortDesc:false,
  portalSnapshot:null,portalActiveIds:new Set(),VIRTUAL_CHUNK_SIZE:100, ESTIMATED_ROW_HEIGHT:40});
const names=['clean','dedupeTenderRows','mergeCurrentPortalRows','currentPortalAllows','parseDate','istParts','todayKey',
  'isDeadlineAlertActive',
  'formatMoney','formatPdfAmount','formatTimeLeft','getTimeLeftClass','updateVisibleTimeLeft','nextDashboardRefreshAt',
  'dateKey','moneyNumber','portalFeeMissing','effectiveProcessingFee','tenderFeeWithPortal','totalFee',
  'cleanDisplayTitle','splitOrganisationChain','validReferenceNumber','extractReferenceFromTitle',
  'normaliseCorrigendumValue','normaliseTender','parseCsv','goVirtualPage','moneySortValue','compareTableValues',
  'updateTableSortHeaders','sortCurrentTable','applyFilters','renderTable',
  'clearFilters','showLiveTenders','showClosingToday','showClosingTomorrow','showNextThreeDays'];
// Some functions have declarations between them; only include their exact body
// by taking the shortest prefix that compiles as a complete function.
for(const name of names){
 const text=source(name); let loaded=false;
 for(let i=text.indexOf('{')+1;i<=text.length;i++) if(text[i-1]==='}'){
  try { new vm.Script(text.slice(0,i)); } catch {continue;}
  vm.runInContext(text.slice(0,i),context);loaded=true;break;
 }
 assert(loaded,name);
}
const alertNow=Date.now();
assert.equal(context.isDeadlineAlertActive(alertNow+10*60*1000,alertNow),true);
assert.equal(context.isDeadlineAlertActive(alertNow+10*60*1000+1,alertNow),false);
assert.equal(context.isDeadlineAlertActive(alertNow+1,alertNow),true);
assert.equal(context.isDeadlineAlertActive(alertNow,alertNow),false);
assert.equal(context.isDeadlineAlertActive('invalid',alertNow),false);
for(const [now,next] of [
 ['2026-09-30T08:59:00+05:30','2026-09-30T09:00:30+05:30'],
 ['2026-09-30T09:00:29+05:30','2026-09-30T09:00:30+05:30'],
 ['2026-09-30T09:00:30+05:30','2026-09-30T09:15:30+05:30'],
 ['2026-09-30T09:15:31+05:30','2026-09-30T09:30:30+05:30'],
 ['2026-09-30T18:59:59+05:30','2026-09-30T19:00:30+05:30'],
 ['2026-09-30T19:00:30+05:30','2026-10-01T09:00:30+05:30']
]) assert.equal(context.nextDashboardRefreshAt(Date.parse(now)),Date.parse(next));
let clockNow=Date.parse('2026-09-30T09:00:00+05:30');
context.Date=class extends Date{static now(){return clockNow;}};
const countdowns=['tender-card-time','time-left'].map(type=>({dataset:{timeLeft:'30-Sep-2026 09:02 AM'},textContent:'',classList:classList([type])}));
context.document.querySelectorAll=selector=>selector==='[data-time-left]'?countdowns:[];
context.updateVisibleTimeLeft();
assert(countdowns.every(x=>x.textContent==='2m 0s'));
assert(countdowns.every(x=>x.classList.contains('countdown-deadline-blink')));
clockNow+=1000;
context.updateVisibleTimeLeft();
assert(countdowns.every(x=>x.textContent==='1m 59s'));
assert(countdowns[1].classList.contains('time-left-urgent'));
for(const node of countdowns)node.dataset.timeLeft='01-Oct-2026 10:02 AM';
context.updateVisibleTimeLeft();
assert(countdowns.every(x=>x.textContent==='1d 1h 1m 59s'&&!x.classList.contains('countdown-deadline-blink')));
clockNow+=1000;context.updateVisibleTimeLeft();
assert(countdowns.every(x=>x.textContent==='1d 1h 1m 58s'));
for(const node of countdowns)node.dataset.timeLeft='30-Sep-2026 09:00 AM';
context.updateVisibleTimeLeft();
assert(countdowns.every(x=>x.textContent==='Closed'&&!x.classList.contains('countdown-deadline-blink')));
assert(countdowns[1].classList.contains('time-left-closed'));
for(const node of countdowns)node.dataset.timeLeft='invalid';
context.updateVisibleTimeLeft();
assert(countdowns.every(x=>x.textContent==='—'&&!x.classList.contains('countdown-deadline-blink')));
assert(html.includes('.countdown-deadline-blink{animation:tender-deadline-blink 2s'));
assert(!/\.tender-card\.closing-final-minutes\{[^}]*animation/.test(html));
assert(!html.includes('setInterval(updateVisibleTimeLeft,20000)'));
assert(/setInterval\(\(\) => \{\s*updateCardDeadlineAlerts\(\);\s*updateVisibleTimeLeft\(\);[\s\S]*?\},1000\);/.test(html));
console.log('PASS: both view countdowns update every second, display seconds beyond a day, blink only the timer for final ten minutes and stop after expiry.');
context.Date=Date;
context.document.querySelectorAll=()=>[];
for(const [id,ref] of [['2026_MPCDF_536489_1','1666/JSDSM/2026/Jabalpur'],['2026_MPTAX_534364_2','CTD/DC-2/STORE/2026/393']]){
 const rows=context.mergeCurrentPortalRows([{'Tender ID':id,'Reference Number':ref}], [{'Tender ID':id,'Reference Number':id}]);
 assert.equal(rows[0]['Reference Number'],ref);
 const missing=context.normaliseTender({'Tender ID':id,'Reference Number':id});
 assert.equal(missing['Reference Number'],'');
}
const master=context.parseCsv(fs.readFileSync('all_tenders_org_detailed.csv','utf8'));
const portal=context.parseCsv(fs.readFileSync('organisation_tenders.csv','utf8'));
const merged=context.mergeCurrentPortalRows(master,portal);
assert(merged.every(r=>r['Reference Number'] !== r['Tender ID']));
context.allTenders=Array.from({length:250},(_,i)=>({'Tender ID':`2026_TEST_${i}_1`,Title:`Work ${250-i}`,
  'Reference Number':`REF/${250-i}`,'Closing Date':'01-Jan-2099 06:00 PM',
  'PAC Amount':String(250-i),District:`District ${250-i}`,'Published Date':`${(i%28)+1}-Sep-2026 01:00 PM`}));
const keys = [...html.matchAll(/data-sort-key="([^"]+)"/g)].map(m=>m[1]);
for(const key of keys){
 context.filteredTenders=[...context.allTenders];
 context.tableSortKey='';context.tableSortDesc=false;
 context.sortCurrentTable(key);
 assert.equal(context.tableSortDesc,false);
 assert.equal(context.virtualRows.length,250);
 assert.equal(context.virtualEnd,100);
 const expected=[...context.allTenders].sort((a,b)=>context.compareTableValues(a,b,key));
 assert.equal(context.virtualRows[0]['Tender ID'],expected[0]['Tender ID']);
 assert.equal(context.virtualRows[249]['Tender ID'],expected[249]['Tender ID']);
 context.sortCurrentTable(key);
 assert.equal(context.tableSortDesc,true);
 const descending=[...context.allTenders].sort((a,b)=>-context.compareTableValues(a,b,key));
 assert.equal(context.virtualRows[0]['Tender ID'],descending[0]['Tender ID']);
 assert.equal(context.virtualRows[249]['Tender ID'],descending[249]['Tender ID']);
 context.applyFilters();
 assert.equal(context.tableSortKey,key);
}
assert.equal(context.moneySortValue('NA'),null);
assert(context.compareTableValues({'Published Date':'01-Jan-2027'},{'Published Date':'30-Dec-2026'},'Published Date')>0);
console.log('PASS: script syntax, both references, CSV merge, complete 250-row ascending/descending sort before 100-row window, sort persistence, chronological dates.');

assert(!html.includes('id="archivedBtn"'));
for(const view of ['table','card']){
 context.tenderViewMode=view;
 context.tableSortKey='Title';context.tableSortDesc=false;
 context.applyFilters();
 context.goVirtualPage(1);
 assert.equal(context.virtualStart,100);
 for(let i=0;i<5;i++) context.applyFilters(false,true);
 assert.equal(context.virtualStart,100,view+' periodic expiry/refresh preserves second page');
 context.goVirtualPage(1);
 assert.equal(context.virtualStart,200);
 assert.equal(context.virtualEnd,250);
 context.goVirtualPage(-1);
 assert.equal(context.virtualStart,100);
 context.allTenders[0]['Closing Date']='01-Jan-2000 06:00 PM';
 context.applyFilters(false,true);
 assert.equal(context.virtualStart,100);
 assert(!context.virtualRows.some(r=>r['Tender ID']==='2026_TEST_0_1'));
 context.allTenders[0]['Closing Date']='01-Jan-2099 06:00 PM';
}
console.log('PASS: both views preserve pages on repeated refresh/expiry; Next 100 final partial page and expired row removal.');
const eveningNow=Date.parse('2026-09-30T20:25:00+05:30');
context.portalSnapshot={verified:true,snapshot_at:'2026-09-30T19:51:00+05:30'};
context.portalActiveIds=new Set(['present']);
assert.equal(context.currentPortalAllows({'Tender ID':'present'},eveningNow),true);
assert.equal(context.currentPortalAllows({'Tender ID':'old-import'},eveningNow),false);
context.portalSnapshot.verified=false;
assert.equal(context.currentPortalAllows({'Tender ID':'old-import'},eveningNow),true);
context.portalSnapshot={verified:true,snapshot_at:'2026-09-29T19:51:00+05:30'};
assert.equal(context.currentPortalAllows({'Tender ID':'old-import'},eveningNow),false);
for(const time of ['2026-10-01T00:00:01+05:30','2026-10-01T08:59:59+05:30','2026-10-01T09:00:00+05:30'])assert.equal(context.currentPortalAllows({'Tender ID':'old-import'},Date.parse(time)),false);
context.portalSnapshot={verified:true,snapshot_at:'2026-10-01T00:32:00+05:30'};assert.equal(context.currentPortalAllows({'Tender ID':'old-import'},Date.parse('2026-10-01T00:35:00+05:30')),false);
context.portalSnapshot={verified:true,snapshot_at:'2026-10-01T10:00:00+05:30'};context.portalActiveIds.add('new-official');assert.equal(context.currentPortalAllows({'Tender ID':'new-official'},Date.parse('2026-10-01T10:01:00+05:30')),true);
context.portalSnapshot=null;
console.log('PASS: verified inventory remains authoritative through midnight and 09:00; only new verified IDs enter Live.');

for(const value of ['1000','1000.0','1000.00']){
 assert.equal(context.formatMoney(value),'₹ 1,000.00');
 assert.equal(context.formatPdfAmount(value),'1,000.00');
}
assert.equal(context.formatMoney('NA'),'NA');
assert.equal(context.formatMoney(''),'—');
assert.equal(context.formatMoney('0'),'₹ 0.00');
assert.equal(context.formatMoney('123.4'),'₹ 123.40');

// Quick count buttons must use the same verified live membership as counts,
// and preserve their date filter when periodic refresh reapplies filters.
const fixedNow=Date.parse('2026-09-30T20:25:00+05:30');
context.Date=class extends Date {
 constructor(...args){super(...(args.length ? args : [fixedNow]));}
 static now(){return fixedNow;}
};
context.portalSnapshot={verified:true,snapshot_at:'2026-09-30T19:51:00+05:30'};
context.portalActiveIds=new Set(['tomorrow-live','tomorrow-cancelled','today-live','expired']);
context.allTenders=[
 {'Tender ID':'tomorrow-live','Closing Date':'01-Oct-2026 03:00 PM'},
 {'Tender ID':'old-master-only','Closing Date':'01-Oct-2026 03:00 PM'},
 {'Tender ID':'tomorrow-cancelled','Closing Date':'01-Oct-2026 03:00 PM',Status:'Cancelled'},
 {'Tender ID':'today-live','Closing Date':'30-Sep-2026 10:00 PM'},
 {'Tender ID':'expired','Closing Date':'30-Sep-2026 03:00 PM'}
];
context.getStatusText=()=> 'Open';
for(const [fn,expected] of [
 ['showClosingTomorrow',['tomorrow-live']],
 ['showClosingToday',['today-live']],
 ['showLiveTenders',['today-live','tomorrow-live']],
 ['showNextThreeDays',['today-live','tomorrow-live']]
]){
 context[fn]();
 assert.deepEqual(Array.from(context.filteredTenders,r=>r['Tender ID']).sort(),expected.slice().sort(),fn);
 context.applyFilters(false,true);
 assert.deepEqual(Array.from(context.filteredTenders,r=>r['Tender ID']).sort(),expected.slice().sort(),fn+' after refresh');
}
console.log('PASS: all quick buttons reject old master-only, cancelled and expired rows; tomorrow selection survives refresh.');

// A select with only a blank option silently discarded values like above:5000000.
assert.match(html,/<input type="hidden" id="pacFilter" value="">/);
assert(!html.includes('<select id="pacFilter"'));
context.portalSnapshot=null;
for(const threshold of [500000,1000000,2000000,5000000,10000000,20000000,50000000,100000000,200000000]){
 context.allTenders=[threshold-1,threshold,threshold+1,'NA','',0].map((amount,i)=>({
  'Tender ID':'pac-'+i,'Closing Date':'01-Oct-2026 03:00 PM','PAC Amount':String(amount)
 }));
 for(const mode of ['above','below']){
  context.clearFilters();
  controls.pacFilter.value=mode+':'+threshold;
  context.applyFilters();
  const expected=mode==='above' ? ['pac-2'] : ['pac-0','pac-5'];
  assert.deepEqual(Array.from(context.filteredTenders,r=>r['Tender ID']).sort(),expected,mode+' '+threshold);
  context.applyFilters(false,true);
  assert.deepEqual(Array.from(context.filteredTenders,r=>r['Tender ID']).sort(),expected,'PAC persists on refresh');
 }
}
console.log('PASS: all nine PAC thresholds, strict Above/Below boundaries, missing/NA exclusion and refresh persistence.');

// Exercise the actual renderer lookup with exact PIN mapping and shared prefixes.
vm.runInContext(source('getDistrictInfo'),context);
context.districtInfoCache=new Map();
context.MP_DISTRICT_MASTER=JSON.parse(fs.readFileSync('data/mp_districts.json','utf8')).districts;
context.PIN_DISTRICTS=JSON.parse(fs.readFileSync('data/pincode_districts.json','utf8')).districts_by_pin;
for(const [pin,district] of [['461228','Harda'],['461331','Harda'],['461441','Harda'],['484552','Umaria']]){
 assert.equal(context.getDistrictInfo({Pincode:pin}).name,district);
}
assert.equal(context.getDistrictInfo({Pincode:'484224',Location:'Anuppur',Organisation:'Shahdol Division'}).name,'Anuppur');
console.log('PASS: exact PIN districts and location before organisation names, including Timarni/Harda.');

// Click the real preference handlers; declarations must survive quick-filter edits.
const prefs = new Map();
function classList(initial=[]){
 const values=new Set(initial);
 return {contains:x=>values.has(x),add:x=>values.add(x),remove:x=>values.delete(x),
  toggle(x,on){if(on===undefined)on=!values.has(x);if(on)values.add(x);else values.delete(x);return on;}};
}
const bodyClasses=classList(['org-display-full']);
const checkboxes=Array.from({length:24},(_,i)=>({dataset:{col:String(i+1)},checked:i<14}));
const cells=Array.from({length:24},()=>({classList:classList(),style:{},colSpan:1}));
const placeholder={classList:classList(),style:{},colSpan:24};
const menu={style:{},classList:classList(),querySelectorAll:()=>checkboxes,contains:()=>false};
const buttons={columnBtn:{getBoundingClientRect:()=>({left:100,bottom:300})},orgDisplayBtn:{},userPrefMenu:menu,resetColumnsBtn:{},viewToggleBtn:{},tenderTable:{style:{}},tableWrap:{clientWidth:1100,scrollWidth:0},tableScrollTopInner:{style:{}}};
let prefRenders=0;
const prefContext=vm.createContext({console,Map,Set,Number,JSON,Math,
 $:id=>buttons[id],localStorage:{getItem:k=>prefs.get(k)??null,setItem:(k,v)=>prefs.set(k,v),removeItem:k=>prefs.delete(k)},
 document:{body:{classList:bodyClasses},addEventListener(){},querySelectorAll:()=>[{children:cells},{children:[placeholder]}]},
 window:{innerWidth:1200,innerHeight:900,addEventListener(){}},escapeHtml:s=>s,
 tenderViewMode:'table',filteredTenders:[],applyTenderView(){},setTenderView(){},renderTable(){prefRenders++;}});
const declarationStart=html.indexOf('let orgDisplayMode =');
assert(declarationStart>=0);
vm.runInContext(html.slice(declarationStart,html.indexOf('function loadHiddenColumns()',declarationStart)),prefContext);
for(const name of ['loadHiddenColumns','applyColumnVisibility','openColumnMenu','setupUserFeatures']) vm.runInContext(source(name),prefContext);
prefContext.setupUserFeatures();
buttons.columnBtn.onclick();
assert(menu.classList.contains('open'));
assert.equal(checkboxes.length,24);
checkboxes[3].checked=false;checkboxes[3].onchange();
assert(cells[3].classList.contains('column-hidden'));
assert.equal(JSON.parse(prefs.get('mp_tender_hidden_columns_v3')).includes(4),true);
buttons.resetColumnsBtn.onclick();assert(!cells[3].classList.contains('column-hidden'));
buttons.orgDisplayBtn.onclick();assert(bodyClasses.contains('org-display-short'));assert.equal(buttons.orgDisplayBtn.textContent,'🏢 Short Org');
buttons.orgDisplayBtn.onclick();assert(bodyClasses.contains('org-display-full'));assert.equal(buttons.orgDisplayBtn.textContent,'🏢 Full Org');
assert.equal(prefRenders,2);
console.log('PASS: Columns opens, selection persists, Reset restores columns, Full/Short Org toggles twice without errors.');

// Showing optional detail columns reserves their width instead of crushing them.
const defaultWidth=parseInt(buttons.tenderTable.style.minWidth);
prefs.set('mp_tender_hidden_columns_v3','[]');prefContext.applyColumnVisibility();
assert(parseInt(buttons.tenderTable.style.minWidth)>defaultWidth+1500);
assert.equal(parseInt(buttons.tenderTable.style.width),cells.reduce((sum,cell)=>sum+parseInt(cell.style.width),0));
assert(cells.slice(16).every(cell=>parseInt(cell.style.minWidth)>=130));
assert.equal(buttons.tableScrollTopInner.style.width,buttons.tenderTable.style.width);
const allWidth=parseInt(buttons.tenderTable.style.width);
prefs.set('mp_tender_hidden_columns_v3','[17]');prefContext.applyColumnVisibility();
assert.equal(parseInt(buttons.tenderTable.style.width),allWidth-parseInt(cells[16].style.width));
assert.equal(placeholder.style.width,undefined);
assert.match(html,/#tenderTable \.table-detail-text\{[^}]*-webkit-line-clamp:3;[^}]*overflow:hidden/);
assert(html.includes('title="${value}"><span class="table-detail-text">${value}</span>'));
console.log('PASS: advanced column widths survive selection changes, scrollbars stay aligned, long text clamps to three lines with full hover text.');

// Alert placements count tenders, including across the 100-row page boundary.
const cardNodes={tenderCardGrid:{innerHTML:''},recordCount:{}};
const cardRows=Array.from({length:130},(_,i)=>({'Tender ID':'T'+(i+1)}));
const cardContext=vm.createContext({virtualRows:cardRows,virtualStart:0,virtualEnd:100,CARD_AD_INTERVAL:12,CARD_ALERT_INTERVAL:20,VIRTUAL_CHUNK_SIZE:100,currentLanguage:'en',
 $:id=>cardNodes[id],escapeHtml:s=>String(s||''),displayTenderValue:(r,k)=>r[k],getDistrictInfo:()=>({name:'Dewas'}),
 cardClosingClass:()=>'',isDeadlineAlertActive:()=>false,parseDate:()=>null,formatTimeLeft:()=>'',formatMoney:()=>'',verifiedTenderFeeText:()=>'',verifiedTotalFeeText:()=>'',formatClosingDateTime:()=>'',whatsappShareUrl:()=>'',stableTenderUrl:()=>''});
for(const name of ['tenderAlertsCard','sarAdCard','renderCardWindow'])vm.runInContext(source(name),cardContext);
function cardOrder(){return [...cardNodes.tenderCardGrid.innerHTML.matchAll(/<article class="tender-card [^"]*"|<aside class="(tender-alert-card|sar-ad-card)"/g)].map(m=>m[1]||'tender')}
function alertAfter(){let tenders=cardContext.virtualStart;return cardOrder().flatMap(type=>{if(type==='tender')tenders++;return type==='tender-alert-card'?[tenders]:[]})}
cardContext.renderCardWindow();
assert.equal(cardOrder()[3],'tender-alert-card');assert.equal(cardOrder().filter(x=>x==='tender').length,100);
assert.deepEqual(alertAfter(),[3,20,40,60,80,100]);assert.equal(cardOrder().filter(x=>x==='sar-ad-card').length,8);
assert(cardNodes.tenderCardGrid.innerHTML.includes('href="https://t.me/mptendersalert"'));
assert(cardNodes.tenderCardGrid.innerHTML.includes('href="https://chat.whatsapp.com/IySc5P5Q1X79AxKpQCgMyQ"'));
cardContext.virtualStart=100;cardContext.virtualEnd=130;cardContext.renderCardWindow();assert.deepEqual(alertAfter(),[120]);
cardContext.virtualStart=0;cardContext.virtualEnd=3;cardContext.renderCardWindow();assert.deepEqual(alertAfter(),[3]);
cardContext.virtualEnd=2;cardContext.renderCardWindow();assert.deepEqual(alertAfter(),[]);
cardContext.virtualEnd=0;cardContext.renderCardWindow();assert(cardNodes.tenderCardGrid.innerHTML.includes('No tender records found.'));assert.deepEqual(alertAfter(),[]);
console.log('PASS: community alert card is fourth, repeats after every 20 tenders across pages, preserves service ads and uses the correct join links.');

assert.equal(context.formatMoney('Not provided on portal'),'Not provided on portal');
assert.equal(context.formatMoney('Not available (processing fee not published)'),'Not available');
assert.equal(context.effectiveProcessingFee({'Processing Fee':'Not provided on portal','Total Fee':'1295','Tender Fee':'1000','EMD Fee':'0'}),0);
