const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('index.html','utf8');
for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);
assert(html.includes('$("pdfBtn").addEventListener("click",exportCardPdf)'));
assert(html.includes('$("pdfBtn2").addEventListener("click",exportPdf)'));
assert(html.includes('>▤ Card View PDF</button>'));
assert(html.includes('>▤ Table View PDF</button>'));
assert(!html.includes('filteredTenders.length ? filteredTenders : allTenders'));
const calls=[],docs=[],links=[];
class Pdf{
 constructor(options={}){this.options=options;this.pages=1;this.width=options.orientation==='landscape'?297:210;this.height=options.orientation==='landscape'?210:297;this.cards=[];this.internal={getPageSize:()=>({width:this.width,height:this.height}),pageSize:{getWidth:()=>this.width,getHeight:()=>this.height},getNumberOfPages:()=>this.pages};docs.push(this);}
 setFont(){}setFontSize(){}setTextColor(){}setFillColor(){}setDrawColor(){}roundedRect(x,y,w,h){if(h===80)this.cards.push({page:this.pages,x,y,w,h});}rect(){}link(x,y,w,h,a){links.push(a.url)}
 splitTextToSize(v){return[String(v)];}getTextWidth(v){return String(v).length;}
 text(v,x,y){calls.push({v:Array.isArray(v)?v.join(' '):v,x,y});}
 textWithLink(v,x,y,a){links.push(a.url);this.text(v,x,y);}
 autoTable(options){
  this.tableCursors=[];
  for(let p=1;p<=(Pdf.tablePageCount||1);p++){
   if(p>1)this.addPage();
   const data={cursor:{y:39}};options.willDrawPage(data);this.tableCursors.push(data.cursor.y);options.didDrawPage();
  }
 }
 addPage(){this.pages++;}save(name){this.saved=name;}
}
const context=vm.createContext({window:{jspdf:{jsPDF:Pdf}},clean:v=>String(v??'').trim(),
 cleanDisplayTitle:v=>String(v??''),formatClosingDateTime:v=>v,
 formatPdfAmount:v=>String(v),filteredTenders:[],allTenders:[],
 quickFilterMode:'', $:()=>({value:'',textContent:'All PAC'}),alert:v=>{context.message=v;}});
const start=html.indexOf('function drawPdfPromotionCard('),end=html.indexOf('\nasync function exportPdf(){',start);
vm.runInContext(html.slice(start,end),context);
const tableEnd=html.indexOf('\n/*\n   Search is intentionally',end);
vm.runInContext(html.slice(end,tableEnd),context);
context.parseDate=()=>null;
const rows=Array.from({length:7},(_,i)=>({'Tender ID':'ID-'+i,'Title':'Tender '+i,'Processing Fee':'295','Total Fee':'1295'}));
const doc=context.createDashboardCardPdf(rows,100,'Closing Today');
assert.equal(doc.pages,2);
assert.equal(doc.options.orientation,'landscape');
assert.equal(doc.cards.filter(c=>c.page===1).length,8);
assert.deepEqual([...new Set(doc.cards.filter(c=>c.page===1).map(c=>c.x))].length,4);
assert.deepEqual([...new Set(doc.cards.filter(c=>c.page===1).map(c=>c.y))],[32,116]);
assert(doc.cards.every(c=>c.x>=8&&c.x+c.w<=289&&c.y+c.h<=196));
for(let i=0;i<7;i++)assert.equal(calls.filter(c=>c.v.endsWith('ID-'+i)).length,1);
assert.equal(calls.filter(c=>c.v==='ADVERTISEMENT').length,2);
assert.equal(calls.filter(c=>c.v==='MP Tender Alerts').length,2);
assert.equal(links.filter(url=>url==='https://chat.whatsapp.com/BuKI6bxZGHVBIHA6KZt7Vy').length,2);
assert(calls.every(c=>c.y<=204));
for(const [count,pages] of [[1,1],[6,1],[12,2],[13,3]]){
 const sample=Array.from({length:count},(_,i)=>({'Tender ID':'PAGE-'+count+'-'+i,'Title':'Tender '+i}));
 const before=calls.length,testDoc=context.createDashboardCardPdf(sample,count,'All Tenders');
 assert.equal(testDoc.pages,pages);
 assert.equal(testDoc.cards.length,count+2*pages);
 for(let i=0;i<count;i++)assert.equal(calls.slice(before).filter(c=>c.v.endsWith('PAGE-'+count+'-'+i)).length,1);
}
(async()=>{
 const before=docs.length;
 await context.exportCardPdf();
 assert.equal(docs.length,before);assert(context.message);
 context.filteredTenders=rows.slice(0,2);context.allTenders=rows;
 await context.exportCardPdf();
 assert(docs.at(-1).saved.startsWith('MP_Tender_Card_View_'));
 assert.equal(docs.at(-1).pages,1);
 Pdf.tablePageCount=3;
 await context.exportPdf();
 assert.equal(docs.at(-1).pages,3);assert.deepEqual(docs.at(-1).tableCursors,[39,100,39]);
 Pdf.tablePageCount=1;
 const groupBefore=links.filter(x=>x.includes('chat.whatsapp.com/')).length;
 await context.exportPdf();assert.equal(docs.at(-1).pages,2);
 assert.equal(links.filter(x=>x.includes('chat.whatsapp.com/')).length,groupBefore+1);
 const pages=docs.at(-1).pages;context.appendPdfPromotionPage(docs.at(-1));assert.equal(docs.at(-1).pages,pages+1);
 assert(html.includes('appendPdfPromotionPage(doc);\n      const pdfName=fileBase(d)+".pdf";'));
 console.log('PASS: landscape Card PDF with four columns, two rows, six tenders plus two clickable promotional cards, exact pagination and unchanged Table PDF');
})().catch(e=>{console.error(e);process.exitCode=1;});
