const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('index.html','utf8');
const functions=html.slice(html.indexOf('function runtimeDataUrls('),html.indexOf('\nlet liveRefreshTimer'));
const metadata=html.slice(html.indexOf('async function loadInventoryMetadata('),html.indexOf('\nwindow.addEventListener("beforeinstallprompt"'));
const calls=[];
const csv='Tender ID,Closing Date,Tender Count\nNEW-1,30-Oct-2026 05:00 PM,1\n';
const context=vm.createContext({AbortController,setTimeout,clearTimeout,Date,console,
 clean:v=>String(v??'').trim(),
 fetch:async(url,options)=>{
  calls.push({url,options});
  if(url.startsWith('https://raw.githubusercontent.com/'))throw Error('Primary host unreachable');
  assert(url.startsWith('https://api.github.com/repos/esign2015/mp-tenders/contents/'));
  assert(url.includes('ref=tender-data'));
  assert.equal(options.headers.Accept,'application/vnd.github.raw+json');
  return {ok:true,text:async()=>url.includes('live_snapshot.json')?JSON.stringify({tender_ids:['NEW-1']}):url.includes('inventory_counts.json')?JSON.stringify({snapshot_at:'2026-10-27T09:00:00Z'}):csv};
 }
});
vm.runInContext(html.slice(html.indexOf('function parseCsv('),html.indexOf('\nfunction runtimeDataUrls(')),context);
vm.runInContext(functions+'\n'+metadata,context);
(async()=>{
 assert.equal((await context.loadFromCsv())[0]['Tender ID'],'NEW-1');
 assert.equal((await context.loadOrganisationTenderCsv())[0]['Tender ID'],'NEW-1');
 assert.equal(await context.loadPortalTenderCountTotal(),1);
 assert.equal((await context.loadLiveSnapshot()).tender_ids[0],'NEW-1');
 assert.equal((await context.loadInventoryMetadata()).snapshot_at,'2026-10-27T09:00:00Z');
 assert.equal(calls.length,10);
 context.fetch=async()=>{throw Error('Both hosts unreachable')};
 await assert.rejects(context.loadFromCsv(),/unreachable/);
 await assert.rejects(context.loadOrganisationTenderCsv(),/unreachable/);
 console.log('PASS: primary host failure uses current tender-data for all five sources; dual failure never returns frozen main-branch CSVs.');
})().catch(e=>{console.error(e);process.exitCode=1});
