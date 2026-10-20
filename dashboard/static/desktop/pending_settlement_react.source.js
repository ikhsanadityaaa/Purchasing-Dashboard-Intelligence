/*
 * Serveone Dashboard Desktop V0.1.14: native React Pending Settlement.
 *
 * This editable component has ALREADY been inserted into the compiled V63
 * React bundle in this release. It is not a standalone browser script:
 * React/Recharts identifiers are bundle-local. When the upstream React source
 * is available, migrate this component into the actual Summary source tree.
 *
 * Unlike the V63.23 legacy widget, the component is owned by the Summary
 * React tree. It never scans headings, reparents DOM, or watches document.body.
 * The whole section and its empty-value template render before network data.
 */
function PSReactSummarySection({url,detailUrl,onPending,darkMode}) {
  const h = y.createElement;
  const [snapshot,setSnapshot] = y.useState({url:'',data:null,error:null,loading:true});
  const [mode,setMode] = y.useState(()=>{
    try{return localStorage.getItem('po-monitoring:ps-dist-mode')==='qty'?'qty':'amount';}
    catch(_){return 'amount';}
  });
  const [search,setSearch] = y.useState('');
  const [detail,setDetail] = y.useState(null);
  const [detailPage,setDetailPage] = y.useState(1);
  const latestDetail = y.useRef(0);
  const urlRef = y.useRef(url);
  urlRef.current = url;
  const fmt = v => Math.round(Number(v)||0).toLocaleString('en-US');
  const compact = v => {
    const n=Number(v)||0,a=Math.abs(n),sign=n<0?'-':'';
    if(a>=1e12)return sign+(a/1e12).toFixed(1)+'T';
    if(a>=1e9)return sign+(a/1e9).toFixed(1)+'B';
    if(a>=1e6)return sign+(a/1e6).toFixed(1)+'M';
    return sign+Math.round(a).toLocaleString('en-US');
  };
  const money = v=>'IDR '+compact(v);
  const monthLabel = m=>{
    const match=String(m||'').match(/^(\d{4})-(\d{2})$/);
    if(!match)return m==='Unknown'?'-':(m||'-');
    const names=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return (names[Number(match[2])-1]||match[2])+' '+match[1].slice(2);
  };
  const cacheKey = key=>'serveone:ps:react-v1:'+key;
  const loadCache = key=>{
    try{const stored=JSON.parse(sessionStorage.getItem(cacheKey(key))||'null');
      return stored && stored.data && Date.now()-stored.savedAt<15*60*1000 ? stored.data:null;
    }catch(_){return null;}
  };
  y.useEffect(()=>{
    // Recreate scope per URL. Every callback belongs to its original URL,
    // so a late response from Summary or an old filter cannot paint elsewhere.
    let alive=true,controller=null,busy=false,lastFetch=0;
    const cached=loadCache(url);
    setSnapshot({url,data:cached,error:null,loading:!cached});
    const request=async(force)=>{
      if(!alive||busy||document.visibilityState==='hidden')return;
      if(!force&&Date.now()-lastFetch<60000)return;
      busy=true;lastFetch=Date.now();
      controller=typeof AbortController==='function'?new AbortController():null;
      try{
        const response=await fetch(url,{credentials:'same-origin',cache:'no-store',
          headers:{'Accept':'application/json'},...(controller?{signal:controller.signal}:{})});
        if(!response.ok)throw new Error('HTTP '+response.status);
        const value=await response.json();
        if(!value||typeof value!=='object'||!value.kpi||!value.heatmap)throw new Error('Invalid summary data');
        if(!alive||urlRef.current!==url)return;
        try{sessionStorage.setItem(cacheKey(url),JSON.stringify({savedAt:Date.now(),data:value}));}catch(_){}
        setSnapshot({url,data:value,error:null,loading:false});
      }catch(error){
        if(!alive||urlRef.current!==url||error.name==='AbortError')return;
        setSnapshot(previous=>({url,data:previous.url===url?previous.data:cached,
          error:error.message||'Network error',loading:false}));
        lastFetch=0; // retry on next scheduled refresh
      }finally{busy=false;controller=null;}
    };
    // fetch on current route only; component does not exist on other pages.
    request(true);
    const live=()=>{lastFetch=0;request(true);};
    const resume=()=>{if(document.visibilityState==='visible')request(true);};
    window.addEventListener('serveone:data-changed',live);
    document.addEventListener('visibilitychange',resume);
    const timer=setInterval(()=>request(false),60000);
    return()=>{alive=false;if(controller)controller.abort();clearInterval(timer);
      window.removeEventListener('serveone:data-changed',live);
      document.removeEventListener('visibilitychange',resume);};
  },[url]);
  y.useEffect(()=>{try{localStorage.setItem('po-monitoring:ps-dist-mode',mode);}catch(_){}},[mode]);
  y.useEffect(()=>{ // cancel in-flight popup requests when leaving Summary/filter changes
    latestDetail.current++;
    setDetail(null);
  },[detailUrl]);
  const openDetail=async(opUnit,month)=>{
    const id=++latestDetail.current;
    setDetailPage(1);
    setDetail({opUnit,month,loading:true,rows:[],error:null});
    try{
      const link=new URL(detailUrl,location.origin);
      if(opUnit)link.searchParams.set('op_unit',opUnit);
      if(month)link.searchParams.set('month',month);
      const response=await fetch(link.pathname+link.search,{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const payload=await response.json();
      if(id!==latestDetail.current)return;
      setDetail({opUnit,month,loading:false,rows:Array.isArray(payload.rows)?payload.rows:[],error:null});
    }catch(error){if(id===latestDetail.current)setDetail({opUnit,month,loading:false,rows:[],error:error.message||'Network error'});}
  };
  const closeDetail=()=>{latestDetail.current++;setDetail(null);};
  const saveDetail=()=>{
    if(!detail||!detail.rows.length)return;
    const heads=['SO Number','SO Item','SO Status','PO No.','Op Unit','Vendor','Product','Qty','Sales Amount','PO Amount (IDR)','Settlement','Purch. Check','Sales Sett.','Delivery Complete','Create Date'];
    const keys=['so_number','so_item','so_status','po_number','operation_unit_name','vendor_name','product_name','so_qty','sales_amount','purchase_amount_idr','settlement_status','purchasing_settlement_check','sales_settlement_status','delivery_complete_date','so_create_date'];
    const name='Pending_Settlement'+(detail.opUnit?'_'+detail.opUnit:'')+(detail.month?'_'+detail.month:'');
    const payload={columns:heads.map((header,i)=>({header,value:r=>r[keys[i]]})),rows:detail.rows,filename:name,sheetName:'Detail'};
    if(typeof window.__v63Export==='function')try{window.__v63Export(payload);return;}catch(_){}
    const quote=v=>{let s=String(v==null?'':v);if(/^[=+@\-]/.test(s))s="'"+s;return /[",\n;]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
    const csv=heads.map(quote).join(',')+'\r\n'+detail.rows.map(r=>keys.map(k=>quote(r[k])).join(',')).join('\r\n');
    const blob=new Blob(['\ufeff',csv],{type:'text/csv;charset=utf-8'});
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name+'.csv';
    document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},200);
  };
  // Never show previous scope's data after filters change, even for one frame.
  const data=snapshot.url===url?snapshot.data:null;
  const loading=snapshot.url!==url||snapshot.loading;
  const error=snapshot.url===url?snapshot.error:null;
  const kpi=data?.kpi||{};
  const aging=[
    {key:'total_pending',label:'Total Pending',color:'#2563eb',bucket:''},
    {key:'less_30',label:'0-30 Days',color:'#14b8a6',bucket:'0-30'},
    {key:'days_30_90',label:'30-90 Days',color:'#2563eb',bucket:'30-90'},
    {key:'days_90_180',label:'90-180 Days',color:'#f97316',bucket:'90-180'},
    {key:'more_180',label:'180+ Days',color:'#dc2626',bucket:'180+'},
  ];
  const pieDef=[{key:'sales_only',label:'Sales not settled',color:'#2563eb'},
    {key:'purch_only',label:'Purchasing not settled',color:'#f59e0b'},
    {key:'both',label:'Both not settled',color:'#dc2626'}];
  const pie=data?.pie||{};
  const pieTotal=pieDef.reduce((n,p)=>n+(Number(pie[p.key]?.count)||0),0);
  const pieRadius=54,pieCirc=2*Math.PI*pieRadius;
  let pieOffset=pieCirc*.25;
  const pieSegments=pieDef.map(p=>{
    const count=Number(pie[p.key]?.count)||0;
    const length=pieTotal?pieCirc*count/pieTotal:0;
    const result={...p,count,length,offset:pieOffset};
    pieOffset-=length;
    return result;
  });
  const months=(data?.heatmap?.months||[]).slice();
  const heatRows=(data?.heatmap?.rows||[]).slice();
  const availableAmount=heatRows.some(r=>Object.values(r.amount_cells||{}).some(v=>Number(v)!==0));
  const isAmount=mode==='amount'&&availableAmount;
  const val=(r,m)=>Number((isAmount?r.amount_cells:r.cells)?.[m]||0);
  const totalFor=r=>isAmount?Object.values(r.amount_cells||{}).reduce((s,v)=>s+Number(v||0),0):Number(r.total)||0;
  heatRows.sort((a,b)=>totalFor(b)-totalFor(a));
  const grandTotal=heatRows.reduce((s,r)=>s+totalFor(r),0);
  const visibleRows=heatRows.filter(r=>String(r.op_unit||'').toLowerCase().includes(search.trim().toLowerCase()));
  const columnTotal=m=>heatRows.reduce((s,r)=>s+val(r,m),0);
  const heatStyle=v=>{
    const t=Math.max(.08,Math.min(1,Math.pow(Math.log1p(Math.abs(v))/Math.log1p(isAmount?2e9:50),.9)));
    const L=darkMode?24+t*30:97-t*46;
    return {background:'hsl(217,88%,'+L.toFixed(1)+'%)',color:t>.55?'#fff':darkMode?'#e2e8f0':'#334155',fontWeight:700};
  };
  const cards=(data?.combo||[]).slice(0,10);
  const maxAmount=Math.max(1,...cards.map(r=>Number(r.amount)||0));
  const monthly=(data?.monthly||[]).slice(-14).map(r=>({...r,monthLabel:monthLabel(r.month)}));
  const panel=(title,content,className='')=>h('div',{className:'ps-react-panel '+className},
    h('h4',{className:'ps-react-panel-title'},title),content);
  const placeholder=text=>h('div',{className:'ps-react-placeholder',role:'status', 'aria-label':text},
    loading?h('div',{className:'ps-react-skeleton-line'}):text);
  const pieElement=h('div',{className:'ps-react-pie-layout'},
    h('svg',{width:150,height:150,viewBox:'0 0 150 150',role:'img','aria-label':'Settlement status breakdown'},
      pieTotal?pieSegments.filter(p=>p.count>0).map(p=>h('circle',{key:p.key,cx:75,cy:75,r:pieRadius,fill:'none',
        stroke:p.color,strokeWidth:26,strokeDasharray:p.length+' '+(pieCirc-p.length),strokeDashoffset:p.offset,
        title:p.label+': '+fmt(p.count)})):
        h('circle',{cx:75,cy:75,r:pieRadius,fill:'none',stroke:'var(--ps-empty)',strokeWidth:26})),
    h('div',{className:'ps-react-pie-legend'},pieDef.map(p=>{
      const count=Number(pie[p.key]?.count)||0,pct=pieTotal?100*count/pieTotal:0;
      return h('div',{className:'ps-react-legend-row',key:p.key},
        h('span',{className:'ps-react-legend-name',title:p.label+' · '+money(pie[p.key]?.amount)},
          h('span',{className:'ps-react-swatch',style:{background:p.color}}),p.label),
        h('span',{className:'ps-react-legend-numbers'},fmt(count),' | ',pct.toFixed(1)+'%'));
    })));
  const monthlyChart=h('div',{className:'ps-react-month-chart'},
    data&&monthly.length?
      h(_d,{width:'100%',height:265},h(Hz,{data:monthly,margin:{top:8,right:10,bottom:0,left:0}},
        h(oF,{strokeDasharray:'3 3',vertical:false,stroke:darkMode?'#374151':'#e5e7eb'}),
        h(TL,{dataKey:'monthLabel',fontSize:9,stroke:darkMode?'#9ca3af':'#64748b'}),
        h(BL,{yAxisId:'left',fontSize:9,stroke:'#2563eb'}),
        h(BL,{yAxisId:'right',orientation:'right',fontSize:9,stroke:'#14b8a6',tickFormatter:compact}),
        h(LO,{formatter:(v,name)=>[name==='Pending Settlement'?fmt(v):money(v),name],
          contentStyle:{background:darkMode?'#1f2937':'#fff',color:darkMode?'#fff':'#111827',borderRadius:8,fontSize:12}}),
        h(_p,{wrapperStyle:{fontSize:11}}),
        h(iL,{yAxisId:'left',dataKey:'so_count',name:'Pending Settlement',fill:'#2563eb',radius:[4,4,0,0],isAnimationActive:false}),
        h(nI,{yAxisId:'right',type:'monotone',dataKey:'amount',name:'Total Sales Amount',stroke:'#14b8a6',strokeWidth:2,dot:{r:2},isAnimationActive:false})))
      :placeholder('No monthly pending settlement data'));
  const top10=h('div',{className:'ps-react-top10'},
    cards.length?cards.map((r,i)=>h('div',{key:r.op_unit||i,className:'ps-react-top-row'},
      h('span',{className:'ps-react-top-rank'},String(i+1)),
      h('div',{className:'ps-react-top-middle'},
        h('div',{className:'ps-react-top-name',title:r.op_unit},r.op_unit||'-'),
        h('div',{className:'ps-react-progress-track'},h('div',{className:'ps-react-progress-fill',style:{width:Math.max(3,100*(Number(r.amount)||0)/maxAmount)+'%'}}))),
      h('div',{className:'ps-react-top-value'},h('strong',null,money(r.amount)),h('span',null,fmt(r.po_count)+' PO')))):
      placeholder('No pending settlement PO data'));
  const gridData=h('div',{className:'ps-react-grid'},
    panel('Monthly Pending Settlements',h(y.Fragment,null,
      h('p',{className:'ps-react-panel-sub'},'Total: ',loading?'—':fmt(kpi.total_pending),' pending | ',loading?'—':money(kpi.pending_sales_amount)),monthlyChart),'ps-react-wide'),
    panel('Settlement Not Complete',pieElement),
    panel('Top 10 PO Pending Settlement by Op Unit',h(y.Fragment,null,
      h('p',{className:'ps-react-panel-sub'},'Ranked by pending PO amount (IDR)'),top10)));
  const cellClick=(r,m)=>openDetail(r||null,m||null);
  const dataCells=()=>{
    if(!data)return h('tr',null,h('td',{colSpan:months.length+3,className:'ps-react-table-empty'},
      loading?'Loading settlement data…':(error?'Unable to load data':'No pending settlement')));
    if(!visibleRows.length)return h('tr',null,h('td',{colSpan:months.length+3,className:'ps-react-table-empty'},
      search?'No matching op units':'No pending settlement'));
    return visibleRows.map((r,i)=>{
      const rowTotal=totalFor(r),percent=grandTotal?100*rowTotal/grandTotal:0;
      return h('tr',{key:r.op_unit||i},
        h('td',{className:'ps-react-sticky-left ps-react-unit',title:r.op_unit,onClick:()=>cellClick(r.op_unit,null)},r.op_unit||'-'),
        ...months.map(m=>{
          const n=val(r,m);return h('td',{key:m,className:'ps-react-cell'+(n?' ps-react-click':' '),
            style:n?heatStyle(n):null,
            onClick:n?()=>cellClick(r.op_unit,m):undefined,
            title:n?`${r.op_unit} · ${monthLabel(m)} · ${isAmount?money(n):fmt(n)} · click for detail`:''},
            n?(isAmount?compact(n):fmt(n)):'');
        }),
        h('td',{className:'ps-react-percent'},percent.toFixed(1)+'%'),
        h('td',{className:'ps-react-total ps-react-click',onClick:()=>cellClick(r.op_unit,null),title:'Open op unit detail'},
          isAmount?money(rowTotal):fmt(rowTotal)));
    });
  };
  const heatmap=panel('Pending Settlement Distribution',h(y.Fragment,null,
    h('div',{className:'ps-react-controls'},
      h('select',{value:mode,onChange:e=>setMode(e.target.value),'aria-label':'Pending Settlement Distribution metric',title:'Choose Qty or Sales Amount'},
        h('option',{value:'amount'},'Sales Amount'),h('option',{value:'qty'},'Qty')),
      h('input',{value:search,onChange:e=>setSearch(e.target.value),placeholder:'Search op unit…','aria-label':'Search op unit'}),
      search?h('button',{type:'button',onClick:()=>setSearch(''),'aria-label':'Clear op unit search'},'×'):null),
    h('p',{className:'ps-react-hint'},search?`Found ${visibleRows.length} of ${heatRows.length} op units`:
      `Showing ${Math.min(15,heatRows.length)} of ${heatRows.length} op units (scroll or search for more)`),
    h('div',{className:'ps-react-table-scroll'},h('table',{className:'ps-react-table'},
      h('thead',null,h('tr',null,
        h('th',{className:'ps-react-sticky-left'},'Op Unit'),
        ...months.map(m=>h('th',{key:m},monthLabel(m))),h('th',null,'%'),h('th',null,'Total'))),
      h('tbody',null,dataCells(),
        data&&heatRows.length?h('tr',{className:'ps-react-grand-row'},
          h('td',{className:'ps-react-sticky-left'},'TOTAL'),
          ...months.map(m=>h('td',{key:m,className:'ps-react-click',onClick:()=>cellClick(null,m)},
            columnTotal(m)?(isAmount?compact(columnTotal(m)):fmt(columnTotal(m))):'')),
          h('td',null,grandTotal?'100%':'0%'),
          h('td',{className:'ps-react-total ps-react-click',onClick:()=>cellClick(null,null)},isAmount?money(grandTotal):fmt(grandTotal))):null)))),'ps-react-full');
  const columns=[['SO Number','so_number'],['SO Item','so_item'],['SO Status','so_status'],['PO No.','po_number'],['Op Unit','operation_unit_name'],['Vendor','vendor_name'],['Product','product_name'],['Qty','so_qty'],['Sales Amount','sales_amount'],['PO Amount (IDR)','purchase_amount_idr'],['Settlement','settlement_status'],['Purch. Check','purchasing_settlement_check'],['Sales Sett.','sales_settlement_status'],['Delivery Complete','delivery_complete_date'],['Create Date','so_create_date']];
  const detailRows=detail?.rows||[];
  const detailPages=Math.max(1,Math.ceil(detailRows.length/50));
  const detailModal=detail?h('div',{className:'ps-react-modal-backdrop',role:'presentation',onClick:closeDetail},
    h('section',{className:'ps-react-modal',role:'dialog','aria-modal':'true','aria-label':'Pending Settlement Detail',onClick:e=>e.stopPropagation()},
      h('header',{className:'ps-react-modal-header'},
        h('div',null,h('h3',null,'Pending Settlement',detail.opUnit?' · '+detail.opUnit:'',detail.month?' · '+monthLabel(detail.month):''),
          h('span',null,fmt(detailRows.length)+' records')),
        h('div',null,h('button',{type:'button',onClick:saveDetail,disabled:detail.loading||!detailRows.length},'Download Excel'),
          h('button',{type:'button',onClick:closeDetail,'aria-label':'Close detail'},'×'))),
      detail.loading?placeholder('Loading detail…'):detail.error?
        h('p',{className:'ps-react-error'},'Unable to load details: ',detail.error):
        h('div',{className:'ps-react-modal-scroll'},h('table',{className:'ps-react-detail-table'},
          h('thead',null,h('tr',null,columns.map(([title,key])=>h('th',{key},title)))),
          h('tbody',null,detailRows.slice((detailPage-1)*50,detailPage*50).map((r,i)=>h('tr',{key:(r.so_number||i)+':'+(r.so_item||i)},
            columns.map(([,key])=>h('td',{key},r[key]??'-'))))))),
      h('footer',{className:'ps-react-modal-footer'},
        h('button',{type:'button',disabled:detailPage<=1,onClick:()=>setDetailPage(p=>Math.max(1,p-1))},'‹'),
        h('span',null,detailPage,' / ',detailPages),
        h('button',{type:'button',disabled:detailPage>=detailPages,onClick:()=>setDetailPage(p=>Math.min(detailPages,p+1))},'›')))):null;
  return h('section',{className:'ps-react-summary'+(darkMode?' ps-react-dark':''),'data-ps-react':'true'},
    h('h3',{className:'ps-react-heading'},h('span',{className:'ps-react-heading-dot'}),'Pending Settlement'),
    h('div',{className:'ps-react-kpis'},aging.map(a=>h('button',{key:a.key,type:'button',className:'ps-react-kpi',
      title:'Open Pending Delivery'+(a.bucket?' with Aging '+a.bucket:''),onClick:()=>onPending(a.bucket||null)},
      h('div',{className:'ps-react-kpi-top'},a.label,h('span',{style:{background:a.color}})),
      h('strong',null,data?fmt(kpi[a.key]):'—')))),
    error?h('p',{className:'ps-react-error',role:'alert'},'Pending Settlement data could not be refreshed: ',error,
      data?' · Showing saved data.':' · The empty template remains available.'):null,
    gridData,heatmap,detailModal);
}
