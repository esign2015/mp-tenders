const USER_TOOL_API='https://mp-tenders-api.onrender.com';
const USER_TOOL_KEY='mp_admin_session';
const userToolNode=id=>document.getElementById(id);
let userToolToken='',userToolCheckedMobile='',userToolRestoreVersion=0;
const userToolBusy=new Set();

function userToolClearAccess(){
  userToolCheckedMobile='';
  userToolNode('userBlock').disabled=true;userToolNode('userUnblock').disabled=true;
  userToolNode('userAccessStatus').textContent='';
}
function userToolDeny(code='404',message='यह पेज उपलब्ध नहीं है।'){
  userToolToken='';
  userToolNode('userToolPanel').hidden=true;userToolNode('userToolError').hidden=false;
  userToolNode('userToolErrorCode').textContent=code;
  userToolNode('userToolErrorTitle').textContent=code==='404'?'Page not found':'सेवा उपलब्ध नहीं है';
  userToolNode('userToolErrorMessage').textContent=message;
  document.title=code==='404'?'404 — Page not found':'सेवा उपलब्ध नहीं है';
  userToolNode('userToolEmail').textContent='';
  userToolNode('passwordResetLink').value='';userToolNode('passwordResetLink').hidden=true;
  userToolNode('passwordResetCopy').hidden=true;userToolNode('passwordResetStatus').textContent='';
  userToolNode('passwordResetVerified').checked=false;
  userToolNode('passwordResetMobile').value='';userToolNode('userAccessMobile').value='';
  userToolClearAccess();
}
async function userToolFetch(path,token,body){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);
  try{
    const response=await fetch(USER_TOOL_API+path,{method:body===undefined?'GET':'POST',
      headers:{Authorization:'Bearer '+token,...(body===undefined?{}:{'Content-Type':'application/json'})},
      ...(body===undefined?{}:{body:JSON.stringify(body)}),cache:'no-store',signal:controller.signal});
    const data=await response.json().catch(()=>({}));
    if(!response.ok||!data.ok){const error=new Error(data.message||'Server से जवाब नहीं आया। फिर प्रयास करें।');error.status=response.status;throw error}
    return data;
  }finally{clearTimeout(timer)}
}
async function userToolRestore(){
  const version=++userToolRestoreVersion,token=localStorage.getItem(USER_TOOL_KEY);
  if(!token){userToolDeny();return}
  if(!userToolToken){
    userToolNode('userToolErrorCode').textContent='…';
    userToolNode('userToolErrorTitle').textContent='कृपया प्रतीक्षा करें';
    userToolNode('userToolErrorMessage').textContent='';
  }
  try{
    const data=await userToolFetch('/api/admin/session',token);
    if(version!==userToolRestoreVersion||localStorage.getItem(USER_TOOL_KEY)!==token)return;
    if(!data.email)throw new Error('Admin session की पुष्टि नहीं हुई।');
    userToolToken=token;
    userToolNode('userToolEmail').textContent=data.email;
    userToolNode('userToolError').hidden=true;userToolNode('userToolPanel').hidden=false;
    document.title='MP Tenders — User Tools';
  }catch(error){
    if(version!==userToolRestoreVersion||localStorage.getItem(USER_TOOL_KEY)!==token)return;
    if(error.status===401||error.status===403){localStorage.removeItem(USER_TOOL_KEY);userToolDeny()}
    else userToolDeny('503','अभी सर्वर से संपर्क नहीं हो सका। थोड़ी देर बाद page reload करें।');
  }
}
async function userToolRequest(path,body){
  const token=localStorage.getItem(USER_TOOL_KEY);
  if(!token||token!==userToolToken){userToolDeny();throw new Error('Admin login required.')}
  try{
    const result=await userToolFetch(path,token,body);
    if(localStorage.getItem(USER_TOOL_KEY)!==token||userToolToken!==token)throw new Error('Admin session बदल गया है।');
    return result;
  }catch(error){
    if((error.status===401||error.status===403)&&localStorage.getItem(USER_TOOL_KEY)===token){
      localStorage.removeItem(USER_TOOL_KEY);userToolDeny();
    }
    throw error;
  }
}
async function userToolAccess(blocked){
  const mobile=userToolNode('userAccessMobile').value.trim();
  if(userToolBusy.has('access')||!userToolNode('userAccessForm').reportValidity()
    ||(typeof blocked==='boolean'&&mobile!==userToolCheckedMobile))return;
  userToolBusy.add('access');
  const check=userToolNode('userAccessForm').querySelector('button[type="submit"]');
  check.disabled=true;userToolNode('userBlock').disabled=true;userToolNode('userUnblock').disabled=true;
  userToolNode('userAccessStatus').textContent='User की जानकारी जाँची जा रही है…';
  try{
    const body={mobile};if(typeof blocked==='boolean')body.blocked=blocked;
    const data=await userToolRequest('/api/admin/accounts/access',body);
    if(userToolNode('userAccessMobile').value.trim()!==mobile)return;
    userToolCheckedMobile=mobile;
    userToolNode('userAccessStatus').textContent=data.profile.name+' · '+data.profile.mobile+' · '+data.profile.district+' / '+data.profile.tehsil+' · '+(data.blocked?'Blocked':'Active');
    userToolNode('userBlock').disabled=data.blocked;userToolNode('userUnblock').disabled=!data.blocked;
  }catch(error){userToolClearAccess();if(userToolToken)userToolNode('userAccessStatus').textContent=error.message}
  finally{check.disabled=false;userToolBusy.delete('access')}
}
userToolNode('userAccessMobile').addEventListener('input',userToolClearAccess);
userToolNode('userAccessForm').onsubmit=event=>{event.preventDefault();return userToolAccess()};
userToolNode('userBlock').onclick=()=>userToolAccess(true);
userToolNode('userUnblock').onclick=()=>userToolAccess(false);
userToolNode('passwordResetMobile').addEventListener('input',()=>{
  userToolNode('passwordResetVerified').checked=false;
  userToolNode('passwordResetLink').value='';userToolNode('passwordResetLink').hidden=true;userToolNode('passwordResetCopy').hidden=true;
  userToolNode('passwordResetStatus').textContent='';
});
userToolNode('passwordResetAdminForm').onsubmit=async event=>{
  event.preventDefault();if(userToolBusy.has('reset')||!event.currentTarget.reportValidity())return;
  userToolBusy.add('reset');
  const mobile=userToolNode('passwordResetMobile').value.trim(),button=userToolNode('passwordResetCreate');
  button.disabled=true;userToolNode('passwordResetLink').value='';userToolNode('passwordResetLink').hidden=true;
  userToolNode('passwordResetCopy').hidden=true;userToolNode('passwordResetStatus').textContent='Reset link बन रही है…';
  try{
    const result=await userToolRequest('/api/admin/accounts/reset-link',{mobile,identity_verified:userToolNode('passwordResetVerified').checked});
    if(userToolNode('passwordResetMobile').value.trim()!==mobile)return;
    userToolNode('passwordResetLink').value=result.reset_url;userToolNode('passwordResetLink').hidden=false;userToolNode('passwordResetCopy').hidden=false;
    userToolNode('passwordResetStatus').textContent='यह link 15 मिनट में expire होगी और एक बार चलेगी। Verified user को WhatsApp पर भेजें।';
    userToolNode('passwordResetVerified').checked=false;
  }catch(error){if(userToolToken)userToolNode('passwordResetStatus').textContent=error.message}
  finally{button.disabled=false;userToolBusy.delete('reset')}
};
userToolNode('passwordResetCopy').onclick=async()=>{
  if(!userToolToken||localStorage.getItem(USER_TOOL_KEY)!==userToolToken){userToolDeny();return}
  try{await navigator.clipboard.writeText(userToolNode('passwordResetLink').value);userToolNode('passwordResetStatus').textContent='Link copied. Verified user को WhatsApp पर भेजें।'}
  catch(_){userToolNode('passwordResetLink').select();userToolNode('passwordResetStatus').textContent='Link select है; copy करें।'}
};
window.addEventListener('storage',event=>{if(event.key===USER_TOOL_KEY){userToolDeny();userToolRestore()}});
window.addEventListener('pageshow',event=>{if(event.persisted){userToolDeny();userToolRestore()}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)userToolRestore()});
setInterval(()=>{if(!document.hidden)userToolRestore()},60000);
userToolRestore();
