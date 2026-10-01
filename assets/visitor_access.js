// Temporary dashboard access through a server-saved three-field profile.
const VISITOR_API='https://mp-tenders-api.onrender.com/api/visitors';
const VISITOR_SESSION_KEY='mp_visitor_session_v1';
const VISITOR_PROFILE_KEY='mp_visitor_profile_v1';
function visitorNode(id){return document.getElementById(id)}
function visitorRequestId(){
  let id=sessionStorage.getItem('mp_visitor_request_id');
  if(!id){
    id=typeof crypto.randomUUID==='function'?crypto.randomUUID():'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.random()*16|0;return(c==='x'?r:(r&3|8)).toString(16)});
    sessionStorage.setItem('mp_visitor_request_id',id);
  }
  return id;
}
async function visitorPost(path,body){
  const response=await fetch(VISITOR_API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});
  const data=await response.json().catch(()=>({}));
  if(!response.ok||!data.ok){const error=new Error(data.message||'Server से जानकारी save नहीं हुई। फिर प्रयास करें।');error.status=response.status;throw error}
  return data;
}
function visitorUnlock(data){
  localStorage.setItem(VISITOR_SESSION_KEY,data.session_token);
  window.dashboardVisitorProfile={...data.profile,visitor_id:data.visitor_id};
  window.dashboardAffidavitProfile=data.affidavit_profile||{};
  window.dashboardProfileStorage=data.storage||'';
  localStorage.setItem(VISITOR_PROFILE_KEY,JSON.stringify(window.dashboardVisitorProfile));
  document.body.classList.remove('telegram-locked');
  visitorNode('telegramGate')?.remove();
  visitorNode('telegramProfile')?.classList.add('visible');
  for(const id of ['telegramProfileName','telegramMenuName'])if(visitorNode(id))visitorNode(id).textContent=data.profile.name;
  if(visitorNode('telegramMenuUsername'))visitorNode('telegramMenuUsername').textContent=data.profile.district;
  if(visitorNode('telegramAvatar'))visitorNode('telegramAvatar').style.display='none';
  const status=document.querySelector('.telegram-profile-status');
  if(status){status.removeAttribute('data-i18n');status.textContent='Profile saved'}
  const button=visitorNode('telegramProfileBtn'),menu=visitorNode('telegramProfileMenu');
  if(button){button.setAttribute('aria-label','Profile menu');button.onclick=event=>{event.stopPropagation();menu?.classList.toggle('open');if(typeof positionTelegramProfileMenu==='function')positionTelegramProfileMenu()}}
  if(typeof positionTelegramProfileMenu==='function'){window.addEventListener('resize',positionTelegramProfileMenu);window.addEventListener('scroll',positionTelegramProfileMenu,true)}
  document.addEventListener('click',event=>{if(menu&&!menu.contains(event.target)&&!button?.contains(event.target))menu.classList.remove('open')});
  const logout=visitorNode('telegramLogoutBtn');
  if(logout)logout.onclick=()=>{localStorage.removeItem(VISITOR_SESSION_KEY);localStorage.removeItem(VISITOR_PROFILE_KEY);sessionStorage.removeItem('mp_visitor_request_id');location.reload()};
}
window.saveDashboardAffidavitProfile=async function(profile){
  const token=localStorage.getItem(VISITOR_SESSION_KEY);
  if(!token)throw new Error('पहले Name, Mobile और District save करें।');
  const result=await visitorPost('/affidavit',{session_token:token,profile});
  window.dashboardAffidavitProfile=result.profile;
  return result;
};
async function enforceVisitorAccess(){
  const form=visitorNode('visitorRegistrationForm'),status=visitorNode('visitorStatus');
  try{
    const cached=JSON.parse(localStorage.getItem(VISITOR_PROFILE_KEY)||'{}');
    for(const [key,id] of [['name','visitorName'],['mobile','visitorMobile'],['district','visitorDistrict']])if(visitorNode(id))visitorNode(id).value=key==='mobile'?String(cached[key]||'').replace(/^\+91/,''):(cached[key]||'');
  }catch(_){}
  fetch('data/mp_districts.json',{cache:'no-store'}).then(r=>r.json()).then(data=>{
    const list=visitorNode('visitorDistrictList');
    for(const district of data.districts||data){const option=document.createElement('option');option.value=district.name;list?.appendChild(option)}
  }).catch(()=>{});
  const token=localStorage.getItem(VISITOR_SESSION_KEY);
  if(token){
    status.textContent='आपकी saved जानकारी check हो रही है…';
    try{visitorUnlock(await visitorPost('/session',{session_token:token}));return true}
    catch(error){if(error.status===401){localStorage.removeItem(VISITOR_SESSION_KEY);sessionStorage.removeItem('mp_visitor_request_id')}status.textContent=error.message}
  }
  return new Promise(resolve=>{
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      if(!form.reportValidity())return;
      const button=visitorNode('visitorSave');button.disabled=true;status.textContent='जानकारी save हो रही है…';
      try{
        const data=await visitorPost('/register',{registration_id:visitorRequestId(),name:visitorNode('visitorName').value.trim(),mobile:visitorNode('visitorMobile').value.trim(),district:visitorNode('visitorDistrict').value.trim()});
        visitorUnlock(data);resolve(true);
      }catch(error){status.textContent=error.message;button.disabled=false}
    });
  });
}
