const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('admin/usertool/index.html','utf8'),admin=fs.readFileSync('admin/index.html','utf8');
assert(html.includes('id="userToolPanel" hidden'));
assert(admin.includes('href="./usertool/"'));
assert(!admin.includes('id="passwordResetAdminForm"'));assert(!admin.includes('id="userAccessForm"'));
for(const match of admin.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
const nodes={},storage=new Map(),events={},calls=[];let replies=[];
function node(id){return nodes[id]??={value:'',checked:false,hidden:id==='userToolPanel',disabled:false,textContent:'',
  reportValidity:()=>true,select(){},querySelector(){return node(id+'Submit')},addEventListener(name,fn){this[name]=fn}}}
const ctx=vm.createContext({console,Set,AbortController,
  document:{title:'',hidden:false,getElementById:node,addEventListener:(name,fn)=>events[name]=fn},
  window:{addEventListener:(name,fn)=>events[name]=fn},navigator:{clipboard:{writeText:async()=>{}}},
  localStorage:{getItem:key=>storage.get(key)||null,removeItem:key=>storage.delete(key)},
  setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,
  fetch:async(url,options)=>{calls.push({url,options});const result=replies.shift();if(result instanceof Error)throw result;
    if(typeof result==='function')return result();assert(result,'unexpected network request');
    return {ok:result.status===200,status:result.status,json:async()=>result.data}}
});
vm.runInContext(fs.readFileSync('assets/admin_usertool.js','utf8'),ctx);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const reply=(status,data)=>({status,data});
const profile={name:'Test User',mobile:'+919876543210',district:'Dewas',tehsil:'Kannod'};
(async()=>{
  await tick();assert(node('userToolPanel').hidden);assert.equal(node('userToolErrorCode').textContent,'404');assert.equal(calls.length,0);
  await assert.rejects(ctx.userToolRequest('/api/admin/accounts/access',{mobile:'9876543210'}));assert.equal(calls.length,0);
  storage.set('mp_admin_session','forged');replies=[reply(401,{ok:false})];await ctx.userToolRestore();
  assert(node('userToolPanel').hidden);assert.equal(node('userToolErrorCode').textContent,'404');assert(!storage.has('mp_admin_session'));
  storage.set('mp_admin_session','valid');replies=[new TypeError('Offline')];await ctx.userToolRestore();
  assert(node('userToolPanel').hidden);assert.equal(node('userToolErrorCode').textContent,'503');
  replies=[reply(200,{ok:true,email:'admin@example.test'})];await ctx.userToolRestore();
  assert(!node('userToolPanel').hidden);assert(node('userToolError').hidden);assert.equal(node('userToolEmail').textContent,'admin@example.test');
  node('userAccessMobile').value='9876543210';replies=[reply(200,{ok:true,profile,blocked:false})];await ctx.userToolAccess();
  assert(!node('userBlock').disabled);assert(node('userUnblock').disabled);
  assert.equal(JSON.parse(calls.at(-1).options.body).blocked,undefined);
  replies=[reply(200,{ok:true,profile,blocked:true})];await ctx.userToolAccess(true);
  assert.equal(JSON.parse(calls.at(-1).options.body).blocked,true);assert(node('userBlock').disabled);assert(!node('userUnblock').disabled);
  node('userAccessMobile').value='9123456789';node('userAccessMobile').input();
  const before=calls.length;await ctx.userToolAccess(false);assert.equal(calls.length,before,'editing a mobile requires checking that user first');
  node('passwordResetMobile').value='9876543210';node('passwordResetVerified').checked=true;
  replies=[reply(200,{ok:true,reset_url:'https://tenders.codinglms.xyz/#reset=test'})];
  await node('passwordResetAdminForm').onsubmit({preventDefault(){},currentTarget:node('passwordResetAdminForm')});
  assert.equal(JSON.parse(calls.at(-1).options.body).identity_verified,true);assert(!node('passwordResetLink').hidden);assert(!node('passwordResetCopy').hidden);
  node('passwordResetMobile').input();assert(node('passwordResetLink').hidden);assert.equal(node('passwordResetLink').value,'');
  let finish;
  replies=[()=>new Promise(resolve=>finish=resolve)];
  const pending=node('passwordResetAdminForm').onsubmit({preventDefault(){},currentTarget:node('passwordResetAdminForm')});await tick();
  storage.delete('mp_admin_session');events.storage({key:'mp_admin_session'});
  finish({ok:true,status:200,json:async()=>({ok:true,reset_url:'must-not-display'})});await pending;
  assert(node('userToolPanel').hidden);assert(node('passwordResetLink').hidden);assert.equal(node('passwordResetLink').value,'');
  storage.set('mp_admin_session','valid-again');replies=[reply(200,{ok:true,email:'admin@example.test'})];await ctx.userToolRestore();
  node('userAccessMobile').value='9876543210';replies=[reply(401,{ok:false,message:'Expired'})];await ctx.userToolAccess();
  assert(node('userToolPanel').hidden);assert.equal(node('userToolErrorCode').textContent,'404');assert.equal(node('userAccessStatus').textContent,'');
  console.log('PASS: verified admin only; unauthorized 404, offline guard, moved controls, checked-mobile block actions, reset links, logout races and expired sessions.');
})().catch(error=>{console.error(error);process.exitCode=1});
