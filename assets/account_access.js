async function accountPost(path,body){
  const response=await fetch('https://mp-tenders-api.onrender.com/api/accounts'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});
  const data=await response.json().catch(()=>({}));
  if(!response.ok||!data.ok){const error=new Error(data.message||'Server से जवाब नहीं आया। फिर प्रयास करें।');error.status=response.status;throw error}
  return data;
}
function accountUnlock(data){
  visitorUnlock(data);
  // Revoke this session on the server; leave other devices signed in.
  visitorNode('telegramLogoutBtn').onclick=async()=>{
    try{await accountPost('/logout',{session_token:localStorage.getItem(VISITOR_SESSION_KEY)});localStorage.removeItem(VISITOR_SESSION_KEY);localStorage.removeItem(VISITOR_PROFILE_KEY);location.reload()}
    catch(error){alert(error.message)}
  };
  for(const id of ['accountLoginPassword','accountNewPassword','accountConfirmPassword','accountResetPassword','accountResetConfirm'])if(visitorNode(id))visitorNode(id).value='';
}
async function enforceAccountAccess(){
  const status=visitorNode('visitorStatus'),forms=['SignIn','SignUp','Forgot','Reset'];
  function mode(name){
    for(const current of forms){visitorNode('account'+current+'Form').hidden=current!==name;visitorNode('account'+current+'Tab')?.classList.toggle('active',current===name)}
    status.textContent='';
  }
  for(const name of forms.slice(0,3))visitorNode('account'+name+'Tab').onclick=()=>mode(name);
  mode('SignIn');
  try{const cached=JSON.parse(localStorage.getItem(VISITOR_PROFILE_KEY)||'{}');visitorNode('accountLoginMobile').value=String(cached.mobile||'').replace(/^\+91/,'')}catch(_){}
  fetch('data/mp_districts.json',{cache:'no-store'}).then(r=>r.json()).then(data=>{for(const district of data.districts||[]){const option=document.createElement('option');option.value=district.name;visitorNode('visitorDistrictList').appendChild(option)}}).catch(()=>{});
  let resetToken='';
  if(location.hash.startsWith('#reset=')){resetToken=location.hash.slice(7);history.replaceState(null,'',location.pathname+location.search);mode('Reset')}
  visitorNode('accountForgotForm').addEventListener('submit',event=>{
    event.preventDefault();if(!event.currentTarget.reportValidity())return;
    const text='MP Tender Dashboard: मेरा password भूल गया हूँ। Registered mobile: '+visitorNode('accountForgotMobile').value.trim()+'. कृपया account verify करके password reset link दें।';
    window.open('https://wa.me/919893610244?text='+encodeURIComponent(text),'_blank','noopener');
    status.textContent='WhatsApp में message भेजें। पहचान verify होने के बाद reset link मिलेगा।';
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
      try{accountUnlock(await accountPost(path,payload));resolve(true)}catch(error){status.textContent=error.message;button.disabled=false}
    });
    const token=localStorage.getItem(VISITOR_SESSION_KEY);
    if(token&&!resetToken){status.textContent='Saved login check हो रहा है…';accountPost('/session',{session_token:token}).then(data=>{accountUnlock(data);resolve(true)}).catch(error=>{if(error.status===401)localStorage.removeItem(VISITOR_SESSION_KEY);status.textContent=error.message})}
  });
}
