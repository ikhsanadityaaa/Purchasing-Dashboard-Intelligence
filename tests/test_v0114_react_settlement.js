/* Native React summary regression tests using a tiny hook harness.
 * No network, browser, company data, or node_modules required.
 */
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.join(__dirname,'..');
const text=fs.readFileSync(path.join(root,'dashboard/static/desktop/pending_settlement_react.source.js'),'utf8');
const bundle=fs.readFileSync(path.join(root,'dashboard/static/assets/index-C9BxB1mr.js'),'utf8');
const html=fs.readFileSync(path.join(root,'dashboard/static/index.html'),'utf8');
const sample={
  kpi:{total_pending:7,less_30:3,days_30_90:2,days_90_180:1,more_180:1,pending_sales_amount:920000000},
  monthly:[{month:'2026-10',so_count:7,amount:920000000}],
  pie:{sales_only:{count:2,amount:100},purch_only:{count:3,amount:200},both:{count:2,amount:300}},
  combo:[{op_unit:'UNIT-A',po_count:2,amount:10000000}],
  heatmap:{months:['2026-10'],rows:[{op_unit:'UNIT-A',total:7,cells:{'2026-10':7},amount_cells:{'2026-10':920000000}}]}
};
function harness(fetcher){
  let hook=0,states=[],effects=[],cleanups=[],events=new Map(),intervals=[];
  const window={__v63Export:null,
    addEventListener(name,cb){events.set(name,cb);},
    removeEventListener(name,cb){if(events.get(name)===cb)events.delete(name);}};
  const document={visibilityState:'visible',addEventListener(name,cb){events.set(name,cb);},
    removeEventListener(name,cb){if(events.get(name)===cb)events.delete(name);}};
  const y={Fragment:'fragment',createElement(type,props,...children){return {type,props:props||{},children};},
    useState(initial){let ix=hook++;if(!(ix in states))states[ix]=typeof initial==='function'?initial():initial;
      return [states[ix],val=>states[ix]=typeof val==='function'?val(states[ix]):val];},
    useRef(initial){let ix=hook++;if(!(ix in states))states[ix]={current:initial};return states[ix];},
    useEffect(fn){effects.push(fn);}};
  const ctx={y,window,document,location:{origin:'http://127.0.0.1'},
    localStorage:{getItem(){return null;},setItem(){}},
    sessionStorage:{getItem(){return null;},setItem(){}},
    fetch:fetcher,URL,AbortController,console,
    setInterval(fn){intervals.push(fn);return intervals.length;},clearInterval(){},
    _d:'chart-responsive',Hz:'chart-composed',oF:'chart-grid',TL:'chart-x',BL:'chart-y',LO:'chart-tooltip',_p:'chart-legend',iL:'chart-bar',nI:'chart-line'};
  vm.runInNewContext(text,ctx);
  return {render(props){hook=0;effects=[];return ctx.PSReactSummarySection(props);},
    start(){cleanups=effects.map(fn=>fn()).filter(x=>typeof x==='function');},
    stop(){cleanups.forEach(fn=>fn());cleanups=[];},
    events,states};
}
const walk=(tree,predicate,out=[])=>{
  if(tree&&typeof tree==='object'){
    if(predicate(tree))out.push(tree);
    if(Array.isArray(tree))tree.forEach(c=>walk(c,predicate,out));
    else if(tree.children)walk(tree.children,predicate,out);
  }
  return out;
};
const find=(tree,type,cls)=>walk(tree,n=>n.type===type&&(!cls||String(n.props.className||'').includes(cls)));
(async()=>{
  // Component is an integral React Summary child, not a DOM-scraping widget.
  assert(!bundle.includes('window.__v63PSW'));
  assert(!bundle.includes('function findAnchorContainer()'));
  assert(!bundle.includes('function onSummaryPage()'));
  assert(bundle.includes('(0,$.jsx)(PSReactSummarySection,{url:Za(`/api/data/pending-settlement`),detailUrl:Za(`/api/data/pending-settlement-detail`),onPending:ko,darkMode:n})'));
  assert.equal((bundle.match(/\(0,\$\.jsx\)\(PSReactSummarySection/g)||[]).length,1);
  assert(html.includes('/desktop/pending_settlement_react.css?v=14'));
  assert(html.includes('desktop-15'));
  assert(!text.includes('MutationObserver'));
  assert(!text.includes('insertBefore(section'));

  let responses=0;
  const h=harness(async(url)=>{responses++;return {ok:true,json:async()=>sample};});
  const props={url:'/api/data/pending-settlement?date_year=2026',detailUrl:'/api/data/pending-settlement-detail?date_year=2026',onPending:()=>{},darkMode:false};
  const initial=h.render(props);
  assert.equal(find(initial,'section','ps-react-summary').length,1);
  assert.equal(find(initial,'button','ps-react-kpi').length,5);
  assert.equal(find(initial,'div','ps-react-grid').length,1);
  assert.equal(find(initial,'table','ps-react-table').length,1);
  // Empty React template is rendered synchronously, prior to fetch or data.
  assert.equal(responses,0);
  h.start();
  for(let i=0;i<15;i++)await Promise.resolve();
  const filled=h.render(props);
  const kpi=find(filled,'button','ps-react-kpi');
  assert(kpi.some(n=>JSON.stringify(n).includes('7')),'KPI receives fetched data in existing template');
  assert.equal(responses,1);
  assert(find(filled,'td','ps-react-unit').length>0,'heatmap populated');
  h.stop();
  assert(!h.events.has('serveone:data-changed'),'live listener detached on unmount');

  let resolveA, resolveB;
  const requests=[];
  const g=harness(async(url)=>{
    requests.push(url);
    return new Promise(resolve=>{if(url.includes('old'))resolveA=resolve;else resolveB=resolve;});
  });
  const pa={...props,url:'/api/data/pending-settlement?scope=old'};
  const pb={...props,url:'/api/data/pending-settlement?scope=new'};
  g.render(pa);g.start();
  assert.equal(requests.length,1);
  g.stop();
  g.render(pb);g.start();
  assert.equal(requests.length,2);
  resolveB({ok:true,json:async()=>sample});
  for(let i=0;i<15;i++)await Promise.resolve();
  resolveA({ok:true,json:async()=>({kpi:{total_pending:999},heatmap:{rows:[]}})});
  for(let i=0;i<15;i++)await Promise.resolve();
  const after=g.render(pb);
  assert(find(after,'button','ps-react-kpi').some(n=>JSON.stringify(n).includes('7')));
  assert(!JSON.stringify(after).includes('999'));
  g.stop();
  console.log('PASS React Settlement: prebuilt skeleton, direct Summary ownership, filtered request scoping, stale response guard, cleanup, heatmap');
})();
