const CACHE_NAME="mp-tenders-shell-v4";
const SHELL=["./","./index.html","./manifest.webmanifest","./assets/sar-logo.png","./assets/visitor_access.js?v=8","./assets/account_access.js?v=24","./assets/bidder_tools.js?v=1","./assets/export_libraries.js?v=1"];
self.addEventListener("install",event=>{
  event.waitUntil(caches.open(CACHE_NAME).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting()));
});
self.addEventListener("activate",event=>{
  event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener("fetch",event=>{
  const url=new URL(event.request.url);
  if(url.origin===location.origin && (url.pathname.endsWith(".csv") || url.pathname.includes("/data/"))){
    return;
  }
  event.respondWith(fetch(event.request).catch(()=>caches.match(event.request)));
});