/* Serveone Desktop V0.1.13 · OAuth desktop configuration form + one sync per session.
 * Sync is background-only; loaded views always use offline local records.
 */
(function(){
'use strict';
const URLS={import:'/Import',vendor:'/Vendor_Control'};
const attempted=new Set();
let current='',panel=null,label=null,credentialButton=null,syncButton=null;
let lastState='',previousSuccess={import:'',vendor:''},lastPoll=0,checking=false;
let oauthConnecting=false,oauthWasConnected=false;
let menuStatus='Google Sheets',menuState='idle';
const api=async(path,options={})=>{
 const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...options});
 const data=await r.json().catch(()=>({}));
 if(!r.ok)throw Error(data.error||'HTTP '+r.status);
 return data;
};
const json=payload=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
function route(){
 const path=decodeURIComponent(window.location.pathname).replace(/\/+$/,'').toLowerCase();
 if(path==='/import')return 'import';
 if(path==='/vendor_control'||path==='/vendor')return 'vendor';
 return '';
}
let setupModal=null, setupMessage=null, setupClientId=null, setupClientSecret=null, setupClientFile=null, setupExisting=null;
function modalMessage(message,kind){
 if(!setupMessage)return;
 setupMessage.textContent=message||'';
 setupMessage.dataset.kind=kind||'info';
}
function closeSetup(){if(setupModal)setupModal.hidden=true;}
function createSetupModal(){
 if(setupModal)return;
 setupModal=document.createElement('div');
 setupModal.id='zd-google-setup-overlay';setupModal.hidden=true;
 setupModal.innerHTML=`
 <div class="zd-google-setup-dialog" role="dialog" aria-modal="true" aria-labelledby="zd-google-title">
  <div class="zd-google-setup-header"><div><h2 id="zd-google-title">Google Sheets · Read-only Login</h2>
   <p>Connect Google using an OAuth Desktop JSON file or enter the credentials manually.</p></div>
   <button type="button" data-google-act="close" class="zd-google-close" aria-label="Close">×</button></div>
  <div class="zd-google-setup-body">
   <div class="zd-google-steps"><strong>Get an OAuth Desktop JSON file</strong>
    <ol><li>Open <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noopener noreferrer">Google Cloud Credentials</a> and select your project.</li>
     <li>Enable <strong>Google Sheets API</strong> in the <a href="https://console.cloud.google.com/apis/library/sheets.googleapis.com" target="_blank" rel="noopener noreferrer">API Library</a>.</li>
     <li>Configure Google Auth Platform and add your account as a test user when the app is in Testing.</li>
     <li>Create an <strong>OAuth client ID</strong> with application type <strong>Desktop app</strong>, then download its JSON file.</li></ol>
   </div>
   <p class="zd-google-existing" data-google-existing hidden>OAuth configuration is saved in Windows. You can log in now or replace it below.</p>
   <form id="zd-google-client-form" autocomplete="off">
    <label for="zd-google-client-file">Import OAuth Desktop JSON</label>
    <input id="zd-google-client-file" name="client-file" type="file" accept=".json,application/json" />
    <p class="zd-google-note">Select the downloaded OAuth Desktop client JSON. Service account files and private keys are rejected.</p>
    <div class="zd-google-divider"><span>or enter manually</span></div>
    <label for="zd-google-client-id">OAuth Desktop Client ID</label>
    <input id="zd-google-client-id" name="client-id" type="text" spellcheck="false" placeholder="123456789-xxxx.apps.googleusercontent.com" maxlength="512" autocomplete="off" />
    <label for="zd-google-client-secret">OAuth Desktop Client Secret</label>
    <input id="zd-google-client-secret" name="client-secret" type="password" spellcheck="false" placeholder="GOCSPX-..." maxlength="1024" autocomplete="new-password" />
    <p class="zd-google-note">Only <code>spreadsheets.readonly</code> access is requested. Credentials and tokens are encrypted for this Windows account.</p>
    <p class="zd-google-modal-status" data-google-status role="status"></p>
    <div class="zd-google-modal-actions">
     <button type="button" class="zd-google-secondary" data-google-act="close">Cancel</button>
     <button type="button" class="zd-google-secondary" data-google-act="connect" hidden>Login with saved settings</button>
     <button type="submit" class="zd-google-primary">Save &amp; Login Google</button>
    </div>
   </form>
  </div>
 </div>`;
 document.body.appendChild(setupModal);
 setupMessage=setupModal.querySelector('[data-google-status]');
 setupClientId=setupModal.querySelector('#zd-google-client-id');
 setupClientSecret=setupModal.querySelector('#zd-google-client-secret');
 setupClientFile=setupModal.querySelector('#zd-google-client-file');
 setupExisting=setupModal.querySelector('[data-google-existing]');
 setupModal.querySelectorAll('[data-google-act="close"]').forEach(btn=>btn.addEventListener('click',closeSetup));
 setupModal.addEventListener('click',event=>{if(event.target===setupModal)closeSetup();});
 setupModal.querySelector('[data-google-act="connect"]').addEventListener('click',async()=>{
  try{await connectGoogle();closeSetup();}
  catch(e){modalMessage('Login could not start: '+e.message,'error');}
 });
 setupClientFile.addEventListener('change',async()=>{
  const file=setupClientFile.files?.[0];if(!file)return;
  if(file.size>32768){modalMessage('OAuth JSON file is too large.','error');setupClientFile.value='';return;}
  setupClientFile.disabled=true;
  try{
   const payload=JSON.parse(await file.text());
   await api('/api/desktop/sheets/credentials',jsonPut(payload));
   modalMessage('OAuth configuration saved. Opening Google login...','info');
   await connectGoogle();closeSetup();
  }catch(e){modalMessage('JSON import failed: '+e.message,'error');}
  finally{setupClientFile.disabled=false;setupClientFile.value='';}
 });
 setupModal.querySelector('#zd-google-client-form').addEventListener('submit',async(event)=>{
  event.preventDefault();
  const id=setupClientId.value.trim(),secret=setupClientSecret.value.trim();
  if(!id.endsWith('.apps.googleusercontent.com')){
   modalMessage('Enter an OAuth Desktop Client ID ending in .apps.googleusercontent.com.','error');return;
  }
  const submit=setupModal.querySelector('button[type="submit"]');submit.disabled=true;
  try{
   await api('/api/desktop/sheets/credentials',jsonPut({client_id:id,client_secret:secret}));
   setupClientId.value='';setupClientSecret.value='';
   await connectGoogle();closeSetup();
  }catch(e){modalMessage('Setup failed: '+e.message,'error');}
  finally{submit.disabled=false;}
 });
}
function jsonPut(payload){return {...json(payload),method:'PUT'};}
async function connectGoogle(){
 const response=await api('/api/desktop/sheets/connect',json({}));
 oauthConnecting=true;
 show(response.started?'Google browser login is opening':'Google login is in progress','syncing');
 return response;
}
async function openSetup(){
 createSetupModal();
 setupModal.hidden=false;setupClientId.value='';setupClientSecret.value='';setupClientFile.value='';modalMessage('','info');
 const savedBtn=setupModal.querySelector('[data-google-act="connect"]');
 savedBtn.hidden=true;setupExisting.hidden=true;
 try{
  const state=await api('/api/desktop/sheets/credentials');
  savedBtn.hidden=!state.oauth_client_configured;
  setupExisting.hidden=!state.oauth_client_configured;
 }catch(e){modalMessage('Could not check OAuth status: '+e.message,'error');}
 setupClientFile.focus();
}
function mount(){
 if(panel)return;
 panel=document.createElement('aside');
 panel.id='zd-sheets-status';panel.hidden=true;
 panel.setAttribute('role','status');panel.setAttribute('aria-live','polite');
 panel.innerHTML='<span class="zd-sheets-dot" aria-hidden="true"></span><span class="zd-sheets-label"></span><button type="button" class="zd-sheets-btn" data-act="sync" title="Sync Google Sheets now">Sync Now</button><button type="button" class="zd-sheets-btn" data-act="setup" title="Read-only Google login for private Sheets">Google Login</button>';
 document.body.appendChild(panel);
 label=panel.querySelector('.zd-sheets-label');
 syncButton=panel.querySelector('[data-act="sync"]');
 credentialButton=panel.querySelector('[data-act="setup"]');
 syncButton.onclick=()=>start('manual');
 credentialButton.onclick=openSetup;
}
function menuButton(label,action){
 const button=document.createElement('button');button.type='button';button.className='zd-google-menu-item';button.textContent=label;
 button.addEventListener('mousedown',event=>event.preventDefault());
 button.addEventListener('click',()=>{action();closeGoogleMenu();});return button;
}
function fillGoogleMenu(menu){
 if(!menu||menu.querySelector('[data-google-menu-actions]'))return;
 const box=document.createElement('div');box.dataset.googleMenuActions='1';box.className='zd-google-menu-actions';
 const info=document.createElement('div');info.className='zd-google-menu-status';info.dataset.state=menuState;info.textContent=menuStatus;box.appendChild(info);
 box.appendChild(menuButton('Google Login',openSetup));menu.appendChild(box);
}
function closeGoogleMenu(){document.querySelectorAll?.('.zd-google-owned-menu').forEach(menu=>menu.hidden=true);}
function pageHeading(name){return [...(document.querySelectorAll?.('h2')||[])].find(node=>(node.textContent||'').trim()===name);}
function mountImportActions(){
 const heading=pageHeading('Import'),row=heading?.parentElement?.parentElement,trigger=row?.querySelector('button[title="More actions"]');if(!trigger)return;
 if(!trigger.dataset.googleBound){trigger.dataset.googleBound='1';trigger.addEventListener('click',()=>{queueMicrotask(mountImportActions);if(typeof setTimeout==='function')setTimeout(mountImportActions,0);});}
 const menu=[...trigger.parentElement.children].find(node=>node!==trigger&&node.tagName==='DIV');fillGoogleMenu(menu);
}
function mountVendorActions(){
 const heading=pageHeading('Vendor'),row=heading?.parentElement?.parentElement,actions=row?.lastElementChild;if(!actions||actions.querySelector('[data-google-vendor-more]'))return;
 const wrap=document.createElement('div');wrap.className='zd-google-more-wrap';wrap.dataset.googleVendorMore='1';
 const trigger=document.createElement('button');trigger.type='button';trigger.className='zd-google-more-trigger';trigger.textContent='More';trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');
 const menu=document.createElement('div');menu.className='zd-google-owned-menu';menu.hidden=true;menu.setAttribute('role','menu');fillGoogleMenu(menu);
 trigger.addEventListener('click',event=>{event.stopPropagation();menu.hidden=!menu.hidden;trigger.setAttribute('aria-expanded',String(!menu.hidden));});wrap.append(trigger,menu);actions.appendChild(wrap);
}
function mountGoogleActions(){if(current==='import')mountImportActions();if(current==='vendor')mountVendorActions();}
document.addEventListener('click',event=>{if(!event.target.closest?.('.zd-google-more-wrap'))closeGoogleMenu();});
function show(message,state){
 if(!panel)mount();
 label.textContent=message;label.title=message;
 panel.dataset.state=state||'idle';
 syncButton.disabled=state==='syncing';
 credentialButton.textContent='Google Login';
 menuStatus=message;menuState=state||'idle';document.querySelectorAll?.('.zd-google-menu-status').forEach(info=>{info.textContent=menuStatus;info.dataset.state=menuState;});mountGoogleActions();
}
async function poll(force=false){
 if(!current||checking)return;
 if(!force&&Date.now()-lastPoll<((lastState==='syncing')?3000:15000))return;
 checking=true;lastPoll=Date.now();
 try{
  const info=await api('/api/desktop/sheets/status');
  if(current!==route())return;
  const st=info[current]||{};const credential=info.credentials||{};
  const name=current==='import'?'Import':'Vendor';
  const source=st.state||'idle';
  if(credential.connecting){
   oauthConnecting=true;
   show(name+' · Complete Google login in the browser; offline data remains available','syncing');
  }else if(oauthConnecting&&credential.oauth_connected&&!oauthWasConnected){
   oauthConnecting=false;oauthWasConnected=true;
   show(name+' · Google connected (read-only). Starting sync...','syncing');
   start('manual');
  }else if(oauthConnecting&&credential.connect_error){
   oauthConnecting=false;show('Google Login: '+credential.connect_error,'error');
  }else if(source==='syncing')show(name+' · Syncing in background; offline data remains visible','syncing');
  else if(source==='completed'){
   const date=st.last_success?new Date(st.last_success).toLocaleString('id-ID'):'baru saja';
   show(name+' · Synced '+date,'completed');
   if(st.last_success&&previousSuccess[current]!==st.last_success){
    previousSuccess[current]=st.last_success;
    window.dispatchEvent(new CustomEvent('serveone:data-changed',{detail:{source:'google-sheets',page:current}}));
   }
  }else if(source==='partial'){
   show(name+' · Partial sync; existing offline data was preserved','warning');
   if(st.last_success&&previousSuccess[current]!==st.last_success){previousSuccess[current]=st.last_success;window.dispatchEvent(new CustomEvent('serveone:data-changed',{detail:{source:'google-sheets',page:current}}));}
  }
  else if(source==='failed')show(name+' · Offline. Sync failed: '+(st.error||'unavailable'),'error');
  else if(source==='needs_credentials')show(name+' · Private Sheet: connect Google with read-only access','warning');
  else if(current==='vendor'&&!info.vendor_offline_available)show('Vendor · No offline snapshot; sync required','warning');
  else show(name+' · Offline data · Use Copy Sheet when needed','idle');
  oauthWasConnected=!!credential.oauth_connected;
  lastState=source;
 }catch(_){show('Offline · Could not check sync status','error');}
 finally{checking=false;}
}
async function start(mode){
 const page=current;if(!page)return;
 try{
  if(mode==='once'&&attempted.has(page))return;
  if(mode==='once')attempted.add(page);
  const result=await api('/api/desktop/sheets/sync',json({page,mode}));
  if(result.accepted)show((page==='import'?'Import':'Vendor')+' · Syncing in background; offline data remains visible','syncing');
  await poll(true);
 }catch(e){show('Sync could not start: '+e.message,'error');}
}
function navigate(){
 mount();const next=route();if(next===current)return;
 current=next;panel.hidden=true;
 lastPoll=0;
 if(!next)return;
 show((next==='import'?'Import':'Vendor')+' · Loading offline data...','idle');
 poll(true);
 start('once');
}
// Existing page Manual Update buttons call /api/*/data?refresh=1.
// Forward only their explicit refresh request to the real async Google sync.
function manualUrl(method,url){
 if(String(method).toUpperCase()!=='GET')return false;
 try{
  const u=new URL(String(url),location.origin);
  if(u.origin!==location.origin||u.searchParams.get('refresh')!=='1')return false;
  return u.pathname==='/api/import/data'||u.pathname==='/api/vendor-control/data';
 }catch(_){return false;}
}
const originalFetch=window.fetch;
window.fetch=function(input,init){
 const method=init?.method||input?.method||'GET';
 const url=typeof input==='string'?input:input?.url;
 if(manualUrl(method,url))queueMicrotask(()=>start('manual'));
 return originalFetch.apply(this,arguments);
};
const originalOpen=XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open=function(method,url){
 if(manualUrl(method,url))queueMicrotask(()=>start('manual'));
 return originalOpen.apply(this,arguments);
};
for(const name of ['pushState','replaceState']){
 const original=history[name];
 history[name]=function(){const result=original.apply(this,arguments);queueMicrotask(navigate);return result;};
}
window.addEventListener('popstate',navigate);
window.addEventListener('pageshow',navigate);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll(true);});
function init(){mount();navigate();setInterval(()=>{navigate();mountGoogleActions();if(!document.hidden)poll();},2000);}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
