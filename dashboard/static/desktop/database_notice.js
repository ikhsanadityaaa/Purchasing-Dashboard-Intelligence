/* Serveone Desktop: explain empty local DB instead of showing silent empty KPI. */
(function(){
'use strict';
async function inspect(){
  try {
    const res=await fetch('/api/desktop/database-status',{credentials:'same-origin',cache:'no-store'});
    if(!res.ok)return;
    const data=await res.json();
    if(data.status==='ready')return;
    const box=document.createElement('div');
    box.id='zd-database-notice';
    box.setAttribute('role','status');
    const info=document.createElement('span');
    info.textContent=data.status==='missing' || data.status==='no_so_data'
      ? 'Database SO lokal belum berisi data. Summary dan Settlement akan kosong sampai snapshot SQLite yang sah diimpor ke PC ini.'
      : 'Database lokal tidak dapat dibaca. Periksa file dan izin akses di folder data aplikasi.';
    const close=document.createElement('button');
    close.type='button'; close.setAttribute('aria-label','Dismiss database notice');
    close.textContent='×'; close.onclick=()=>box.remove();
    box.append(info,close); document.body.appendChild(box);
  } catch(e){ /* No extra network/error dialogs on startup. */ }
}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',inspect,{once:true});}
else inspect();
})();
