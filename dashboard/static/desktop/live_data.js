/* Serveone Desktop V0.1.7 — local data revision monitor. No external requests. */
(function(){
  'use strict';
  const ENDPOINT='/api/desktop/data-revision';
  let seen=null, busy=false;
  let latestReceived=0;
  async function check(){
    if(busy||document.visibilityState==='hidden')return;
    busy=true;
    try{
      const response=await fetch(ENDPOINT,{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
      if(!response.ok)return;
      const data=await response.json();
      const revision=typeof data.revision==='string'?data.revision:'';
      if(seen===null){seen=revision;return;}
      if(revision!==seen){
        seen=revision;
        latestReceived=Date.now();
        // React view refresh is in the V63 bundle's mounted App useEffect.
        window.dispatchEvent(new CustomEvent('serveone:data-changed',{detail:{revision}}));
        // SWR backend may return a stale response while aggregate caches rebuild.
        // A bounded follow-up refresh ensures currently visible charts converge.
        for(const delay of [2500,7000]){
          setTimeout(()=>{
            if(seen===revision && latestReceived && document.visibilityState!=='hidden'){
              window.dispatchEvent(new CustomEvent('serveone:data-changed',{detail:{revision,followup:true}}));
            }
          },delay);
        }
      }
    }catch(_){/* app may be shutting down: leave the last view intact */}
    finally{busy=false;}
  }
  function start(){
    check();
    setInterval(check,2500);
    document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')check()});
    window.addEventListener('focus',check);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
})();
