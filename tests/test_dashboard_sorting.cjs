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
  VIRTUAL_CHUNK_SIZE:100, ESTIMATED_ROW_HEIGHT:40});
const names=['clean','dedupeTenderRows','mergeCurrentPortalRows','parseDate','istParts','todayKey',
  'isDeadlineAlertActive',
  'dateKey','moneyNumber','effectiveProcessingFee','tenderFeeWithPortal','totalFee',
  'cleanDisplayTitle','splitOrganisationChain','validReferenceNumber','extractReferenceFromTitle',
  'normaliseCorrigendumValue','normaliseTender','parseCsv','goVirtualPage','moneySortValue','compareTableValues',
  'updateTableSortHeaders','sortCurrentTable','applyFilters','renderTable'];
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
assert.equal(context.isDeadlineAlertActive(alertNow+15*60*1000,alertNow),true);
assert.equal(context.isDeadlineAlertActive(alertNow+15*60*1000+1,alertNow),false);
assert.equal(context.isDeadlineAlertActive(alertNow+1,alertNow),true);
assert.equal(context.isDeadlineAlertActive(alertNow,alertNow),false);
assert.equal(context.isDeadlineAlertActive('invalid',alertNow),false);
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
