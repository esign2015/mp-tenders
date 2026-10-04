// Shared helpers for the mobile/password account and saved document profile.
const VISITOR_API='https://mp-tenders-api.onrender.com/api/visitors';
const VISITOR_SESSION_KEY='mp_visitor_session_v1';
const VISITOR_PROFILE_KEY='mp_visitor_profile_v1';
function visitorNode(id){return document.getElementById(id)}
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
  window.dashboardAffidavitProfileCanonical=!!data.affidavit_profile_canonical;
  window.dashboardAffidavitProfileEdited=false;
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
  if(!token)throw new Error('पहले अपने account में Sign in करें।');
  const result=await visitorPost('/affidavit',{session_token:token,profile});
  if(localStorage.getItem(VISITOR_SESSION_KEY)!==token)throw new Error('Session changed. Please Sign in again.');
  if(profile.email && result.profile?.email!==profile.email)throw new Error('Email सहित profile save नहीं हुई। कृपया refresh करके फिर कोशिश करें।');
  for(const key of ['pincode','district','tehsil','houseNumber','roadStreet','locality','landmark'])if(profile[key]&&result.profile?.[key]!==profile[key])throw new Error('पूरी profile save नहीं हुई। कृपया refresh करके फिर कोशिश करें।');
  window.dashboardAffidavitProfile=result.profile;
  window.dashboardAffidavitProfileCanonical=!!result.canonical;
  window.cacheDashboardAffidavitProfile?.(result.profile);
  return result;
};
