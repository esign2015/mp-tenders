const ACCOUNT_IDLE_MS=15*60*1000,ACCOUNT_ACTIVITY_KEY='mp_account_activity_v1';
let accountIdleTimer=null,accountActivityBusy=false,accountLastHeartbeat=Date.now();
function accountLogout(message=''){
  const token=localStorage.getItem(VISITOR_SESSION_KEY);
  localStorage.removeItem(VISITOR_SESSION_KEY);localStorage.removeItem(VISITOR_PROFILE_KEY);localStorage.removeItem(ACCOUNT_ACTIVITY_KEY);
  sessionStorage.removeItem('mp_visitor_request_id');
  if(message)sessionStorage.setItem('mp_account_notice',message);
  document.body.classList.add('telegram-locked');
  if(token)accountPost('/logout',{session_token:token}).catch(()=>{});
  location.reload();
}
function accountExpired(){
  const stamp=Number(localStorage.getItem(ACCOUNT_ACTIVITY_KEY));
  if(stamp&&Date.now()-stamp>=ACCOUNT_IDLE_MS){accountLogout('15 मिनट inactivity के कारण logout हो गया। फिर Sign in करें।');return true}
  return false;
}
function accountActivity(){
  const token=localStorage.getItem(VISITOR_SESSION_KEY);if(!token||accountExpired()||document.hidden)return;
  if(Date.now()-Number(localStorage.getItem(ACCOUNT_ACTIVITY_KEY))>=1000)localStorage.setItem(ACCOUNT_ACTIVITY_KEY,String(Date.now()));
  if(Date.now()-accountLastHeartbeat>=120000&&!accountActivityBusy){
    accountActivityBusy=true;accountLastHeartbeat=Date.now();
    accountPost('/activity',{session_token:token}).catch(error=>{if(error.status===401&&localStorage.getItem(VISITOR_SESSION_KEY)===token)accountLogout(error.message)}).finally(()=>accountActivityBusy=false);
  }
}
function accountSetupControls(data){
  localStorage.setItem(ACCOUNT_ACTIVITY_KEY,String(Date.now()));accountLastHeartbeat=0;
  if(accountIdleTimer)clearInterval(accountIdleTimer);
  accountIdleTimer=setInterval(()=>{if(localStorage.getItem(VISITOR_SESSION_KEY))accountExpired()},1000);
  for(const event of ['pointerdown','pointermove','keydown','scroll','touchstart'])document.addEventListener(event,accountActivity,{passive:true});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)accountExpired()});
  window.addEventListener('storage',event=>{if(event.key===VISITOR_SESSION_KEY&&!event.newValue)location.reload()});
  const modal=visitorNode('accountSettingsModal'),status=visitorNode('accountSettingsStatus');
  function open(view){
    visitorNode('accountEditProfileForm').hidden=view!=='profile';visitorNode('accountChangePasswordForm').hidden=view!=='password';
    visitorNode('accountSettingsTitle').textContent=view==='profile'?'My Profile / मेरा प्रोफाइल':'Change Password / पासवर्ड बदलें';
    const profile=window.dashboardVisitorProfile||data.profile;
    visitorNode('accountEditName').value=profile.name;visitorNode('accountEditDistrict').value=profile.district;visitorNode('accountEditMobile').value=profile.mobile;
    status.textContent='';modal.classList.add('open');modal.setAttribute('aria-hidden','false');visitorNode('telegramProfileMenu').classList.remove('open');
  }
  visitorNode('accountProfileBtn').onclick=()=>open('profile');visitorNode('accountPasswordBtn').onclick=()=>open('password');
  visitorNode('accountSettingsClose').onclick=()=>{modal.classList.remove('open');modal.setAttribute('aria-hidden','true');for(const id of ['accountCurrentPassword','accountChangeNew','accountChangeConfirm'])visitorNode(id).value=''};
  visitorNode('accountEditProfileForm').onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;const button=form.querySelector('button');button.disabled=true;status.textContent='Profile save हो रहा है…';
    const token=localStorage.getItem(VISITOR_SESSION_KEY);
    try{const result=await accountPost('/profile',{session_token:token,name:visitorNode('accountEditName').value.trim(),district:visitorNode('accountEditDistrict').value.trim()});
      if(localStorage.getItem(VISITOR_SESSION_KEY)!==token)return;
      window.dashboardVisitorProfile={...window.dashboardVisitorProfile,...result.profile};localStorage.setItem(VISITOR_PROFILE_KEY,JSON.stringify(window.dashboardVisitorProfile));
      for(const id of ['telegramProfileName','telegramMenuName'])visitorNode(id).textContent=result.profile.name;visitorNode('telegramMenuUsername').textContent=result.profile.district;status.textContent='✓ Profile save हो गया।';
    }catch(error){status.textContent=error.message;if(error.status===401)accountLogout(error.message)}finally{button.disabled=false}
  };
  visitorNode('accountChangePasswordForm').onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;const button=form.querySelector('button');button.disabled=true;status.textContent='Password बदल रहा है…';
    try{const result=await accountPost('/change-password',{session_token:localStorage.getItem(VISITOR_SESSION_KEY),current_password:visitorNode('accountCurrentPassword').value,password:visitorNode('accountChangeNew').value,confirm_password:visitorNode('accountChangeConfirm').value});accountLogout(result.message)}
    catch(error){status.textContent=error.message;if(error.status===401&&error.code!=='wrong_password')accountLogout(error.message)}finally{button.disabled=false}
  };
}

async function accountPost(path,body){
  const response=await fetch('https://mp-tenders-api.onrender.com/api/accounts'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store',keepalive:path==='/logout'});
  const data=await response.json().catch(()=>({}));
  if(!response.ok||!data.ok){const error=new Error(data.message||'Server से जवाब नहीं आया। फिर प्रयास करें।');error.status=response.status;error.code=data.code;throw error}
  return data;
}
function accountUnlock(data){
  visitorUnlock(data);
  visitorNode('telegramLogoutBtn').onclick=()=>accountLogout();
  accountSetupControls(data);
  for(const id of ['accountLoginPassword','accountNewPassword','accountConfirmPassword','accountResetPassword','accountResetConfirm'])if(visitorNode(id))visitorNode(id).value='';
}
async function enforceAccountAccess(){
  const status=visitorNode('visitorStatus'),forms=['SignIn','SignUp','Forgot','Reset'];
  function mode(name){
    for(const current of forms){visitorNode('account'+current+'Form').hidden=current!==name;visitorNode('account'+current+'Tab')?.classList.toggle('active',current===name)}
    status.textContent='';
  }
  const mobileValue=value=>String(value||'').trim().replace(/^\+91/,'');
  function signupRequired(mobile,password,message){
    visitorNode('visitorMobile').value=mobileValue(mobile);
    visitorNode('accountNewPassword').value=password||'';visitorNode('accountConfirmPassword').value='';
    visitorNode('accountLoginPassword').value='';mode('SignUp');status.textContent=message;
  }
  function existingAccount(mobile,message){
    visitorNode('accountLoginMobile').value=mobileValue(mobile);
    for(const id of ['accountLoginPassword','accountNewPassword','accountConfirmPassword'])visitorNode(id).value='';
    mode('SignIn');
  const notice=sessionStorage.getItem('mp_account_notice');if(notice){status.textContent=notice;sessionStorage.removeItem('mp_account_notice')}
  if(localStorage.getItem(VISITOR_SESSION_KEY)&&accountExpired())return new Promise(()=>{});status.textContent=message;
  }
  for(const name of forms.slice(0,3))visitorNode('account'+name+'Tab').onclick=()=>mode(name);
  mode('SignIn');
  try{const cached=JSON.parse(localStorage.getItem(VISITOR_PROFILE_KEY)||'{}');visitorNode('accountLoginMobile').value=String(cached.mobile||'').replace(/^\+91/,'')}catch(_){}
  fetch('data/mp_districts.json',{cache:'no-store'}).then(r=>r.json()).then(data=>{for(const district of data.districts||[])for(const id of ['visitorDistrictList','accountDistrictList']){const option=document.createElement('option');option.value=district.name;visitorNode(id)?.appendChild(option)}}).catch(()=>{});
  let resetToken='';
  if(location.hash.startsWith('#reset=')){resetToken=location.hash.slice(7);history.replaceState(null,'',location.pathname+location.search);mode('Reset')}
  const forgotLink=visitorNode('accountForgotWhatsApp');
  function clearForgotLink(){forgotLink.hidden=true;forgotLink.removeAttribute('href')}
  visitorNode('accountForgotMobile').addEventListener('input',clearForgotLink);
  visitorNode('accountForgotForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;
    clearForgotLink();const mobile=visitorNode('accountForgotMobile').value.trim(),button=form.querySelector('button');button.disabled=true;
    status.textContent='Registered account check हो रहा है…';
    try{
      await accountPost('/forgot-check',{mobile});
      if(visitorNode('accountForgotMobile').value.trim()!==mobile)return;
      const text='MP Tender Dashboard: मेरा tenders.codinglms.xyz ka password भूल गया हूँ। Registered mobile: '+mobile+' . कृपया account verify करके password reset link दें।';
      forgotLink.href='https://wa.me/919893610244?text='+encodeURIComponent(text);forgotLink.hidden=false;
      status.textContent='Account registered है। नीचे WhatsApp link से message भेजें; पहचान verify होने पर reset link मिलेगा।';
    }catch(error){if(error.code==='signup_required')signupRequired(mobile,'',error.message);else status.textContent=error.message}
    finally{button.disabled=false}
  });
  visitorNode('accountResetForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;
    const button=form.querySelector('button');button.disabled=true;
    try{const result=await accountPost('/reset-password',{reset_token:resetToken,password:visitorNode('accountResetPassword').value,confirm_password:visitorNode('accountResetConfirm').value});resetToken='';mode('SignIn');status.textContent=result.message;visitorNode('accountResetPassword').value='';visitorNode('accountResetConfirm').value=''}
    catch(error){status.textContent=error.message}finally{button.disabled=false}
  });
  return new Promise(resolve=>{
    for(const [name,path] of [['SignIn','/signin'],['SignUp','/signup']])visitorNode('account'+name+'Form').addEventListener('submit',async event=>{
      event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;
      const button=form.querySelector('button');button.disabled=true;status.textContent='Account check हो रहा है…';
      const payload=name==='SignIn'?{mobile:visitorNode('accountLoginMobile').value.trim(),password:visitorNode('accountLoginPassword').value}:{name:visitorNode('visitorName').value.trim(),mobile:visitorNode('visitorMobile').value.trim(),district:visitorNode('visitorDistrict').value.trim(),password:visitorNode('accountNewPassword').value,confirm_password:visitorNode('accountConfirmPassword').value};
      try{accountUnlock(await accountPost(path,payload));resolve(true)}catch(error){
        if(name==='SignIn'&&error.code==='signup_required')signupRequired(payload.mobile,payload.password,error.message);
        else if(name==='SignUp'&&(error.code==='account_exists'||error.status===409))existingAccount(payload.mobile,'आप पहले से Sign up हैं। अब केवल password डालकर Sign in करें।');
        else status.textContent=error.message;button.disabled=false;
      }
    });
    const token=localStorage.getItem(VISITOR_SESSION_KEY);
    if(token&&!resetToken){status.textContent='Saved login check हो रहा है…';accountPost('/session',{session_token:token}).then(data=>{accountUnlock(data);resolve(true)}).catch(error=>{if(error.status===401)localStorage.removeItem(VISITOR_SESSION_KEY);status.textContent=error.message})}
  });
}
