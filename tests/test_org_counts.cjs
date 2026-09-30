const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('org/index.html','utf8');
const script=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)][0][1];
new vm.Script(script);
const nodes={};const document={getElementById:id=>nodes[id]??=( {value:'',textContent:'',innerHTML:'',addEventListener(){}} )};
const snapshot={policy_version:'official-portal-v1',organisation_count:2,portal_tender_count:5,copied_tenders:5,detail_complete:2,detail_failed:0,detail_skipped:0,detail_pending:3,organisations:[{organisation:'UAD',serial:1,portal_tender_count:4,copied_tenders:4,detail_complete:2,detail_failed:0,detail_skipped:0,detail_pending:2},{organisation:'Other',serial:2,portal_tender_count:1,copied_tenders:1,detail_complete:0,detail_failed:0,detail_skipped:0,detail_pending:1}]};
const ctx=vm.createContext({document,console,setInterval(){},fetch:async url=>{assert(url.includes('inventory_counts.json'));return{ok:true,json:async()=>snapshot}}});
vm.runInContext(script.slice(0,script.indexOf("$('search').addEventListener")),ctx);
(async()=>{
 await ctx.load();assert.equal(nodes.copiedCount.textContent,'5 / 2');assert(nodes.body.innerHTML.includes('UAD'));assert(nodes.body.innerHTML.includes('Other'));
 nodes.search.value='UAD';ctx.render();assert(!nodes.body.innerHTML.includes('Other'));assert.equal(nodes.copiedCount.textContent,'5 / 2');assert(nodes.body.innerHTML.includes('कुल — सभी organisations'));
 console.log('PASS: /org uses one summary and keeps global counts stable when filtering.');
})().catch(e=>{console.error(e);process.exitCode=1});
