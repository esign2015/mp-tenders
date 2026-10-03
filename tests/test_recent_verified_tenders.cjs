const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('index.html','utf8');
const start=html.indexOf('function currentPortalAllows('),end=html.indexOf('function escapeHtml(',start);
const now=Date.parse('2026-10-03T17:45:00+05:30');
const ctx=vm.createContext({Date,Number,clean:v=>String(v||'').trim(),
  portalActiveIds:new Set(['listed']),portalSnapshot:{snapshot_at:'2026-10-03T16:27:00+05:30'},
  parseDate:v=>v?new Date(v):null});
vm.runInContext(html.slice(start,end),ctx);
const row={'Tender ID':'new','Detail Extracted':'YES','Search Route':'Home -> Tender ID -> GO -> Tender Title -> Detail',
  'Tested At':'2026-10-03T17:42:00+05:30','Status':'Open','Published Date':'2026-10-03T17:00:00+05:30','Closing Date':'2026-10-09T17:30:00+05:30'};
assert.equal(ctx.currentPortalAllows({'Tender ID':'listed'},now),true);
assert.equal(ctx.currentPortalAllows(row,now),true);
assert.equal(ctx.currentPortalAllows({...row,'Search Route':'Home latest -> exact reference -> live link -> full detail'},now),true);
for(const changes of [{'Tested At':'2026-10-03T16:26:00+05:30'},{'Tested At':''},{'Tested At':'2026-10-04T17:00:00+05:30'},
 {'Closing Date':'2026-10-03T17:44:59+05:30'},{'Status':'Cancelled'},{'Detail Extracted':''},
 {'Search Route':'RSP import'},{'Published Date':'2026-10-03T18:00:00+05:30'}])
 assert.equal(Boolean(ctx.currentPortalAllows({...row,...changes},now)),false,JSON.stringify(changes));
ctx.portalSnapshot.snapshot_at='2026-10-03T17:43:00+05:30';
assert.equal(ctx.currentPortalAllows(row,now),false,'a newer full snapshot supersedes the earlier lookup');
console.log('PASS: fresh official detail appears before the next full copy; expired, cancelled, imported and older rows remain excluded.');
