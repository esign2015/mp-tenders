const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('data/index.html','utf8');
for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
const source=html.slice(html.indexOf('const progressRoot='),html.indexOf("document.getElementById('refresh').onclick"));
const nodes={};const document={getElementById:id=>nodes[id]??={textContent:'',innerHTML:'',style:{}}};
const inventory={policy_version:'official-portal-v1',portal_tender_count:5312,copied_tenders:5312,detail_complete:1399,detail_pending:3913,detail_failed:0,detail_skipped:0,organisation_count:92,department_count:8,known_pincode_count:700,missing_pincode_count:0,missing_district_count:0};
let fail=false;
const ctx=vm.createContext({document,location:{origin:'https://example.org'},fetch:async url=>({ok:!(fail&&url.includes('inventory_counts.json')),json:async()=>url.includes('inventory_counts.json')?inventory:url.includes('status.json')?{total_tenders:9999,detail_complete:9999,detail_remaining:9999,status:'completed',updated_at:new Date().toISOString()}:url.includes('evening_verification')?{detail_success:9999,detail_pending:9999}:url.includes('organisation_changes')?{current_tender_count:9999}:{}})});
vm.runInContext(source,ctx);
(async()=>{
 await ctx.load();assert.equal(nodes.refreshTime.textContent.startsWith('Last refresh:'),true);
 for(const target of ['status','process','quality','changes'])assert(!nodes[target].innerHTML.includes('9,999'),target+' must ignore historical counts');
 assert(nodes.status.innerHTML.includes('1,399'));assert(nodes.status.innerHTML.includes('3,913'));assert(nodes.quality.innerHTML.includes('Pincode Missing'));assert(nodes.quality.innerHTML.includes('District Missing'));
 const previous=nodes.status.innerHTML;fail=true;await ctx.load();assert.equal(nodes.refreshTime.textContent,'Refresh error');assert.equal(nodes.status.innerHTML,previous);
 console.log('PASS: /data uses current summary throughout and retains last counts on summary failure.');
})().catch(e=>{console.error(e);process.exitCode=1});
