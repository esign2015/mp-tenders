const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('index.html','utf8');
for(const m of html.matchAll(/<script\\b[^>]*>([\\s\\S]*?)<\/script>/g))new vm.Script(m[1]);
assert(html.includes('$("pdfBtn").addEventListener("click",exportCardPdf)'));
assert(html.includes('$("pdfBtn2").addEventListener("click",exportPdf)'));
assert(html.includes('>▤ Card View PDF</button>'));
assert(html.includes('>▤ Table View PDF</button>'));
assert(!html.includes('filteredTenders.length ? filteredTenders : allTenders'));
const calls=[],docs=[];
class Pdf{
 constructor(){this.pages=1;this.internal={getPageSize:()=>({width:210,height:297}),pageSize:{getWidth:()=>210,getHeight:()=>297},getNumberOfPages:()=>this.pages};docs.push(this);}
 setFont(){}setFontSize(){}setTextColor(){}setFillColor(){}setDrawColor(){}roundedRect(){}rect(){}link(){}
 splitTextToSize(v){return[String(v)];}getTextWidth(v){return String(v).length;}
 text(v,x,y){calls.push({v:Array.isArray(v)?v.join(' '):v,x,y});}
 addPage(){this.pages++;}save(name){this.saved=name;}
}
const context=vm.createContext({window:{jspdf:{jsPDF:Pdf}},clean:v=>String(v??'').trim(),
 cleanDisplayTitle:v=>String(v??''),formatClosingDateTime:v=>v,
 formatPdfAmount:v=>String(v),filteredTenders:[],allTenders:[],
 quickFilterMode:'', $:()=>({value:'',textContent:'All PAC'}),alert:v=>{context.message=v;}});
const start=html.indexOf('function createDashboardCardPdf('),end=html.indexOf('\nasync function exportPdf(){',start);
vm.runInContext(html.slice(start,end),context);
const rows=Array.from({length:7},(_,i)=>({'Tender ID':'ID-'+i,'Title':'Tender '+i,'Processing Fee':'295','Total Fee':'1295'}));
const doc=context.createDashboardCardPdf(rows,100,'Closing Today');
assert.equal(doc.pages,2);
for(let i=0;i<7;i++)assert.equal(calls.filter(c=>c.v.endsWith('ID-'+i)).length,1);
assert.equal(calls.filter(c=>c.v==='ADVERTISEMENT').length,1);
assert(calls.every(c=>c.y<=291));
(async()=>{
 const before=docs.length;
 await context.exportCardPdf();
 assert.equal(docs.length,before);assert(context.message);
 context.filteredTenders=rows.slice(0,2);context.allTenders=rows;
 await context.exportCardPdf();
 assert(docs.at(-1).saved.startsWith('MP_Tender_Card_View_'));
 assert.equal(docs.at(-1).pages,1);
 console.log('PASS: distinct PDF buttons, six card slots, one advertisement, exact filtered rows and empty-filter protection');
})().catch(e=>{console.error(e);process.exitCode=1;});
