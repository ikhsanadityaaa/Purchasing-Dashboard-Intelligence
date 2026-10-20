/* Local desktop only: fit dashboard to laptop viewport, with keyboard and Ctrl+wheel zoom, no on-screen controls. */
(function(){
'use strict';
const STORAGE='serveone_desktop_zoom_pct_v1', MIN=65, MAX=125, STEP=5;
const getRoot=()=>document.getElementById('root');
const clamp=n=>Math.min(MAX,Math.max(MIN,Math.round(n/STEP)*STEP));
function autoFit(){
 const w=window.innerWidth||1366,h=window.innerHeight||768;
 return clamp(Math.min(100,Math.min(w/1510,h/875)*100));
}
let stored;
try{stored=Number(localStorage.getItem(STORAGE));}catch(_){}
let pct=stored>=MIN&&stored<=MAX?clamp(stored):autoFit();
let manuallySet=stored>=MIN&&stored<=MAX;
function apply(shouldSave){
 const root=getRoot();if(root){
  root.style.zoom=String(pct/100);
  // fixed height keeps the scaled layout from getting clipped on small displays
  root.style.minHeight=`${Math.ceil(100/(pct/100))}vh`;
 }
 if(shouldSave){manuallySet=true;try{localStorage.setItem(STORAGE,String(pct));}catch(_){}}
}
function setZoom(value,save=true){pct=clamp(value);apply(save);}
document.addEventListener('keydown',e=>{
 if(!e.ctrlKey||e.altKey||e.metaKey||!['+','=','-','0'].includes(e.key))return;
 if(e.target.closest('input,textarea,select,[contenteditable]'))return;
 e.preventDefault();
 if(e.key==='0'){try{localStorage.removeItem(STORAGE);}catch(_){}manuallySet=false;setZoom(autoFit(),false);}
 else setZoom(pct+(e.key==='-'?-STEP:STEP));
});
window.addEventListener('wheel',e=>{
 if(!e.ctrlKey||e.altKey||e.metaKey)return;
 e.preventDefault();
 // Same direction as Chromium browser zoom: wheel up = larger.
 setZoom(pct+(e.deltaY<0?STEP:-STEP));
},{passive:false,capture:true});
window.addEventListener('resize',()=>{if(!manuallySet)setZoom(autoFit(),false)},{passive:true});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>apply(false),{once:true});else apply(false);
})();
