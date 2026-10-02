function accountMobileValue(value){
  let digits=String(value||'').replace(/[^0-9]/g,'');
  if(digits.length===12&&digits.startsWith('91'))digits=digits.slice(2);
  return /^[6-9]/.test(digits)?digits.slice(0,10):'';
}
function accountMiddleNameValue(value){
  return String(value||'').replace(/[^\p{L}\p{M} .'’\-]/gu,'').slice(0,10);
}
function accountBindMiddleName(id){
  const field=visitorNode(id);if(!field)return;
  field.addEventListener('input',()=>{field.value=accountMiddleNameValue(field.value)});
  field.addEventListener('paste',event=>{
    event.preventDefault();
    const value=field.value,start=field.selectionStart??value.length,end=field.selectionEnd??start;
    field.value=accountMiddleNameValue(value.slice(0,start)+event.clipboardData.getData('text')+value.slice(end));
    field.dispatchEvent(new Event('input',{bubbles:true}));
  });
}
function accountBindMobile(id){
  const field=visitorNode(id);if(!field)return;
  const clean=()=>{field.value=accountMobileValue(field.value)};
  field.addEventListener('input',clean);
  field.addEventListener('paste',event=>{
    event.preventDefault();field.value=accountMobileValue(event.clipboardData.getData('text'));
    field.dispatchEvent(new Event('input',{bubbles:true}));
  });
}
const accountPlacePickers=new Map();
function accountBindPlacePicker(fieldId,listId,choices,onUpdate){
  const field=visitorNode(fieldId),list=visitorNode(listId);let accepted='';
  function render(){
    const prefix=field.value.trim().toLowerCase();list.replaceChildren();
    for(const value of choices().filter(value=>value.toLowerCase().startsWith(prefix))){const option=document.createElement('option');option.value=value;list.appendChild(option)}
  }
  function update(){
    const options=choices(),draft=field.value.trim();
    if(draft&&!options.some(value=>value.toLowerCase().startsWith(draft.toLowerCase())))field.value=accepted;
    const exact=options.find(value=>value.toLowerCase()===field.value.trim().toLowerCase());
    if(exact)field.value=exact;
    accepted=field.value;render();onUpdate();
  }
  function commit(){
    const exact=choices().find(value=>value.toLowerCase()===field.value.trim().toLowerCase());
    field.value=exact||'';accepted=field.value;render();onUpdate();
  }
  field.addEventListener('input',update);
  field.addEventListener('change',commit);field.addEventListener('blur',commit);
  field.addEventListener('focus',render);
  accountPlacePickers.set(fieldId,()=>{accepted=field.value;render()});render();
}
let accountTehsilDirectory={};
function accountTehsilOptions(districtId,tehsilId,selected=''){
  const field=visitorNode(tehsilId);if(!field)return;
  const values=accountTehsilDirectory[visitorNode(districtId).value.trim()]||[];
  if(tehsilId==='visitorTehsil'){
    field.disabled=!values.length;field.value=values.includes(selected)?selected:'';
    field.placeholder=values.length?'Tehsil / तहसील':'Tehsil / तहसील — पहले जिला चुनें';
    accountPlacePickers.get(tehsilId)?.();return;
  }
  field.replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent=values.length?'Tehsil / तहसील चुनें':'Tehsil / तहसील — पहले जिला चुनें';field.appendChild(empty);
  for(const value of values){const option=document.createElement('option');option.value=value;option.textContent=value;field.appendChild(option)}
  field.disabled=!values.length;field.value=values.includes(selected)?selected:'';
}
const ACCOUNT_ACTIVITY_KEY='mp_account_activity_v1',ACCOUNT_EXPIRY_KEY='mp_account_daily_expiry_v1';
const ACCOUNT_MAINTENANCE_MESSAGE='Website under maintenance. Please login after 12:30 AM IST.';
let accountClockOffset=0,accountMaintenanceTimer=null,accountWasInMaintenance=false;
function accountNow(){return Date.now()+accountClockOffset}
function accountMaintenanceActive(now=accountNow()){const minute=Math.floor(((now+19800000)%86400000)/60000);return minute>=1410||minute<30}
function accountDailyExpiry(now=accountNow()){const cutoff=Math.floor((now+19800000)/86400000)*86400000-19800000+84600000;return cutoff>now?cutoff:cutoff+86400000}
function accountApplyMaintenance(){
  const blocked=accountMaintenanceActive(),notice=visitorNode('accountMaintenanceNotice');
  if(notice)notice.hidden=!blocked;
  if(blocked&&localStorage.getItem(VISITOR_SESSION_KEY)){accountLogout(ACCOUNT_MAINTENANCE_MESSAGE);return}
  if(blocked){
    if(visitorNode('accountGateHeading'))visitorNode('accountGateHeading').textContent='Website under maintenance';
    if(visitorNode('visitorStatus'))visitorNode('visitorStatus').textContent=ACCOUNT_MAINTENANCE_MESSAGE;
    if(visitorNode('accountTabs'))visitorNode('accountTabs').hidden=true;
    for(const name of ['SignIn','SignUp','Forgot','Reset','Repair'])if(visitorNode('account'+name+'Form'))visitorNode('account'+name+'Form').hidden=true;
    for(const name of ['SignIn','Forgot'])if(visitorNode('account'+name+'Pane'))visitorNode('account'+name+'Pane').hidden=true;
  }else if(accountWasInMaintenance){
    visitorNode('accountTabs').hidden=false;visitorNode('accountSignInPane').hidden=false;visitorNode('accountSignInForm').hidden=false;
    visitorNode('accountGateHeading').textContent='Welcome back / स्वागत है';visitorNode('visitorStatus').textContent='';
  }
  accountWasInMaintenance=blocked;
  if(visitorNode('accountSignInForm'))accountUpdateAllSubmits();
}
let accountLogoutTimer=null,accountSessionTimer=null,accountSessionBusy=false;
async function accountCheckSession(){
  const token=localStorage.getItem(VISITOR_SESSION_KEY);
  if(!token||document.hidden||accountSessionBusy||accountExpired())return;
  accountSessionBusy=true;
  try{await accountPost('/session-check',{session_token:token})}
  catch(error){if(error.status===401&&localStorage.getItem(VISITOR_SESSION_KEY)===token)accountLogout(error.message)}
  finally{accountSessionBusy=false}
}
function accountLogout(message=''){
  const token=localStorage.getItem(VISITOR_SESSION_KEY);
  localStorage.removeItem(VISITOR_SESSION_KEY);localStorage.removeItem(VISITOR_PROFILE_KEY);localStorage.removeItem(ACCOUNT_ACTIVITY_KEY);localStorage.removeItem(ACCOUNT_EXPIRY_KEY);
  sessionStorage.removeItem('mp_visitor_request_id');
  if(message)sessionStorage.setItem('mp_account_notice',message);
  document.body.classList.add('telegram-locked');
  if(token)accountPost('/logout',{session_token:token}).catch(()=>{});
  location.reload();
}
function accountExpired(){
  const expiry=Number(localStorage.getItem(ACCOUNT_EXPIRY_KEY));
  if(accountMaintenanceActive()||(expiry&&accountNow()>=expiry)){accountLogout(accountMaintenanceActive()?ACCOUNT_MAINTENANCE_MESSAGE:'रात 11:30 PM IST पर session बंद हो गया। फिर Sign in करें।');return true}
  return false;
}
function accountActivity(){
  if(localStorage.getItem(VISITOR_SESSION_KEY)&&!accountExpired()&&!document.hidden)localStorage.setItem(ACCOUNT_ACTIVITY_KEY,String(accountNow()));
}
function accountSetupControls(data){
  if(data.new_login||!localStorage.getItem(ACCOUNT_ACTIVITY_KEY))localStorage.setItem(ACCOUNT_ACTIVITY_KEY,String(Date.now()));
  localStorage.setItem(ACCOUNT_EXPIRY_KEY,String(data.session_expires_at?Math.min(data.session_expires_at*1000,accountDailyExpiry()):accountDailyExpiry()));
  if(accountLogoutTimer)clearInterval(accountLogoutTimer);
  accountLogoutTimer=setInterval(()=>{if(localStorage.getItem(VISITOR_SESSION_KEY))accountExpired()},1000);
  if(accountSessionTimer)clearInterval(accountSessionTimer);
  accountSessionTimer=setInterval(accountCheckSession,60000);
  for(const event of ['pointerdown','pointermove','keydown','scroll','touchstart'])document.addEventListener(event,accountActivity,{passive:true});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)accountCheckSession()});
  window.addEventListener('storage',event=>{if(event.key===VISITOR_SESSION_KEY&&!event.newValue)location.reload()});
  const modal=visitorNode('accountSettingsModal'),status=visitorNode('accountSettingsStatus');
  function open(view){
    visitorNode('accountEditProfileForm').hidden=view!=='profile';visitorNode('accountChangePasswordForm').hidden=view!=='password';
    visitorNode('accountSettingsTitle').textContent=view==='profile'?'My Profile / मेरा प्रोफाइल':'Change Password / पासवर्ड बदलें';
    const profile=window.dashboardVisitorProfile||data.profile;
    visitorNode('accountEditName').value=profile.first_name||profile.name.split(' ')[0];visitorNode('accountEditMiddleName').value=profile.middle_name||(profile.first_name?'':profile.name.split(' ').slice(1,-1).join(' '));visitorNode('accountEditLastName').value=profile.last_name||(profile.first_name||!profile.name.includes(' ')?'':profile.name.split(' ').at(-1)); visitorNode('accountEditDistrict').value=profile.district;visitorNode('accountEditMobile').value=profile.mobile;accountTehsilOptions('accountEditDistrict','accountEditTehsil',profile.tehsil);
    status.textContent='';modal.classList.add('open');modal.setAttribute('aria-hidden','false');visitorNode('telegramProfileMenu').classList.remove('open');
  }
  visitorNode('accountProfileBtn').onclick=()=>open('profile');visitorNode('accountPasswordBtn').onclick=()=>open('password');
  visitorNode('accountSettingsClose').onclick=()=>{modal.classList.remove('open');modal.setAttribute('aria-hidden','true');for(const id of ['accountCurrentPassword','accountChangeNew','accountChangeConfirm'])visitorNode(id).value=''};
  visitorNode('accountEditProfileForm').onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;const button=form.querySelector('button');button.disabled=true;status.textContent='Profile save हो रहा है…';
    const token=localStorage.getItem(VISITOR_SESSION_KEY);
    try{const result=await accountPost('/profile',{session_token:token,first_name:visitorNode('accountEditName').value.trim(),middle_name:visitorNode('accountEditMiddleName').value.trim(),last_name:visitorNode('accountEditLastName').value.trim(),tehsil:visitorNode('accountEditTehsil').value,district:visitorNode('accountEditDistrict').value.trim()});
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
  if(Number.isFinite(data.server_time))accountClockOffset=data.server_time*1000-Date.now();
  if(data.code==='maintenance')accountApplyMaintenance();
  if(!response.ok||!data.ok){const error=new Error(data.message||'Server से जवाब नहीं आया। फिर प्रयास करें।');error.status=response.status;error.code=data.code;throw error}
  return data;
}
function accountEnsureProfile(data){
  if(!data.profile_corrections?.length)return Promise.resolve(data);
  accountShowLoginLoading(false);
  const profile=data.profile,form=visitorNode('accountRepairForm'),status=visitorNode('visitorStatus');
  visitorNode('accountTabs').hidden=true;
  for(const name of ['SignIn','SignUp','Forgot','Reset'])visitorNode('account'+name+'Form').hidden=true;
  for(const name of ['SignIn','Forgot'])visitorNode('account'+name+'Pane').hidden=true;
  visitorNode('accountGateHeading').textContent='अपना नाम सही करें';
  visitorNode('accountRepairName').value=profile.first_name||String(profile.name||'').split(' ')[0];
  // Show the saved value so the bidder can replace a mobile number with the correct name.
  visitorNode('accountRepairMiddleName').value=profile.middle_name||'';
  visitorNode('accountRepairLastName').value=profile.last_name||'';
  const messages=[];
  if(data.profile_corrections.includes('last_name'))messages.push('Surname / उपनाम भरना जरूरी है।');
  if(data.profile_corrections.includes('middle_name'))messages.push('Middle Name में अंक या Mobile Number है। सही Middle Name लिखें, या न हो तो खाली छोड़ें।');
  visitorNode('accountRepairNotice').textContent=messages.join(' ');
  form.hidden=false;status.textContent='';
  accountBindMiddleName('accountRepairMiddleName');
  visitorNode('accountRepairLogout').onclick=()=>{
    accountPost('/logout',{session_token:data.session_token}).catch(()=>{});
    localStorage.removeItem(VISITOR_SESSION_KEY);localStorage.removeItem(VISITOR_PROFILE_KEY);location.reload();
  };
  return new Promise(resolve=>{
    form.onsubmit=async event=>{
      event.preventDefault();const button=form.querySelector('button[type="submit"]');
      if(button.disabled||!form.reportValidity())return;
      button.disabled=true;status.textContent='सही जानकारी save हो रही है…';
      try{
        const result=await accountPost('/profile-names',{session_token:data.session_token,first_name:visitorNode('accountRepairName').value.trim(),middle_name:visitorNode('accountRepairMiddleName').value.trim(),last_name:visitorNode('accountRepairLastName').value.trim()});
        if(result.profile_corrections?.length)throw new Error('कृपया नाम की जानकारी पूरी करें।');
        form.hidden=true;resolve({...data,profile:result.profile,profile_corrections:[]});
      }catch(error){status.textContent=error.message;if(error.status===401)accountLogout(error.message)}
      finally{button.disabled=false}
    };
  });
}
async function accountUnlock(data){
  data=await accountEnsureProfile(data);
  visitorUnlock(data);
  visitorNode('telegramLogoutBtn').onclick=()=>accountLogout();
  accountSetupControls(data);
  if(data.details_id){
    const token=data.session_token;
    (async()=>{
      const deadline=Date.now()+300000;
      while(localStorage.getItem(VISITOR_SESSION_KEY)===token && Date.now()<deadline){
        const result=await accountPost('/details',{session_token:token,details_id:data.details_id});
        if(result.pending){await new Promise(resolve=>setTimeout(resolve,2000));continue}
        if(localStorage.getItem(VISITOR_SESSION_KEY)===token && !window.dashboardAffidavitProfileEdited)window.dashboardAffidavitProfile=result.affidavit_profile||{};
        return;
      }
    })().catch(()=>{ /* Saved local affidavit details remain available if enrichment is delayed. */ });
  }
  for(const id of ['accountLoginPassword','accountNewPassword','accountConfirmPassword','accountResetPassword','accountResetConfirm'])if(visitorNode(id))visitorNode(id).value='';
}
const accountBusyForms=new Set();
let accountLoadingFocus=null;
function accountShowLoginLoading(show,name='SignIn'){
  const overlay=visitorNode('accountLoginLoading'),gate=visitorNode('telegramGate');
  if(!overlay)return;
  overlay.hidden=!show;
  overlay.setAttribute('aria-busy',String(show));
  if(gate)gate.inert=show;
  if(show){
    visitorNode('accountLoadingTitle').textContent=name==='SignUp'?'Sign up हो रहा है…':'Sign in हो रहा है…';
    accountLoadingFocus=document.activeElement;visitorNode('accountLoadingTitle')?.focus?.();
    overlay.onkeydown=event=>{
      if(event.key!=='Tab')return;
      const links=[...overlay.querySelectorAll('a[href]:not([hidden])')];
      if(!links.length){event.preventDefault();return;}
      if(event.shiftKey&&(document.activeElement===links[0]||document.activeElement===visitorNode('accountLoadingTitle'))){event.preventDefault();links.at(-1).focus();}
      else if(!event.shiftKey&&document.activeElement===links.at(-1)){event.preventDefault();links[0].focus();}
    };
  }
  else{const previous=accountLoadingFocus;accountLoadingFocus=null;if(previous?.isConnected!==false)previous?.focus?.();}
}
function accountFormReady(name){
  if(accountMaintenanceActive())return false;
  const value=id=>visitorNode(id)?.value||'';
  const mobile=id=>/^[6-9][0-9]{9}$/.test(value(id));
  const password=id=>{const p=value(id);return p.length>=8&&p.length<=128&&[/[A-Z]/,/[a-z]/,/[0-9]/,/[^A-Za-z0-9\s]/].every(test=>test.test(p))};
  if(name==='SignIn')return mobile('accountLoginMobile')&&value('accountLoginPassword').length>0&&value('accountLoginPassword').length<=128;
  if(name==='Forgot')return mobile('accountForgotMobile');
  if(name==='Reset')return password('accountResetPassword')&&value('accountResetPassword')===value('accountResetConfirm');
  const first=value('visitorName').trim(),middle=value('visitorMiddleName').trim(),last=value('visitorLastName').trim();
  return mobile('visitorMobile')&&first.length>=2&&first.length<=15&&middle.length<=10&&middle===accountMiddleNameValue(middle)&&last.length>=1&&last.length<=15&&
    (accountTehsilDirectory[value('visitorDistrict').trim()]||[]).includes(value('visitorTehsil'))&&password('accountNewPassword')&&value('accountNewPassword')===value('accountConfirmPassword');
}
function accountUpdateSubmit(name){
  const button=visitorNode('account'+name+'Form')?.querySelector('button[type="submit"]');if(!button)return;
  button.disabled=accountBusyForms.has(name)||!accountFormReady(name);
  button.setAttribute('aria-busy',String(accountBusyForms.has(name)));
}
function accountSetBusy(name,busy){
  if(busy)accountBusyForms.add(name);else accountBusyForms.delete(name);
  if(name==='SignIn'||name==='SignUp')accountShowLoginLoading(busy,name);
  accountUpdateSubmit(name);
}
function accountUpdateAllSubmits(){for(const name of ['SignIn','SignUp','Forgot','Reset'])accountUpdateSubmit(name)}
async function enforceAccountAccess(){
  // Begin backend startup while the user fills the login form.
  fetch('https://mp-tenders-api.onrender.com/api/accounts/ready',{cache:'no-store'}).then(r=>r.json()).then(data=>{if(Number.isFinite(data.server_time))accountClockOffset=data.server_time*1000-Date.now();accountApplyMaintenance()}).catch(()=>{});
  const status=visitorNode('visitorStatus'),forms=['SignIn','SignUp','Forgot','Reset'];
  function mode(name){
    visitorNode('telegramGate').classList.toggle('account-signup',name==='SignUp');
    visitorNode('accountGateHeading').textContent={SignIn:'Welcome back / स्वागत है',SignUp:'Create your account / नया अकाउंट बनाएं',Forgot:'Password भूल गए?',Reset:'Set new password / नया Password'}[name];
    for(const pane of ['SignIn','Forgot'])visitorNode('account'+pane+'Pane').hidden=pane!==name;
    for(const current of forms){visitorNode('account'+current+'Form').hidden=current!==name;visitorNode('account'+current+'Tab')?.classList.toggle('active',current===name)}
    status.textContent='';accountUpdateAllSubmits();
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
  for(const name of forms){
    const form=visitorNode('account'+name+'Form');
    for(const event of ['input','change'])form.addEventListener(event,()=>accountUpdateSubmit(name));
  }
  mode('SignIn');
  accountApplyMaintenance();
  if(accountMaintenanceTimer)clearInterval(accountMaintenanceTimer);
  accountMaintenanceTimer=setInterval(accountApplyMaintenance,1000);
  for(const id of ['accountLoginMobile','visitorMobile','accountForgotMobile'])accountBindMobile(id);
  for(const id of ['visitorMiddleName','accountEditMiddleName'])accountBindMiddleName(id);
  try{const cached=JSON.parse(localStorage.getItem(VISITOR_PROFILE_KEY)||'{}');visitorNode('accountLoginMobile').value=String(cached.mobile||'').replace(/^\+91/,'')}catch(_){}
  for(const [district,tehsil] of [['accountEditDistrict','accountEditTehsil']])visitorNode(district).addEventListener('input',()=>{accountTehsilOptions(district,tehsil);accountUpdateAllSubmits()});
  fetch('data/mp_tehsils.json',{cache:'no-store'}).then(r=>r.json()).then(data=>{
    accountTehsilDirectory=data.districts||{};
    for(const district of Object.keys(accountTehsilDirectory))for(const id of ['accountDistrictList']){const option=document.createElement('option');option.value=district;visitorNode(id)?.appendChild(option)}
    visitorNode('visitorDistrict').disabled=false;
    let selectedDistrict='';
    accountBindPlacePicker('visitorDistrict','visitorDistrictList',()=>Object.keys(accountTehsilDirectory),()=>{
      const district=visitorNode('visitorDistrict').value;
      const selected=Object.hasOwn(accountTehsilDirectory,district)?district:'';
      if(selected!==selectedDistrict){selectedDistrict=selected;accountTehsilOptions('visitorDistrict','visitorTehsil')}
      accountUpdateAllSubmits();
    });
    accountBindPlacePicker('visitorTehsil','visitorTehsilList',()=>accountTehsilDirectory[visitorNode('visitorDistrict').value]||[],()=>accountUpdateAllSubmits());
    accountTehsilOptions('visitorDistrict','visitorTehsil');accountTehsilOptions('accountEditDistrict','accountEditTehsil',window.dashboardVisitorProfile?.tehsil);accountUpdateAllSubmits();
  }).catch(()=>{status.textContent='जिला/तहसील सूची नहीं मिली। Page reload करें।'});
  let resetToken='';
  if(location.hash.startsWith('#reset=')){resetToken=location.hash.slice(7);history.replaceState(null,'',location.pathname+location.search);mode('Reset')}
  const forgotLink=visitorNode('accountForgotWhatsApp');
  function clearForgotLink(){forgotLink.hidden=true;forgotLink.removeAttribute('href')}
  visitorNode('accountForgotMobile').addEventListener('input',clearForgotLink);
  visitorNode('accountForgotForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;if(accountBusyForms.has('Forgot')||!accountFormReady('Forgot')||!form.reportValidity())return;
    clearForgotLink();const mobile=visitorNode('accountForgotMobile').value.trim(),button=form.querySelector('button');accountSetBusy('Forgot',true);
    status.textContent='Registered account check हो रहा है…';
    try{
      await accountPost('/forgot-check',{mobile});
      if(visitorNode('accountForgotMobile').value.trim()!==mobile)return;
      const text='MP Tender Dashboard: मेरा tenders.codinglms.xyz ka password भूल गया हूँ। Registered mobile: '+mobile+' . कृपया account verify करके password reset link दें।';
      forgotLink.href='https://wa.me/919893610244?text='+encodeURIComponent(text);forgotLink.hidden=false;
      status.textContent='Account registered है। नीचे WhatsApp link से message भेजें; पहचान verify होने पर reset link मिलेगा।';
    }catch(error){if(error.code==='signup_required')signupRequired(mobile,'',error.message);else status.textContent=error.message}
    finally{accountSetBusy('Forgot',false)}
  });
  visitorNode('accountResetForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;if(accountBusyForms.has('Reset')||!accountFormReady('Reset')||!form.reportValidity())return;
    accountSetBusy('Reset',true);
    try{const result=await accountPost('/reset-password',{reset_token:resetToken,password:visitorNode('accountResetPassword').value,confirm_password:visitorNode('accountResetConfirm').value});resetToken='';mode('SignIn');status.textContent=result.message;visitorNode('accountResetPassword').value='';visitorNode('accountResetConfirm').value=''}
    catch(error){status.textContent=error.message}finally{accountSetBusy('Reset',false)}
  });
  return new Promise(resolve=>{
    for(const [name,path] of [['SignIn','/signin'],['SignUp','/signup']])visitorNode('account'+name+'Form').addEventListener('submit',async event=>{
      event.preventDefault();const form=event.currentTarget;if(accountBusyForms.has(name)||!accountFormReady(name)||!form.reportValidity())return;
      accountSetBusy(name,true);status.textContent='Account check हो रहा है…';
      const payload=name==='SignIn'?{mobile:visitorNode('accountLoginMobile').value.trim(),password:visitorNode('accountLoginPassword').value}:{first_name:visitorNode('visitorName').value.trim(),middle_name:visitorNode('visitorMiddleName').value.trim(),last_name:visitorNode('visitorLastName').value.trim(),tehsil:visitorNode('visitorTehsil').value,mobile:visitorNode('visitorMobile').value.trim(),district:visitorNode('visitorDistrict').value.trim(),password:visitorNode('accountNewPassword').value,confirm_password:visitorNode('accountConfirmPassword').value};
      try{await accountUnlock(await accountPost(path,payload));resolve(true)}catch(error){
        if(name==='SignIn'&&error.code==='signup_required')signupRequired(payload.mobile,payload.password,error.message);
        else if(name==='SignUp'&&(error.code==='account_exists'||error.status===409))existingAccount(payload.mobile,'आप पहले से Sign up हैं। अब केवल password डालकर Sign in करें।');
        else status.textContent=error.message;
      }finally{accountSetBusy(name,false);accountUpdateAllSubmits()}
    });
    const token=localStorage.getItem(VISITOR_SESSION_KEY);
    if(token&&!resetToken&&!accountMaintenanceActive()){status.textContent='Saved login check हो रहा है…';accountPost('/session',{session_token:token}).then(async data=>{await accountUnlock(data);resolve(true)}).catch(error=>{if(error.status===401)localStorage.removeItem(VISITOR_SESSION_KEY);status.textContent=error.message})}
  });
}
