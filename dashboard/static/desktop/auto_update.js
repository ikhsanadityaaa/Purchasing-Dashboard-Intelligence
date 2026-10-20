/* Serveone Dashboard Desktop — local-only automation controls V0.1.4 */
(function(){
'use strict';
const NAMES={smro:'Search Client Odr',serp:'All Product ID',pur:'Prod Reg Status',all:'Run All Process'};
const PROCESSES=['smro','serp','pur'];
const DN=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const PRESETS=[['1month','1 Month'],['3month','3 Months'],['6month','6 Months'],['this_year','This Year'],['custom','Custom Date']];
const DEFAULT_RANGE=()=>({preset:'1month',start_date:'',end_date:'today'});
const defaultRanges=()=>Object.fromEntries(PROCESSES.map(p=>[p,DEFAULT_RANGE()]));
let schedules=[],ranges=defaultRanges(),dialog=null,timer=null,tab='processes',configured=false,username='',logContent='';
const $=(s,p=document)=>p.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateISO=()=>{const now=new Date(),day=new Date(now.getTime()-now.getTimezoneOffset()*60000);return day.toISOString().slice(0,10);};
async function api(path,opts={}){const res=await fetch(path,{credentials:'same-origin',cache:'no-store',...opts});const body=await res.json().catch(()=>({}));if(!res.ok)throw Error(body.error||'HTTP '+res.status);return body;}
const jsonOpts=value=>({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
function status(msg,error=false){const e=$('#zd-status');if(e){e.textContent=msg;e.classList.toggle('zd-error',error);}}
function openPanel(open){if(!dialog)return;dialog.hidden=!open;document.body.style.overflow=open?'hidden':'';if(open){showTab('processes');refresh();}else{clearInterval(timer);timer=null;}}
const rangeFields=(prefix,value=DEFAULT_RANGE())=>`<div class="zd-range-editor" data-prefix="${prefix}"><div class="zd-field zd-range-select"><label for="${prefix}-preset">Date Range</label><select id="${prefix}-preset" data-preset="${prefix}">${PRESETS.map(([p,label])=>`<option value="${p}" ${p===value.preset?'selected':''}>${label}</option>`).join('')}</select></div><div class="zd-custom-fields" id="${prefix}-custom" hidden><div class="zd-field"><label for="${prefix}-start">Start Date</label><input type="date" id="${prefix}-start" value="${esc(value.start_date||'')}"></div><div class="zd-field"><label for="${prefix}-end">End Date <span class="zd-hint">(blank = today)</span></label><input type="date" id="${prefix}-end" value="${esc(value.end_date==='today'?'':value.end_date||'')}"></div></div></div>`;
function shell(){
 if(dialog)return;
 dialog=document.createElement('div');dialog.id='z-desktop-shade';dialog.hidden=true;
 dialog.innerHTML=`<div id="z-desktop-panel" role="dialog" aria-modal="true" aria-labelledby="zd-heading">
 <div class="zd-top"><div><div id="zd-heading" class="zd-title">Auto Update</div><div class="zd-sub">Local data automation & scheduling</div></div><button class="zd-close" id="zd-close" aria-label="Close">×</button></div>
 <div class="zd-tabs" role="tablist" aria-label="Auto Update settings"><button class="zd-tab active" data-tab="processes" role="tab">Processes</button><button class="zd-tab" data-tab="scheduler" role="tab">Scheduler</button><button class="zd-tab" data-tab="activity" role="tab">Activity Log</button><span id="zd-run-status" class="zd-pill zd-tab-status">Idle</span></div>
 <div class="zd-body">
  <section class="zd-page" id="zd-tab-processes">
   <div class="zd-control-row"><label class="zd-toggle"><input id="zd-enabled" type="checkbox"> Enable Automation</label><label class="zd-toggle" title="On: Chrome runs in the background. Off: Chrome stays visible for troubleshooting."><input id="zd-headless" type="checkbox" checked> Headless Mode (On/Off)</label></div>
   <div class="zd-panel zd-credentials"><div class="zd-row"><div><div class="zd-section">SMRO Account</div><div class="zd-muted">Used for all three processes. Saved encrypted for this Windows account.</div></div><span id="zd-credential-state" class="zd-smallstatus">Not configured</span></div>
   <div class="zd-credential-fields"><div class="zd-field"><label for="zd-username">SMRO ID</label><input id="zd-username" type="text" autocomplete="off" spellcheck="false" placeholder="Enter SMRO ID"></div><div class="zd-field"><label for="zd-password">Password</label><div class="zd-password-wrap"><input id="zd-password" type="password" autocomplete="new-password" placeholder="Enter password"><button type="button" id="zd-password-toggle" class="zd-eye" aria-label="Show password">Show</button></div></div><button type="button" class="zd-btn secondary" id="zd-save-credentials">Save Account</button></div>
   </div>
   <div class="zd-row"><div class="zd-section">Processes</div><span class="zd-muted">Choose a separate date range for each process</span></div>
   <div id="zd-processes" class="zd-processes">${PROCESSES.map(p=>`<div class="zd-process" data-process="${p}"><div class="zd-process-top"><strong>${NAMES[p]}</strong><button class="zd-btn secondary" data-run="${p}">▶ Run</button></div><div class="zd-step-status" role="status" aria-live="polite"></div>${rangeFields('zd-'+p)}</div>`).join('')}</div>
   <div class="zd-run-all"><button class="zd-btn secondary" type="button" id="zd-check-local-data">Check Local SO Data</button> <button class="zd-btn" id="zd-run-all" data-run="all">▶ Run All Process</button> <button class="zd-btn zd-stop" id="zd-stop-all" type="button" disabled>■ Stop All</button></div><div id="zd-local-data-result" class="zd-local-data-result" hidden role="status"></div>
  </section>
  <section class="zd-page" id="zd-tab-scheduler" hidden>
   <div class="zd-panel"><div class="zd-row"><div><div class="zd-section">Schedule Settings</div><div class="zd-muted">Add multiple schedules without replacing previous schedules.</div></div></div>
   <label class="zd-toggle zd-task-toggle"><input id="zd-win-task" type="checkbox"> Continue scheduled updates when the dashboard is closed (Windows Task Scheduler)</label>
   <div class="zd-fields"><div class="zd-field"><label for="zd-process">Process</label><select id="zd-process">${Object.entries(NAMES).map(([p,n])=>`<option value="${p}">${n}</option>`).join('')}</select></div>
   <div class="zd-field"><label for="zd-mode">Schedule Type</label><select id="zd-mode"><option value="daily">Daily</option><option value="weekly">Custom Days</option><option value="interval">Every N Minutes</option></select></div>
   <div class="zd-field" id="zd-time-wrap"><label for="zd-time">Time</label><input id="zd-time" type="time" value="06:00"></div>
   <div class="zd-field" id="zd-interval-wrap" hidden><label for="zd-interval">Interval (minutes)</label><input id="zd-interval" type="number" min="30" max="1440" value="120"></div></div>
   <div class="zd-days" id="zd-days" hidden>${DN.map((d,i)=>`<label class="zd-day"><input type="checkbox" value="${i}" ${i<5?'checked':''}> ${d}</label>`).join('')}</div>
   <div class="zd-schedule-range" id="zd-schedule-range">${rangeFields('zd-schedule',{preset:'1month',start_date:dateISO(),end_date:'today'})}</div>
   <div class="zd-schedule-range-hint" id="zd-schedule-range-hint" hidden>Run All Process uses each process's Date Range from the Processes tab.</div>
   <div class="zd-add-schedule"><button id="zd-add" class="zd-btn secondary">+ Add Schedule</button></div>
   </div>
   <div class="zd-section">Saved Schedules</div><div id="zd-list" class="zd-list"></div>
   <div class="zd-warning">When Windows Task Scheduler is enabled, the PC must be awake and connected to the company system. Only enable it after testing the worker.</div>
  </section>
  <section class="zd-page" id="zd-tab-activity" hidden><div class="zd-row"><div><div class="zd-section">Activity Log</div><div class="zd-muted">Latest 40 entries</div></div><div class="zd-log-actions"><button class="zd-btn secondary" id="zd-copy-log">Copy</button><button class="zd-btn secondary" id="zd-clear-log">Clear Log</button><button class="zd-btn secondary" id="zd-refresh">Refresh</button></div></div><div class="zd-logs" id="zd-logs" role="log" aria-live="off">No runs yet.</div></section>
 </div>
 <div class="zd-foot"><span class="zd-status" id="zd-status">Ready.</span><div class="zd-foot-actions"><button class="zd-btn secondary" id="zd-cancel">Close</button><button class="zd-btn" id="zd-save">Save Settings</button></div></div>
 </div>`;
 document.body.appendChild(dialog);
 $('#zd-close').onclick=$('#zd-cancel').onclick=()=>openPanel(false);
 $('#zd-refresh').onclick=poll;$('#zd-copy-log').onclick=copyLog;$('#zd-clear-log').onclick=clearLog;$('#zd-stop-all').onclick=stopAll;$('#zd-save').onclick=save;$('#zd-save-credentials').onclick=async()=>{try{await saveCredentials();}catch(e){status('Account save failed: '+e.message,true);}};
 $('#zd-check-local-data').onclick=checkLocalData;
 $('#zd-add').onclick=addSchedule;$('#zd-mode').onchange=changeMode;$('#zd-process').onchange=updateScheduleRange;
 $('#zd-password-toggle').onclick=()=>{const input=$('#zd-password');const visible=input.type==='text';input.type=visible?'password':'text';$('#zd-password-toggle').textContent=visible?'Show':'Hide';};
 dialog.addEventListener('mousedown',e=>{if(e.target===dialog)openPanel(false);});
 dialog.addEventListener('click',event=>{
  const btn=event.target.closest('button');if(!btn)return;
  if(btn.dataset.tab)showTab(btn.dataset.tab);
  if(btn.dataset.run)run(btn.dataset.run);
  if(btn.dataset.remove!==undefined){schedules.splice(Number(btn.dataset.remove),1);renderList();status('Schedule removed. Click Save Settings to apply.');}
 });
 dialog.addEventListener('change',event=>{if(event.target.matches('select[data-preset]'))updateRangeVisibility(event.target.dataset.preset);});
 dialog.addEventListener('input',event=>{if(event.target.id==='zd-username'||event.target.id==='zd-password')$('#zd-credential-state').textContent='Unsaved changes';});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&dialog&&!dialog.hidden)openPanel(false);});
 PROCESSES.forEach(p=>updateRangeVisibility('zd-'+p));updateRangeVisibility('zd-schedule');changeMode();updateScheduleRange();renderList();
}
function showTab(target){tab=target;['processes','scheduler','activity'].forEach(p=>{$('#zd-tab-'+p).hidden=p!==target;dialog.querySelector('[data-tab="'+p+'"]').classList.toggle('active',p===target);});if(target==='activity')poll();}
function updateRangeVisibility(prefix){const selected=$('#'+prefix+'-preset');if(selected)$('#'+prefix+'-custom').hidden=selected.value!=='custom';}
function updateScheduleRange(){const all=$('#zd-process').value==='all';$('#zd-schedule-range').hidden=all;$('#zd-schedule-range-hint').hidden=!all;}
function changeMode(){let mode=$('#zd-mode').value;$('#zd-days').hidden=mode!=='weekly';$('#zd-interval-wrap').hidden=mode!=='interval';$('#zd-time-wrap').hidden=mode==='interval';}
function getRange(prefix){const preset=$('#'+prefix+'-preset').value;if(preset!=='custom')return {preset,start_date:'',end_date:'today'};
 const start=$('#'+prefix+'-start').value,end=$('#'+prefix+'-end').value||'today';if(!start)throw Error('Select Start Date for Custom Date.');if(end!=='today'&&end<start)throw Error('End Date cannot be before Start Date.');return {preset,start_date:start,end_date:end};}
function setRange(prefix,range){const value=range||DEFAULT_RANGE();$('#'+prefix+'-preset').value=value.preset||'1month';$('#'+prefix+'-start').value=value.start_date||'';$('#'+prefix+'-end').value=value.end_date==='today'?'':value.end_date||'';updateRangeVisibility(prefix);}
const description=s=>s.mode==='interval'?`Every ${s.interval_minutes} min`:s.mode==='weekly'?`${DN.filter((_,i)=>s.days.includes(i)).join(', ')} · ${s.time}`:`Daily · ${s.time}`;
function renderList(){const el=$('#zd-list');if(!el)return;if(!schedules.length){el.innerHTML='<div class="zd-empty">No schedules added yet.</div>';return;}
 el.innerHTML=schedules.map((s,i)=>`<div class="zd-schedule"><div class="zd-clock" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3.5 2"></path></svg></div><div class="zd-schedule-text"><strong>${esc(NAMES[s.process])}</strong><div class="zd-muted">${esc(description(s))} · ${s.process==='all'?'Uses Processes date ranges':esc(PRESETS.find(p=>p[0]===(s.range_preset||'custom'))?.[1]||'Custom Date')}${s.process!=='all'&&(s.range_preset||'custom')==='custom'?' · '+esc(s.start_date)+' → '+esc(s.end_date==='today'?'Today':s.end_date):''}</div></div><button data-remove="${i}" class="zd-btn secondary">Remove</button></div>`).join('');}
function addSchedule(){try{const mode=$('#zd-mode').value,range=$('#zd-process').value==='all'?DEFAULT_RANGE():getRange('zd-schedule');const days=[...$('#zd-days').querySelectorAll('input:checked')].map(e=>Number(e.value));if(mode==='weekly'&&!days.length)throw Error('Select at least one weekday.');const interval=Number($('#zd-interval').value);if(mode==='interval'&&(interval<30||interval>1440))throw Error('Interval must be 30–1440 minutes.');
 schedules.push({process:$('#zd-process').value,mode,time:$('#zd-time').value||'06:00',days,interval_minutes:interval,range_preset:range.preset,start_date:range.start_date||dateISO(),end_date:range.end_date,enabled:true});renderList();status('Schedule added. Click Save Settings to apply.');}catch(e){status(e.message,true);}}
async function loadCredentials(){const result=await api('/api/desktop/credentials');configured=!!result.configured;username=result.username||'';$('#zd-username').value=username;$('#zd-password').value='';$('#zd-password').placeholder=configured?'Saved securely · enter to change':'Enter password';$('#zd-credential-state').textContent=configured?'Account saved':'Not configured';}
async function saveCredentials(){const id=$('#zd-username').value.trim(),password=$('#zd-password').value;if(!id)throw Error('Enter SMRO ID.');if(!password&&configured&&id===username)return;
 const result=await api('/api/desktop/credentials',jsonOpts({username:id,password}));configured=!!result.configured;username=result.username;$('#zd-password').value='';$('#zd-password').placeholder='Saved securely · enter to change';$('#zd-credential-state').textContent='Account saved';status('SMRO account saved securely for this Windows user.');}
async function maybeSaveCredentials(){if($('#zd-password').value||$('#zd-username').value.trim()!==username)await saveCredentials();}
async function save(){try{await maybeSaveCredentials();ranges=Object.fromEntries(PROCESSES.map(p=>[p,getRange('zd-'+p)]));const payload={automation_enabled:$('#zd-enabled').checked,windows_scheduler_enabled:$('#zd-win-task').checked,headless:$('#zd-headless').checked,process_ranges:ranges,schedules};const data=await api('/api/desktop/settings',jsonOpts(payload));schedules=data.schedules||[];renderList();status('Settings saved locally.');}catch(e){status('Save failed: '+e.message,true);}}
async function run(process){
 try{
  await maybeSaveCredentials();
  if(!configured)throw Error('Save SMRO ID and password before running automation.');
  // Every Run All must use exactly the choices currently visible in Processes,
  // not an independent or stale Scheduler range.
  const edited=Object.fromEntries(PROCESSES.map(p=>[p,getRange('zd-'+p)]));
  // Run uses the current editor values, even before Save Settings.
  // Persisting settings is a separate explicit action.
  const date_range=process==='all'?null:edited[process];
  status('Starting '+NAMES[process]+'...');
  await api('/api/desktop/run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({process,date_range,process_ranges:process==='all'?edited:undefined})});
  status('Local processing started: '+NAMES[process]+'. Open Activity Log to follow progress.');
  await poll();
 }catch(e){status('Run failed: '+e.message,true);await poll();}
}
async function stopAll(){if(!window.confirm('Stop the active process and all queued processes? Unsaved work in the active automation may be interrupted.'))return;try{status('Stopping active worker and its child processes…');await api('/api/desktop/stop-all',{method:'POST'});await poll();}catch(e){status('Stop failed: '+e.message,true);}}
function updateLogs(lines){const entries=lines||[],value=entries.join('')||'No runs yet.';const log=$('#zd-logs');if(!log||value===logContent)return;log.replaceChildren();if(!entries.length){log.textContent=value;}else entries.forEach(raw=>{const row=document.createElement('div'),text=String(raw).trimEnd();row.className='zd-log-line';if(/Result: (Success|Completed)|Import complete/i.test(text)&&!/errors/i.test(text))row.classList.add('zd-log-success');else if(/Result: (Failed|Completed with errors)|Error:|Import failed/i.test(text))row.classList.add('zd-log-error');else if(/Result: Stopped/i.test(text))row.classList.add('zd-log-stopped');row.textContent=text;log.appendChild(row);});logContent=value;log.scrollTop=log.scrollHeight;}
function completedAt(value){if(!value)return '';const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleString('en-US',{month:'short',day:'numeric',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});}
async function copyLog(){const text=logContent||'No runs yet.';try{await navigator.clipboard.writeText(text);status('Activity Log copied.');}catch(_){const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();document.execCommand('copy');area.remove();status('Activity Log copied.');}}
async function clearLog(){if(!window.confirm('Clear saved Activity Log? This cannot be undone.'))return;try{await api('/api/desktop/logs/clear',{method:'POST'});logContent='';updateLogs([]);status('Activity Log cleared.');}catch(e){status('Clear log failed: '+e.message,true);}}
async function checkLocalData(){
 try{
  const d=await api('/api/desktop/import-diagnostics');
  const view=$('#zd-local-data-result');view.hidden=false;
  if(d.status!=='ready'){view.textContent='Database status: '+d.status+'. Check the local database file.';return;}
  view.textContent='SQLite local: '+d.so_data_rows.toLocaleString()+' SO rows · Last 2 months: '+d.so_last_2_months.toLocaleString()+' · Delivery Completed: '+d.completed_last_2_months.toLocaleString()+' · Latest SO date: '+(d.latest_so_date||'none')+' · Archived: '+d.history_rows.toLocaleString()+'. Summary Settlement counts Delivery Completed rows and uses active filters.';
 }catch(e){status('Database inspection failed: '+e.message,true);}
}
function updateRunButtons(state){
 const running=!!state.running;
 const stepStates=state.process_statuses||{};
 const progress=state.process_progress||{};
 const errors=state.process_errors||{};
 const completed=state.process_completed_at||{};
 const stop=$('#zd-stop-all');if(stop)stop.disabled=!running || !!state.stop_requested;
 const headless=$('#zd-headless');if(headless)headless.disabled=running;
 const all=!!state.batch_active;
 PROCESSES.forEach(id=>{
  const card=dialog.querySelector('.zd-process[data-process="'+id+'"]');
  if(!card)return;
  const step=stepStates[id]||'idle';
  card.classList.toggle('zd-process-active',step==='running');
  card.classList.toggle('zd-process-waiting',step==='queued');
  card.classList.toggle('zd-process-success',step==='completed');
  card.classList.toggle('zd-process-failed',step==='failed');
  const label=card.querySelector('.zd-step-status');
  if(label){
   label.className='zd-step-status zd-step-'+step;
   const finished=completedAt(completed[id]);
   label.textContent=(step==='running'||step==='queued')?(progress[id]||(step==='running'?'Running':'Waiting')):
     step==='completed'?'✓ Completed'+(finished?' · '+finished:''):step==='failed'?'✕ Failed · '+(errors[id]||state.last_error||'See Activity Log'):step==='cancelled'?'■ Stopped':finished?'Last completed · '+finished:'';
   label.title=step==='failed'?(errors[id]||state.last_error||'See Activity Log'):'';
  }
 });
 dialog.querySelectorAll('button[data-run]').forEach(btn=>{
  const id=btn.dataset.run;
  if(!btn.dataset.idleLabel)btn.dataset.idleLabel=btn.innerHTML;
  const active=running && (id==='all'?all:state.current_process_id===id);
  btn.disabled=running;
  btn.classList.toggle('zd-running',active);
  btn.innerHTML=active?'<span class="zd-spinner" aria-hidden="true"></span> '+(id==='all'?'Running All...':'Running...'):btn.dataset.idleLabel;
  btn.setAttribute('aria-busy',active?'true':'false');
 });
}
async function poll(){if(!dialog||dialog.hidden)return;try{const [state,logs]=await Promise.all([api('/api/desktop/status'),api('/api/desktop/logs')]);$('#zd-run-status').textContent=state.batch_active?'Run All: '+(state.current_process||'Preparing'):state.running?'Processing locally: '+state.status:state.status==='error'?'Last run failed: '+(state.last_error||'Open Activity Log'):state.status==='stopped'?'Stopped by user':'Local database';updateRunButtons(state);$('#zd-run-status').classList.toggle('zd-pill-error',state.status==='error');updateLogs(logs.lines);}catch(e){status('Status unavailable: '+e.message,true);}}
async function refresh(){try{const [settings]=await Promise.all([api('/api/desktop/settings'),loadCredentials()]);schedules=settings.schedules||[];ranges=settings.process_ranges||defaultRanges();$('#zd-enabled').checked=!!settings.automation_enabled;$('#zd-win-task').checked=!!settings.windows_scheduler_enabled;$('#zd-headless').checked=settings.headless!==false;PROCESSES.forEach(p=>setRange('zd-'+p,ranges[p]));renderList();status('Settings loaded.');await poll();}catch(e){status('Cannot load settings: '+e.message,true);}clearInterval(timer);timer=setInterval(poll,2500);}
function placeButton(){
 const manual=[...document.querySelectorAll('button')].find(btn=>/^\s*Manual Update\s*$/i.test(btn.textContent||''));
 if(!manual)return;
 const wrap=manual.parentElement, parent=wrap&&wrap.parentElement;
 if(!parent)return;
 let button=$('#z-auto-update-btn');
 if(!button){
   button=document.createElement('button');button.id='z-auto-update-btn';button.type='button';
   button.innerHTML='<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M20 7v-4h-4"/><path d="M20 7a8 8 0 0 0-13.65-2.65L4 7"/><path d="M4 17v4h4"/><path d="M4 17a8 8 0 0 0 13.65 2.65L20 17"/></svg><span>Auto Update</span>';
   button.onclick=e=>{e.preventDefault();e.stopPropagation();shell();openPanel(true);};
 }
 if(button.parentElement!==parent||button.nextElementSibling!==wrap)parent.insertBefore(button,wrap);
 button.className=manual.className+' zd-header-auto';
 // Respect the same intrinsic padding and computed height as Manual Update.
 const measured=manual.offsetHeight || Math.round(manual.getBoundingClientRect().height);
 if(measured>0){button.style.height=measured+'px';button.style.minHeight=measured+'px';}
}

function init(){placeButton();window.addEventListener('resize',placeButton,{passive:true});let queued=false;new MutationObserver(()=>{if(queued)return;queued=true;setTimeout(()=>{queued=false;placeButton();},250);}).observe(document.body,{subtree:true,childList:true});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
