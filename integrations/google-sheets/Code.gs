// Bound Apps Script for the user's private MP Tenders Sheet.
// Set MP_TENDERS_SHARED_SECRET in Script Properties; never put it in this file.
const MP_TENDER_SHEET_ID = '1VHILTCBB-CR0srqOTmaxf0b17wWJCpaOuMpVp_KphKw';
const MP_SCHEMA = {
  Users: ['user_id','name','mobile','district','signup_at','last_visit_at','visit_count','mobile_verified'],
  VisitorSessions: ['visitor_id','user_id','name','mobile','district','signup_at','last_visit_at','visit_count'],
  AffidavitProfiles: ['visitor_id','user_id','bidderName','firmName','status','place','relative','relativeName','relativePost','relativePosting','updated_at','last_request_id','parentName','parentRelation','address'],
  VisitEvents: ['request_id','visitor_id','user_id','visited_at','event'],
  Accounts: ['user_id','mobile','revision','record_json'],
  AccountRateLimits: ['rate_key','window_start','attempts']
};

function mpBook_(){return SpreadsheetApp.openById(MP_TENDER_SHEET_ID);}
function mpTables_(book,names){
  const tables={};
  (names||Object.keys(MP_SCHEMA)).forEach(name=>{
    const headers=MP_SCHEMA[name];
    let sheet=book.getSheetByName(name);
    if(!sheet)sheet=book.insertSheet(name);
    if(sheet.getLastRow()===0){
      sheet.getRange(1,1,1,headers.length).setValues([headers]);
      sheet.setFrozenRows(1);
      sheet.getRange(1,1,1,headers.length).setFontWeight('bold').setBackground('#e8effa');
      sheet.autoResizeColumns(1,headers.length);
    }else{
      const found=sheet.getRange(1,1,1,headers.length).getDisplayValues()[0];
      if(name==='AffidavitProfiles' && found.slice(0,12).join('|')===headers.slice(0,12).join('|') && found.slice(12).every(value=>!value)){sheet.getRange(1,13,1,3).setValues([headers.slice(12)]);}
      else if(found.join('|')!==headers.join('|'))throw Error('Unexpected headers in '+name+'. Existing data was preserved.');
    }
    tables[name]=sheet;
  });
  return tables;
}

// Run once as the Sheet owner; this authorizes the script and prepares tabs.
function setupMpTenders(){
  const lock=LockService.getScriptLock();lock.waitLock(20000);
  try{mpTables_(mpBook_());SpreadsheetApp.flush();}finally{lock.releaseLock();}
}
function mpRows_(sheet){
  if(sheet.getLastRow()<2)return [];
  const keys=MP_SCHEMA[sheet.getName()];
  return sheet.getRange(2,1,sheet.getLastRow()-1,keys.length).getValues().map((values,index)=>{
    const row={_row:index+2};keys.forEach((key,i)=>row[key]=values[i]);return row;
  });
}
function mpWrite_(sheet,row){
  const keys=MP_SCHEMA[sheet.getName()],position=row._row||sheet.getLastRow()+1;
  // Explicit text format protects mobile leading '+' and formula-like names.
  const range=sheet.getRange(position,1,1,keys.length);range.setNumberFormat('@');
  range.setValues([keys.map(key=>{
    const value=row[key]===undefined?'':row[key];
    return typeof value==='string'&&/^[=+\-@]/.test(value)?"'"+value:value;
  })]);
  row._row=position;
}
function mpPublic_(row){const copy={...row};delete copy._row;return copy;}
function mpError_(status,message){const error=Error(message);error.status=status;throw error;}
function mpNow_(){return Utilities.formatDate(new Date(),'Asia/Kolkata',"yyyy-MM-dd'T'HH:mm:ssXXX");}
function mpUuid_(value){return /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(String(value||''));}
function mpText_(value,max){const text=String(value||'').trim();if(text.length>max)mpError_(400,'Profile field is too long.');return text;}
function mpSignature_(timestamp,payload,secret){
  return Utilities.computeHmacSha256Signature(timestamp+'\n'+payload,secret,Utilities.Charset.UTF_8)
    .map(byte=>('0'+((byte+256)%256).toString(16)).slice(-2)).join('');
}
function mpEqual_(a,b){if(a.length!==b.length)return false;let diff=0;for(let i=0;i<a.length;i++)diff|=a.charCodeAt(i)^b.charCodeAt(i);return diff===0;}
function mpEvent_(tables,visitor,request,event){
  if(mpRows_(tables.VisitEvents).some(row=>row.request_id===request.request_id))return false;
  mpWrite_(tables.VisitEvents,{request_id:request.request_id,visitor_id:visitor.visitor_id,user_id:visitor.user_id,visited_at:mpNow_(),event});return true;
}
function mpAffidavit_(tables,visitorId){
  const row=mpRows_(tables.AffidavitProfiles).find(row=>row.visitor_id===visitorId);
  if(!row)return {};
  return Object.fromEntries(['bidderName','firmName','status','place','relative','relativeName','relativePost','relativePosting','parentName','parentRelation','address'].map(key=>[key,row[key]||'']));
}
function mpHandle_(tables,p){
  if(String(p.action||'').startsWith('account_'))return mpAccountHandle_(tables,p);
  if(p.action==='list_visitors')return {visitors:mpRows_(tables.VisitorSessions).map(mpPublic_)};
  if(p.action==='list_affidavits')return {affidavits:mpRows_(tables.AffidavitProfiles).map(mpPublic_)};
  if(p.action==='status')return {spreadsheet_id:MP_TENDER_SHEET_ID,unique_users:mpRows_(tables.Users).length,visitor_sessions:mpRows_(tables.VisitorSessions).length};
  if(!mpUuid_(p.visitor_id))mpError_(400,'Invalid visitor ID.');
  let visitor=mpRows_(tables.VisitorSessions).find(row=>row.visitor_id===p.visitor_id);
  if(p.action==='register'){
    const name=mpText_(p.name,120),district=mpText_(p.district,100),mobile=String(p.mobile||'');
    if(name.length<2||district.length<2||!/^\+91[6-9][0-9]{9}$/.test(mobile))mpError_(400,'Invalid registration details.');
    if(visitor){
      if(visitor.name!==name||visitor.mobile!==mobile||visitor.district!==district)mpError_(409,'Registration request already used. Reload and retry.');
      return {visitor:mpPublic_(visitor),affidavit_profile:mpAffidavit_(tables,p.visitor_id)};
    }
    const now=mpNow_();
    let contact=mpRows_(tables.Users).find(row=>row.mobile===mobile);
    if(!contact)contact={user_id:Utilities.getUuid(),name,mobile,district,signup_at:now,last_visit_at:now,visit_count:0,mobile_verified:'No'};
    visitor={visitor_id:p.visitor_id,user_id:contact.user_id,name,mobile,district,signup_at:now,last_visit_at:now,visit_count:1};
    mpWrite_(tables.Users,contact);
    mpWrite_(tables.VisitorSessions,visitor);
    if(mpEvent_(tables,visitor,p,'register')){contact.visit_count=Number(contact.visit_count)+1;contact.last_visit_at=now;mpWrite_(tables.Users,contact);}
    return {visitor:mpPublic_(visitor),affidavit_profile:{}};
  }
  if(!visitor)mpError_(401,'Profile not found. Please save again.');
  if(p.action==='session'){
    if(mpEvent_(tables,visitor,p,'visit')){
      const now=mpNow_();visitor.visit_count=Number(visitor.visit_count)+1;visitor.last_visit_at=now;mpWrite_(tables.VisitorSessions,visitor);
      const contact=mpRows_(tables.Users).find(row=>row.user_id===visitor.user_id);
      if(contact){contact.visit_count=Number(contact.visit_count)+1;contact.last_visit_at=now;mpWrite_(tables.Users,contact);}
    }
    return {visitor:mpPublic_(visitor),affidavit_profile:mpAffidavit_(tables,p.visitor_id)};
  }
  if(p.action==='read_affidavit')return {profile:mpAffidavit_(tables,p.visitor_id)};
  if(p.action==='save_affidavit'){
    const profile=p.profile||{},keys=['bidderName','firmName','status','place','relative','relativeName','relativePost','relativePosting','parentName','parentRelation','address'];
    const old=mpRows_(tables.AffidavitProfiles).find(row=>row.visitor_id===p.visitor_id);
    const row={...(old||{}),visitor_id:p.visitor_id,user_id:visitor.user_id,updated_at:mpNow_(),last_request_id:p.request_id};
    keys.forEach(key=>row[key]=mpText_(profile[key],240));
    row.parentRelation=row.parentRelation||'S/o';
    if(!['S/o','D/o','W/o'].includes(row.parentRelation))mpError_(400,'Invalid parent relationship.');
    if(!row.bidderName||!row.firmName||!row.place||!['yes','no'].includes(row.relative))mpError_(400,'Invalid affidavit profile.');
    if(row.relative==='yes'&&(!row.relativeName||!row.relativePost||!row.relativePosting))mpError_(400,'Relative details are required.');
    if(!old||old.last_request_id!==p.request_id)mpWrite_(tables.AffidavitProfiles,row);
    return {profile:mpAffidavit_(tables,p.visitor_id)};
  }
  mpError_(400,'Unknown operation.');
}

// Private account JSON contains password hashes, hashed session/reset tokens.
// Never expose this operation or these tabs through public spreadsheet sharing.
function mpAccountHandle_(tables,p){
  const action=p.action.slice(8),rows=mpRows_(tables.Accounts);
  if(action==='lookup'||action==='get'){
    let rate_results;
    if(p.rate_checks!==undefined){
      if(action!=='lookup'||!Array.isArray(p.rate_checks)||p.rate_checks.length!==2||p.rate_checks[0].limit!==120||p.rate_checks[1].limit!==12)mpError_(400,'Invalid lookup limits.');
      rate_results=p.rate_checks.map(check=>mpAccountHandle_(tables,{action:'account_rate',...check}));
    }
    const row=rows.find(row=>action==='lookup'?row.mobile===p.mobile:row.user_id===p.user_id);
    return {record:row?JSON.parse(row.record_json):null,...(rate_results?{rate_results}:{})};
  }
  if(action==='create'){
    const record=p.record||{};
    if(!mpUuid_(record.user_id)||!/^\+91[6-9][0-9]{9}$/.test(record.mobile)||!String(record.password_hash||'').startsWith('scrypt:'))mpError_(400,'Invalid account.');
    if(rows.some(row=>row.mobile===record.mobile))mpError_(409,'यह mobile registered है। Sign in या Forgot password चुनें।');
    mpHandle_(tables,{...p,action:'register',visitor_id:record.user_id,name:record.name,mobile:record.mobile,district:record.district});
    record.revision=1;mpWrite_(tables.Accounts,{user_id:record.user_id,mobile:record.mobile,revision:1,record_json:JSON.stringify(record)});
    return {record};
  }
  if(action==='update'){
    const record=p.record||{},row=rows.find(row=>row.user_id===record.user_id);
    if(!row||Number(row.revision)!==Number(p.expected_revision))return {updated:false};
    if(row.mobile!==record.mobile)mpError_(400,'Account mobile cannot be changed through this operation.');
    record.revision=Number(p.expected_revision)+1;
    mpWrite_(tables.Accounts,{...row,revision:record.revision,record_json:JSON.stringify(record)});return {updated:true};
  }
  if(action==='rate'){
    if(!/^[a-f0-9]{64}$/.test(String(p.key||''))||![12,120].includes(p.limit))mpError_(400,'Invalid limit.');
    const start=Math.floor(Date.now()/900000)*900;
    let row=mpRows_(tables.AccountRateLimits).find(row=>row.rate_key===p.key);
    if(!row)row={rate_key:p.key,window_start:start,attempts:0};
    row.attempts=Number(row.window_start)===start?Number(row.attempts)+1:1;row.window_start=start;
    mpWrite_(tables.AccountRateLimits,row);return {allowed:row.attempts<=p.limit};
  }
  mpError_(400,'Unknown account operation.');
}
function doPost(event){
  let lock;
  try{
    const raw=event&&event.postData&&event.postData.contents;if(!raw||raw.length>20000)mpError_(400,'Invalid request.');
    const envelope=JSON.parse(raw),secret=PropertiesService.getScriptProperties().getProperty('MP_TENDERS_SHARED_SECRET')||'';
    const timestamp=String(envelope.timestamp||''),payload=String(envelope.payload||'');
    if(secret.length<32||Math.abs(Date.now()/1000-Number(timestamp))>180||!/^\d+$/.test(timestamp)||!mpEqual_(String(envelope.signature||''),mpSignature_(timestamp,payload,secret)))mpError_(401,'Request authorization failed.');
    const request=JSON.parse(payload);
    if(request.spreadsheet_id!==MP_TENDER_SHEET_ID||!mpUuid_(request.request_id))mpError_(400,'Invalid Sheet request.');
    lock=LockService.getScriptLock();lock.waitLock(20000);
    const account=String(request.action||'').slice(8);
    const names=String(request.action||'').startsWith('account_')&&['lookup','get','update','rate'].includes(account)
      ? (account==='rate'?['Accounts','AccountRateLimits']:['Accounts',...(request.rate_checks?['AccountRateLimits']:[])]) : undefined;
    const result=mpHandle_(mpTables_(mpBook_(),names),request);SpreadsheetApp.flush();
    return ContentService.createTextOutput(JSON.stringify({ok:true,...result})).setMimeType(ContentService.MimeType.JSON);
  }catch(error){return ContentService.createTextOutput(JSON.stringify({ok:false,status:error.status||503,message:error.status?error.message:'Sheet operation failed. Please retry.'})).setMimeType(ContentService.MimeType.JSON);}
  finally{if(lock&&lock.hasLock())lock.releaseLock();}
}
