const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('assets/visitor_access.js','utf8');
const html=fs.readFileSync('index.html','utf8');
assert(html.includes('enforceVisitorAccess().then'));
assert(!html.includes('enforceTelegramAccess().then'));
assert(!html.includes('id="telegramLoginStart"'));
for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
const local=new Map(),session=new Map(),nodes={};
const storage=map=>({getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)});
const node=id=>nodes[id]??={value:'',textContent:'',style:{},classList:{add(){},remove(){},toggle(){}},remove(){this.removed=true},setAttribute(){},appendChild(){},addEventListener(e,handler){this[e]=handler},reportValidity(){return true},contains(){return false}};
let locked=true,fail=false;
const document={getElementById:node,createElement:()=>({}),addEventListener(){},querySelector:()=>({textContent:'',removeAttribute(){}}),body:{classList:{remove(c){if(c==='telegram-locked')locked=false}}}};
const ctx=vm.createContext({window:{},document,localStorage:storage(local),sessionStorage:storage(session),crypto:{randomUUID:()=> 'de930213-1646-4daa-b9e2-7055c5c294a8'},location:{reload(){}},fetch:async(url,options)=>{
 assert(!url.includes('/telegram/'));
 if(url.includes('mp_districts'))return{json:async()=>({districts:[{name:'Dewas'}]})};
 if(fail)return{ok:false,status:503,json:async()=>({message:'retry'})};
 const body=JSON.parse(options.body);assert(body.name||body.session_token);
 return{ok:true,json:async()=>({ok:true,visitor_id:'visitor-test',session_token:'saved-token',profile:{name:'Visitor',mobile:'+919876543210',district:'Dewas'}})};
}});
vm.runInContext(source,ctx);
(async()=>{
 const pending=ctx.enforceVisitorAccess();await new Promise(r=>setImmediate(r));
 node('visitorName').value='Visitor';node('visitorMobile').value='9876543210';node('visitorDistrict').value='Dewas';
 fail=true;await node('visitorRegistrationForm').submit({preventDefault(){}});assert(locked);assert.equal(local.size,0);
 fail=false;await node('visitorRegistrationForm').submit({preventDefault(){}});await pending;assert(!locked);assert.equal(local.get('mp_visitor_session_v1'),'saved-token');assert.equal(ctx.window.dashboardVisitorProfile.visitor_id,'visitor-test');
 locked=true;await ctx.enforceVisitorAccess();assert(!locked);assert.equal(node('visitorMobile').value,'9876543210');
 console.log('PASS: no Telegram calls, save failure stays locked, save success and session refresh open dashboard.');
})().catch(e=>{console.error(e);process.exitCode=1});
