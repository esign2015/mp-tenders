const exportLibraryLoads=new Map();
function loadExportLibrary(url){
 if(exportLibraryLoads.has(url))return exportLibraryLoads.get(url);
 const task=new Promise((resolve,reject)=>{const script=document.createElement("script");script.src=url;script.onload=resolve;script.onerror=()=>{exportLibraryLoads.delete(url);script.remove();reject(new Error("Export library download failed. फिर प्रयास करें।"));};document.head.appendChild(script);});
 exportLibraryLoads.set(url,task);return task;
}
async function ensureExportLibraries(kind){
 if(kind==="word"){if(!window.docx)await loadExportLibrary("https://cdn.jsdelivr.net/npm/docx@8.5.0/build/index.umd.js");return;}
 if(!window.jspdf?.jsPDF)await loadExportLibrary("https://cdn.jsdelivr.net/npm/jspdf@2.5.2/dist/jspdf.umd.min.js");
 if(!window.jspdf.jsPDF.API.autoTable)await loadExportLibrary("https://cdn.jsdelivr.net/npm/jspdf-autotable@3.8.4/dist/jspdf.plugin.autotable.min.js");
}
