const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('index.html','utf8');assert(html.includes('enforceAccountAccess().then'));assert(!html.includes('enforceTelegramAccess().then'));
for(const source of [html,fs.readFileSync('admin/index.html','utf8')])for(const script of source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(script[1]);
const nodes={},local=new Map();const node=id=>nodes[id]??={value:'',hidden:false,textContent:'',classList:{toggle(){}},addEventListener(e,h){this[e]=h},reportValidity(){return true},querySelector(){return this.button??={disabled:false}},appendChild(){}};
let fail=true,unlocked=false,opened='';
const ctx=vm.createContext({visitorNode:node,VISITOR_SESSION_KEY:'token',VISITOR_PROFILE_KEY:'profile',localStorage:{getItem:k=>local.get(k)||null,removeItem:k=>local.delete(k)},visitorUnlock:data=>{unlocked=true;local.set('token',data.session_token)},location:{hash:'',pathname:'/',search:'',reload(){}},history:{replaceState(){}},window:{open:url=>opened=url},document:{createElement:()=>({})},alert(){},fetch:async(url,options)=>{
 assert(!url.includes('/telegram/'));if(url.includes('mp_districts'))return{json:async()=>({districts:[]})};
 const body=JSON.parse(options.body);
 if(fail)return{ok:false,status:401,json:async()=>({message:'wrong password'})};
 return{ok:true,json:async()=>({ok:true,session_token:'account-token',visitor_id:'user-id',profile:{name:'Test',mobile:'+919876543210',district:'Dewas'}})};
}});
vm.runInContext(fs.readFileSync('assets/account_access.js','utf8'),ctx);
(async()=>{
 const pending=ctx.enforceAccountAccess();await new Promise(r=>setImmediate(r));
 assert(!node('accountSignInForm').hidden);assert(node('accountSignUpForm').hidden);
 node('accountSignUpTab').onclick();assert(!node('accountSignUpForm').hidden);assert(node('accountSignInForm').hidden);
 node('accountForgotTab').onclick();node('accountForgotMobile').value='9876543210';node('accountForgotForm').submit({preventDefault(){},currentTarget:node('accountForgotForm')});assert(opened.startsWith('https://wa.me/919893610244?text='));
 node('accountSignInTab').onclick();node('accountLoginMobile').value='9876543210';node('accountLoginPassword').value='test password long';
 await node('accountSignInForm').submit({preventDefault(){},currentTarget:node('accountSignInForm')});assert(!unlocked);assert(!local.has('token'));
 fail=false;await node('accountSignInForm').submit({preventDefault(){},currentTarget:node('accountSignInForm')});await pending;assert(unlocked);assert.equal(node('accountLoginPassword').value,'');
 console.log('PASS: three account options, explicit WhatsApp draft, failed login stays locked, successful login clears passwords, dashboard/admin script syntax.');
})().catch(e=>{console.error(e);process.exitCode=1});
