const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const bundle=fs.readFileSync(path.join(__dirname,'..','dashboard','static','assets','index-C9BxB1mr.js'),'utf8');
assert(bundle.includes('_kScale=_kt.scrollWidth>0?_tr.width/_kt.scrollWidth:1'),'floating table header must account for desktop CSS zoom');
assert(bundle.includes('translate3d(${-_sl*_kScale}px,0,0)'),'floating table header scroll offset must use visual pixels');
assert(bundle.includes('setProperty(`min-height`,`${_hh}px`,`important`)'),'floating table header must override inherited minimum height');
assert(bundle.includes('setProperty(`vertical-align`,`top`,`important`)'),'floating table header text must align to the top');
assert(bundle.includes('_df.style.setProperty(`align-items`,`flex-start`,`important`)'),'floating header inner content must align to the top');
assert(bundle.includes('a.getBoundingClientRect();let _needH=Math.max(_hh,32,..._dst.map(_cell=>_cell.scrollHeight||0))'),'floating header frame must preserve enough visual height for wrapped labels');
assert(bundle.includes('XG();tK();\n    Ya(Ma);U();'),'live data changes must refresh update timestamps on every page');
assert(bundle.includes('children:`V1.0.0`'),'header must show the public V1.0.0 version');
(async()=>{
 const calls=[],timers=[],events={};
 const status={import:{state:'idle'},vendor:{state:'idle'},credentials:{configured:true}};
 const control={};
 const panel={hidden:true,dataset:{},setAttribute(){},querySelector(q){return control[q]??(control[q]={disabled:false,textContent:'',onclick:null,addEventListener(){},click(){}});}};
 const document={readyState:'complete',body:{appendChild(){}},createElement(){return panel;},hidden:false,addEventListener(n,f){events[n]=f;}};
 const loc={pathname:'/'};
 const history={pushState(_a,_b,path){loc.pathname=path;},replaceState(_a,_b,path){loc.pathname=path;}};
 const window={location:loc,history,addEventListener(n,f){events[n]=f;},dispatchEvent() {}};
 const ctx={window,document,history,location:loc,XMLHttpRequest:function(){},
   queueMicrotask,URL,CustomEvent:function(){},setInterval:f=>{timers.push(f);},Date,console,
   fetch:async(path,opts={})=>{calls.push([path,opts.method||'GET',opts.body]);
     if(path.includes('/sheets/status'))return {ok:true,json:async()=>status};
     if(path.includes('/sheets/sync'))return {ok:true,json:async()=>({accepted:true})};
     return {ok:true,json:async()=>({})};},};
 ctx.window.fetch=ctx.fetch;
 ctx.XMLHttpRequest.prototype.open=function(){};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','dashboard','static','desktop','sheet_sync.js'),'utf8'),ctx);
 const flush=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));};
 const count=(page,mode)=>calls.filter(e=>e[0].endsWith('/sheets/sync')&&JSON.parse(e[2]).page===page&&JSON.parse(e[2]).mode===mode).length;
 assert.equal(count('import','once'),0);
 history.pushState(null,null,'/Import');await flush();assert.equal(count('import','once'),1);
 history.pushState(null,null,'/Settlement');await flush();
 history.pushState(null,null,'/Import');await flush();assert.equal(count('import','once'),1,'revisit Import must not sync');
 history.pushState(null,null,'/Vendor_Control');await flush();assert.equal(count('vendor','once'),1);
 history.pushState(null,null,'/Import');await flush();assert.equal(count('import','once'),1);
 control['[data-act="sync"]'].onclick();await flush();assert.equal(count('import','manual'),1);
 console.log('PASS: first visit once per page; return navigation does not sync; manual still works');
})();
