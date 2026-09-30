const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('admin/index.html','utf8');
const source=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n').replace(/\bboot\(\);\s*$/,'');
const elements={};const timers=[];const calls=[];let replies=[];
const context=vm.createContext({Date,AbortController,console,
 document:{getElementById:id=>elements[id]||={textContent:'',appendChild(){},classList:{add(){},remove(){}}},
 createElement:()=>({}),createTextNode:text=>({textContent:text})},
 localStorage:{getItem:()=> 'test-session'},
 setTimeout:(fn,delay)=>{timers.push(delay);return timers.length;},clearTimeout(){},
 fetch:async(url,options)=>{calls.push({url,options});const next=replies.shift();if(next instanceof Error)throw next;return {ok:next.status===200,status:next.status,json:async()=>next.data};}});
vm.runInContext(source,context);
(async()=>{
 const started=Date.now();
 replies=[{status:404},{status:200,data:{workflow_runs:[{id:123,status:'completed',conclusion:'success',created_at:new Date(started).toISOString(),html_url:'https://github.com/esign2015/mp-tenders/actions/runs/123'}]}}];
 await context.pollWorkflow('data_refresh',0,started);
 assert(elements.workflowState.textContent.includes('सफल'));
 assert.equal(calls.length,2);
 assert(!calls[1].options.headers,'Admin token never sent to public GitHub');
 assert(!timers.includes(90000),'Completed runs stop polling');
 replies=[{status:200,data:{workflow_runs:[]}}];timers.length=0;
 await context.pollWorkflow('data_refresh',0,started);
 assert(elements.workflowState.textContent.includes('प्रतीक्षा'));
 assert(timers.includes(90000),'No run yet must keep polling');
 replies=[{status:403}];timers.length=0;
 await context.pollWorkflow('data_refresh',0,started);
 assert(!elements.workflowState.textContent.includes('session समाप्त'),'Public API limits do not mean admin logout');
 assert(timers.includes(90000));
 replies=[new TypeError('Failed to fetch')];timers.length=0;
 await context.pollWorkflow('data_refresh',0,started);
 assert(elements.workflowState.textContent.includes('असफल होना तय नहीं'));
 assert(timers.includes(90000),'Network failure must retry');
 replies=[{status:200,data:{workflow_runs:[{id:124,status:'completed',conclusion:'failure',created_at:new Date(started).toISOString()}]}}];
 await context.pollWorkflow('data_refresh',0,started);
 assert(elements.workflowState.textContent.includes('असफल'));
 replies=[{status:200,data:{workflow_runs:[{id:99,created_at:new Date(started-60000).toISOString()}]}}];
 const old=await context.workflowStatus('data_refresh',started);
 assert.equal(old.id,undefined,'Previous run cannot be reported as the requested run');
 console.log('PASS: missing Render endpoint falls back to real GitHub status; no-run/network retry, failed-run display, stale-run exclusion, no token leakage.');
})().catch(e=>{console.error(e);process.exitCode=1;});
