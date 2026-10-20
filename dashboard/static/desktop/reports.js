(function(){
  'use strict';

  const ICON='<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2h9l4 4v16H6z"/><path d="M14 2v5h5M9 13h6M9 17h6M9 9h2"/></svg>';
  const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const num=value=>Number(value)||0;
  const count=value=>new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(num(value));
  const money=value=>{
    const n=num(value),a=Math.abs(n);
    for(const [size,label] of [[1e12,'T'],[1e9,'B'],[1e6,'M'],[1e3,'K']])if(a>=size)return 'IDR '+(n/size).toFixed(a>=size*100?0:1)+label;
    return 'IDR '+new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(n);
  };
  const pct=value=>num(value).toFixed(1)+'%';
  const iso=date=>date.toISOString().slice(0,10);
  const dateLabel=value=>{
    const d=new Date(String(value)+'T00:00:00');
    return Number.isNaN(d.getTime())?String(value||''):d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});
  };

  function rangeFor(preset){
    const to=new Date();to.setHours(12,0,0,0);
    const from=new Date(to);
    if(preset==='week')from.setDate(from.getDate()-6);
    else if(preset==='month')from.setDate(from.getDate()-29);
    else from.setFullYear(from.getFullYear()-1);
    return {from:iso(from),to:iso(to)};
  }

  function ensureDialog(){
    let dialog=document.getElementById('z-report-dialog');
    if(dialog)return dialog;
    dialog=document.createElement('dialog');
    dialog.id='z-report-dialog';
    dialog.innerHTML=`<form method="dialog" id="z-report-form">
      <div class="z-report-head"><div><h2>Create Report</h2><p>Select report content and reporting period.</p></div><button class="z-report-close" value="cancel" aria-label="Close">&times;</button></div>
      <div class="z-report-body">
        <div id="z-report-error" class="z-report-error" role="alert"></div>
        <div class="z-report-field"><label for="z-report-type">Report</label><select id="z-report-type" class="z-report-select"><option value="summary">Summary Report</option><option value="cost">Cost Reduction &amp; PIC KPI</option></select></div>
        <div class="z-report-field"><label for="z-report-range">Date Range</label><select id="z-report-range" class="z-report-select"><option value="week">Last 7 Days</option><option value="month">Last 30 Days</option><option value="year" selected>Last 12 Months</option><option value="custom">Custom Range</option></select><p class="z-report-hint">Only records inside this range appear in report. Cost Reduction compares with same dates one year earlier.</p></div>
        <div class="z-report-dates"><div class="z-report-field"><label for="z-report-from">From</label><input id="z-report-from" class="z-report-date" type="date" required></div><div class="z-report-field"><label for="z-report-to">To</label><input id="z-report-to" class="z-report-date" type="date" required></div></div>
      </div>
      <div class="z-report-actions"><button class="z-report-cancel" value="cancel">Cancel</button><button id="z-report-generate" class="z-report-generate" value="default">Create Printable Report</button></div>
    </form>`;
    document.body.appendChild(dialog);
    const preset=dialog.querySelector('#z-report-range');
    const from=dialog.querySelector('#z-report-from');
    const to=dialog.querySelector('#z-report-to');
    function setPreset(){
      const custom=preset.value==='custom';
      from.disabled=!custom;to.disabled=!custom;
      if(!custom){const range=rangeFor(preset.value);from.value=range.from;to.value=range.to}
    }
    preset.addEventListener('change',setPreset);setPreset();
    dialog.querySelector('#z-report-form').addEventListener('submit',event=>{
      if(event.submitter&&event.submitter.value==='cancel')return;
      event.preventDefault();createReport(dialog);
    });
    return dialog;
  }

  function openDialog(type){
    const dialog=ensureDialog();
    dialog.querySelector('#z-report-type').value=type;
    dialog.querySelector('#z-report-error').classList.remove('show');
    dialog.showModal();
  }

  function addButton(){
    const path=location.pathname.replace(/\/$/,'')||'/';
    const type=path==='/Cost_Reduction'?'cost':path==='/'?'summary':null;
    if(!type)return;
    const expected=type==='summary'?'Summary':'Cost Reduction';
    const heading=[...document.querySelectorAll('h1')].find(el=>el.textContent.trim()===expected);
    if(!heading)return;
    const header=heading.closest('header')||heading.parentElement;
    if(!header)return;
    let button=header.querySelector('.z-report-button');
    if(!button){
      button=document.createElement('button');
      button.type='button';button.className='z-report-button';button.innerHTML=ICON;
      button.setAttribute('aria-label','Create report');button.title='Create report';
      button.addEventListener('click',()=>openDialog(type));
    }
    const autoUpdate=header.querySelector('#z-auto-update-btn');
    const summaryActions=autoUpdate?.parentElement;
    const genericActions=[...header.querySelectorAll('div')].find(el=>el.children.length&&[...el.children].some(child=>child.tagName==='BUTTON'));
    const target=summaryActions||genericActions||header;
    if(autoUpdate){
      if(button.parentElement!==target||button.nextElementSibling!==autoUpdate)target.insertBefore(button,autoUpdate);
    }else if(button.parentElement!==target||button!==target.firstElementChild)target.insertBefore(button,target.firstChild);
  }

  async function getJSON(path,query){
    const url=new URL(path,location.origin);
    Object.entries(query||{}).forEach(([key,value])=>{if(value)url.searchParams.set(key,value)});
    const headers={'Accept':'application/json'};
    if(window.__serveoneDesktopUploadToken)headers['X-Serveone-Desktop-Upload']=window.__serveoneDesktopUploadToken;
    const response=await fetch(url,{credentials:'same-origin',cache:'no-store',headers});
    let data={};try{data=await response.json()}catch(_){ }
    if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
    return data;
  }

  function page(title,subtitle,body){return `<section class="report-page"><header class="report-header"><div><div class="eyebrow">PURCHASING DASHBOARD REPORT</div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div></header>${body}<footer>Internal dashboard report <span>${esc(subtitle)}</span></footer></section>`}
  function coverPage(title,period){return `<section class="report-page cover-page"><div class="cover-top"><span>Purchasing Dashboard</span></div><div class="cover-main"><div class="cover-mark"></div><p>ANALYSIS REPORT</p><h1>${esc(title)}</h1><div class="cover-period"><span>Reporting Period</span><strong>${esc(period)}</strong></div></div><footer>Generated from Serveone Dashboard <span>${esc(period)}</span></footer></section>`}
  function kpis(items){return `<div class="kpi-row">${items.map(x=>`<div class="kpi"><span>${esc(x[0])}</span><strong class="${x[2]||''}">${esc(x[1])}</strong>${x[3]?`<small>${esc(x[3])}</small>`:''}</div>`).join('')}</div>`}
  function table(headers,rows,empty){return rows.length?`<table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map((cell,index)=>`<td${index===0?' class="label"':''}>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table>`:`<div class="empty">${esc(empty||'No data for selected period.')}</div>`}
  function bars(rows,labelKey,valueKey,formatter){
    const max=Math.max(...rows.map(x=>num(x[valueKey])),1);
    return `<div class="bars">${rows.slice(0,10).map(x=>`<div class="bar-row"><div class="bar-label">${esc(x[labelKey]||'Unknown')}</div><div class="bar-track"><i style="width:${Math.max(1,num(x[valueKey])/max*100)}%"></i></div><b>${esc(formatter(x[valueKey]))}</b></div>`).join('')||'<div class="empty">No ranking data for selected period.</div>'}</div>`;
  }
  function visualBars(rows,labelOf,valueOf,formatter,limit=10,colorOf){
    const data=(rows||[]).slice(0,limit),max=Math.max(...data.map(valueOf).map(num),1);
    return `<div class="visual-bars">${data.map(row=>{const value=num(valueOf(row));return `<div class="visual-row"><div class="visual-label" title="${esc(labelOf(row))}">${esc(labelOf(row)||'Unknown')}</div><div class="visual-track"><i class="${colorOf?colorOf(row):''}" style="width:${Math.max(value?2:0,Math.abs(value)/max*100)}%"></i></div><b class="${value<0?'bad':''}">${esc(formatter(value))}</b></div>`}).join('')||'<div class="empty">No data for selected period.</div>'}</div>`;
  }
  function segments(items){
    const total=items.reduce((sum,item)=>sum+Math.max(0,num(item.value)),0);
    if(!total)return '<div class="empty">No distribution data for selected period.</div>';
    return `<div class="segments"><div class="segment-track">${items.map(item=>`<i style="width:${num(item.value)/total*100}%;background:${item.color}" title="${esc(item.label)}: ${count(item.value)}"></i>`).join('')}</div><div class="segment-legend">${items.map(item=>`<div><i style="background:${item.color}"></i><span>${esc(item.label)}</span><b>${count(item.value)} · ${pct(num(item.value)/total*100)}</b></div>`).join('')}</div></div>`;
  }
  function dualTrend(rows){
    const data=(rows||[]).slice(-12),max=Math.max(...data.flatMap(row=>[num(row.purchase_amount),num(row.sales_amount)]),1);
    return `<div class="dual-trend">${data.map(row=>`<div class="dual-row"><span>${esc(row.month)}</span><div><i class="purchase" style="width:${num(row.purchase_amount)/max*100}%"></i><i class="sales" style="width:${num(row.sales_amount)/max*100}%"></i></div><b>${money(num(row.sales_amount)-num(row.purchase_amount))}</b></div>`).join('')||'<div class="empty">No monthly data for selected period.</div>'}<div class="chart-key"><i class="purchase"></i>Purchase <i class="sales"></i>Sales <b>Right: gross margin</b></div></div>`;
  }
  function progressVisual(rows){
    return `<div class="progress-list">${(rows||[]).map(row=>{const progress=num(row.target_progress_pct),impact=num(row.ytd_price_impact);return `<div class="progress-item"><div class="progress-head"><b>${esc(row.pic_name)}</b><span class="${impact>=0?'good':'bad'}">${money(impact)} / ${money(row.target_saving)}</span></div><div class="progress-track"><i class="${impact<0?'negative':progress>=100?'achieved':''}" style="width:${Math.min(100,Math.max(impact<0?3:0,progress))}%"></i></div><div class="progress-meta"><span>${pct(row.cost_reduction_pct)} cost reduction</span><span>${pct(progress)} target progress</span><span>${count(row.comparable_items)} comparable items</span></div></div>`}).join('')||'<div class="empty">No assigned PIC has comparable transactions in selected period.</div>'}</div>`;
  }
  const note=text=>`<div class="note">${esc(text)}</div>`;

  function summaryHTML(data,range){
    const {stats,registered,aging,pending,completed}=data;
    const period=dateLabel(range.from)+' to '+dateLabel(range.to);
    const margin=completed.total_margin;
    const marginPct=num(completed.total_sales)>0&&margin!=null?num(margin)/num(completed.total_sales)*100:0;
    const agingTotals=(aging||[]).reduce((a,r)=>({less_30:a.less_30+num(r.less_30),days_30_90:a.days_30_90+num(r.days_30_90),days_90_180:a.days_90_180+num(r.days_90_180),more_180:a.more_180+num(r.more_180),total_open:a.total_open+num(r.total_open)}),{less_30:0,days_30_90:0,days_90_180:0,more_180:0,total_open:0});
    const settlement=pending.kpi||{};
    const distribution=completed.margin_distribution||{};
    const negative=num(distribution.negative);
    const oldest=(aging||[]).slice().sort((a,b)=>num(b.more_180)-num(a.more_180)).slice(0,8);
    const deliverySegments=[{label:'0-30 days',value:agingTotals.less_30,color:'#2563eb'},{label:'30-90 days',value:agingTotals.days_30_90,color:'#60a5fa'},{label:'90-180 days',value:agingTotals.days_90_180,color:'#f59e0b'},{label:'180+ days',value:agingTotals.more_180,color:'#dc2626'}];
    const settlementSegments=[{label:'0-30 days',value:settlement.less_30,color:'#2563eb'},{label:'30-90 days',value:settlement.days_30_90,color:'#60a5fa'},{label:'90-180 days',value:settlement.days_90_180,color:'#f59e0b'},{label:'180+ days',value:settlement.more_180,color:'#dc2626'}];
    return documentShell('Summary Report',period,
      page('Executive Summary',period,
        kpis([['Completed Orders',count(completed.total_count)],['Purchase Amount',money(completed.total_purchase)],['Sales Amount',money(completed.total_sales)],['Gross Margin',margin==null?'N/A':money(margin),margin!=null&&margin<0?'bad':'good',margin==null?'Missing purchase data':pct(marginPct)],['Pending Delivery',count(stats.total_so_count)],['Pending Settlement',count(settlement.total_pending)]])+
        `<div class="two-col"><div><h2>Margin Record Mix</h2>${segments([{label:'Positive margin',value:distribution.positive,color:'#14b8a6'},{label:'Negative margin',value:negative,color:'#dc2626'},{label:'Zero margin',value:distribution.zero,color:'#94a3b8'}])}</div><div><h2>Operational Attention</h2>${visualBars([{label:'Delivery 180+ days',value:agingTotals.more_180},{label:'Settlement 180+ days',value:settlement.more_180},{label:'Settlement without date',value:settlement.no_date},{label:'Never ordered items',value:(registered.pie||{}).never}],r=>r.label,r=>r.value,count,4,()=> 'risk')}</div></div>`+
        note('All values use dashboard records whose source date falls inside selected reporting period.'))+
      page('Completed Orders and Margin',period,
        `<h2>Monthly Purchase, Sales and Gross Margin</h2>${dualTrend(completed.monthly_trend||[])}`+
        `<div class="two-col"><div><h2>Top Clients by Sales</h2>${bars(completed.top_clients||[],'client','sales_amount',money)}</div><div><h2>Top Vendors by Purchase</h2>${bars(completed.top_vendors||[],'vendor','purchase_amount',money)}</div></div>`)+
      page('Pending Operations',period,
        kpis([['Pending Delivery',count(agingTotals.total_open)],['0-30 Days',count(agingTotals.less_30)],['30-90 Days',count(agingTotals.days_30_90)],['90-180 Days',count(agingTotals.days_90_180)],['180+ Days',count(agingTotals.more_180),'bad']])+
        `<div class="two-col"><div><h2>Delivery Aging Distribution</h2>${segments(deliverySegments)}<h2>Oldest Backlog by Vendor</h2>${visualBars(oldest,r=>r.vendor||'Unknown',r=>r.more_180,count,8,()=> 'risk')}</div><div><h2>Settlement Aging Distribution</h2>${segments(settlementSegments)}<h2>Pending Settlement by Operation Unit</h2>${visualBars(pending.combo||[],r=>r.op_unit||'Unknown',r=>r.amount,money,8)}</div></div>`)+
      page('Registration and Follow-up',period,
        kpis([['Registered Products',count(registered.total_products)],['Ordered',count((registered.pie||{}).ordered)],['Never Ordered',count((registered.pie||{}).never)],['New',count((registered.pie||{}).new)],['Negative Margin Records',count(negative),'bad']])+
        `<div class="two-col"><div><h2>Registration Order Status</h2>${segments([{label:'Ordered',value:(registered.pie||{}).ordered,color:'#14b8a6'},{label:'Never ordered',value:(registered.pie||{}).never,color:'#dc2626'},{label:'New',value:(registered.pie||{}).new,color:'#2563eb'}])}<h2>Top Registered Items by Amount</h2>${visualBars(registered.bar||[],r=>r.prod_name||r.prod_id||'Unknown',r=>r.total_amount,money,8)}</div><div><h2>Settlement Status</h2>${segments([{label:'Sales not settled',value:(pending.pie||{}).sales_only?.count,color:'#2563eb'},{label:'Purchasing not settled',value:(pending.pie||{}).purch_only?.count,color:'#f59e0b'},{label:'Both not settled',value:(pending.pie||{}).both?.count,color:'#dc2626'}])}<h2>Repeated Orders</h2>${visualBars(registered.repeat||[],r=>r.prod_name||r.prod_id||'Unknown',r=>r.order_freq,count,8)}</div></div>`)
    );
  }

  function costHTML(data,range){
    const period=dateLabel(range.from)+' to '+dateLabel(range.to);
    const k=data.kpis||{},pics=data.pic_progress_all||data.pic_progress||[],pareto=(data.pareto||{}).items||[],achieved=data.achieved_reductions||[],vendors=data.vendor_opportunities||[],watch=data.dormant_lost||[];
    const impact=num(k.ytd_price_impact),achievedSaving=num((data.achieved_summary||{}).saving_amount);
    const comparison=[dateLabel((data.period||{}).comparison_from),dateLabel((data.period||{}).comparison_to)].filter(Boolean).join(' to ');
    const target=num(k.target_saving),remaining=Math.max(0,target-impact);
    return documentShell('Cost Reduction & PIC KPI',period,
      page('Cost Reduction Overview',period,
        kpis([['Baseline Spend',money(k.baseline_spend)],['Selected Period Spend',money(k.current_ytd_spend)],['Saving Target',money(k.target_saving)],['Price Impact',money(impact),impact>=0?'good':'bad'],['Cost Reduction',pct(k.cost_reduction_pct),num(k.cost_reduction_pct)>=0?'good':'bad'],['Achieved Saving',money(achievedSaving),'good']])+
        `<div class="two-col"><div><h2>Target Coverage</h2>${segments([{label:'Actual saving',value:Math.max(0,impact),color:'#14b8a6'},{label:'Remaining target',value:remaining,color:'#cbd5e1'}])}<div class="period-compare"><b>Selected</b><span>${esc(period)}</span><b>Comparison</b><span>${esc(comparison||'Not available')}</span></div></div><div><h2>Price Direction</h2>${segments([{label:'Price down',value:k.price_down_items,color:'#14b8a6'},{label:'Price up',value:k.price_up_items,color:'#dc2626'}])}<h2>Comparable Item Mix</h2>${segments([{label:'Historical',value:k.historical_comparable_items,color:'#1e3a8a'},{label:'New repeat',value:k.new_repeat_comparable_items,color:'#60a5fa'}])}</div></div>`+
        note('Positive price impact means saving. Negative price impact means purchase price increased against comparison period.'))+
      page('KPI by PIC',period,
        `<h2>Target Progress by PIC</h2>${progressVisual(pics)}`+
        note('Unassigned transactions remain in overall Cost Reduction totals but do not create a PIC KPI row.'))+
      page('Priority and Achieved Reduction',period,
        `<div class="two-col wide"><div><h2>Pareto Spend Priorities</h2>${visualBars(pareto,r=>(r.product_name||r.product_id||'Unknown')+' · '+(r.pic_name||'Unassigned'),r=>r.current_spend,money,12,r=>num(r.price_change_pct)>0?'risk':'')}</div><div><h2>Achieved Saving</h2>${visualBars(achieved,r=>(r.product_name||r.product_id||'Unknown')+' · '+pct(r.realized_reduction_pct),r=>r.realized_saving_amount,money,12,()=> 'saving')}</div></div>`)+
      page('Sourcing Opportunities and Watchlist',period,
        `<div class="two-col wide"><div><h2>Authorized Vendor Opportunities</h2>${visualBars(vendors,r=>(r.manufacturer_name||'Unknown')+' · '+count(r.item_count)+' items',r=>r.current_spend,money,12)}</div><div><h2>Risk Watchlist</h2>${visualBars(watch,r=>(r.product_name||r.product_id||'Unknown')+' · '+String(r.risk_status||'review').replaceAll('_',' '),r=>r.risk_amount,money,12,()=> 'risk')}</div></div>`)
    );
  }

  function documentShell(title,period,pages){
    return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)} - ${esc(period)}</title><style>
      :root{--primary:#2563eb;--primary-dark:#1e3a8a;--ink:#1f2937;--muted:#64748b;--line:#e2e8f0;--surface:#f8fafc;--good:#047857;--good-fill:#14b8a6;--bad:#dc2626}*{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:var(--ink);font-family:Inter,'Segoe UI',Arial,sans-serif}.toolbar{position:sticky;top:0;z-index:5;display:flex;justify-content:space-between;align-items:center;gap:16px;padding:10px 18px;background:#fff;color:var(--ink);border-bottom:1px solid var(--line)}.toolbar p{margin:0;font-size:13px}.toolbar button{border:1px solid #cbd5e1;border-radius:9px;background:#f8fafc;color:#344054;padding:9px 14px;font-weight:700;cursor:pointer}.report-page{width:297mm;min-height:210mm;margin:12px auto;padding:14mm 16mm 11mm;background:#fff;box-shadow:0 5px 24px rgba(15,23,42,.12);position:relative;page-break-after:always}.report-header{display:flex;justify-content:space-between;align-items:flex-start;border-top:4px solid var(--primary);padding-top:8px;margin-bottom:14px}.eyebrow{font-size:9px;letter-spacing:1.6px;color:var(--primary);font-weight:700}.report-header h1{margin:3px 0 2px;color:var(--ink);font-size:23px}.report-header p{margin:0;color:var(--muted);font-size:11px}.cover-page{overflow:hidden;background:linear-gradient(135deg,#fff 0 72%,#eff6ff 72% 100%)}.cover-top{display:flex;justify-content:flex-end;align-items:center;padding-bottom:12px;border-bottom:1px solid var(--line);color:var(--muted);font-size:11px;font-weight:600}.cover-main{display:flex;min-height:145mm;flex-direction:column;justify-content:center;align-items:flex-start}.cover-mark{width:72px;height:7px;background:var(--primary);margin-bottom:22px}.cover-main p{margin:0 0 10px;color:var(--primary);font-size:11px;font-weight:800;letter-spacing:2px}.cover-main h1{max-width:820px;margin:0;color:var(--ink);font-size:42px;line-height:1.1;letter-spacing:-1.4px}.cover-period{display:grid;gap:5px;margin-top:30px;padding:16px 20px;border-left:4px solid var(--primary);background:#eff6ff}.cover-period span{color:var(--muted);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px}.cover-period strong{color:var(--primary-dark);font-size:18px}h2{margin:13px 0 7px;color:var(--ink);font-size:14px}.kpi-row{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));border:1px solid var(--line);margin-bottom:12px}.kpi{min-width:0;padding:10px;border-right:1px solid var(--line)}.kpi:last-child{border-right:0}.kpi span{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;font-weight:700}.kpi strong{display:block;margin-top:5px;color:var(--primary-dark);font-size:16px;white-space:nowrap}.kpi small{display:block;margin-top:3px;color:var(--muted);font-size:8px}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:14px}.two-col.wide{grid-template-columns:1.12fr .88fr}table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:9px}th{padding:6px;background:#e2e8f0;color:#334155;text-align:right;border:1px solid #cbd5e1}th:first-child{text-align:left}td{padding:5px 6px;text-align:right;border:1px solid var(--line);overflow-wrap:anywhere}td.label{text-align:left;font-weight:600}tbody tr:nth-child(even){background:var(--surface)}.good{color:var(--good)!important}.bad{color:var(--bad)!important}.bars,.visual-bars{display:grid;gap:6px}.bar-row,.visual-row{display:grid;grid-template-columns:35% 1fr 70px;gap:7px;align-items:center;font-size:8px}.bar-label,.visual-label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bar-track,.visual-track{height:10px;background:#e2e8f0}.bar-track i,.visual-track i{display:block;height:100%;background:var(--primary)}.visual-track i.risk{background:var(--bad)}.visual-track i.saving{background:var(--good-fill)}.bar-row b,.visual-row b{text-align:right}.segments{padding:4px 0 8px}.segment-track{height:22px;display:flex;overflow:hidden;background:#eef2f7}.segment-track i{display:block;height:100%}.segment-legend{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 12px;margin-top:9px}.segment-legend>div{display:grid;grid-template-columns:8px 1fr auto;align-items:center;gap:6px;font-size:8px}.segment-legend i{width:8px;height:8px}.segment-legend b{font-weight:700}.dual-trend{display:grid;gap:5px;margin-bottom:8px}.dual-row{display:grid;grid-template-columns:50px 1fr 70px;gap:7px;align-items:center;font-size:8px}.dual-row>div{display:grid;gap:2px}.dual-row i{display:block;height:5px}.purchase{background:#334155}.sales{background:var(--primary)}.dual-row b{text-align:right;color:#344054}.chart-key{display:flex;align-items:center;gap:5px;color:var(--muted);font-size:8px}.chart-key i{width:11px;height:6px}.chart-key b{margin-left:auto}.progress-list{display:grid;grid-template-columns:1fr 1fr;gap:10px 14px}.progress-item{border:1px solid var(--line);padding:9px}.progress-head,.progress-meta{display:flex;justify-content:space-between;gap:8px;font-size:8px}.progress-head b{font-size:10px;color:var(--ink)}.progress-track{height:10px;background:#e2e8f0;margin:7px 0;overflow:hidden}.progress-track i{display:block;height:100%;background:var(--primary)}.progress-track i.achieved{background:var(--good-fill)}.progress-track i.negative{background:var(--bad)}.progress-meta{color:var(--muted)}.period-compare{display:grid;grid-template-columns:75px 1fr;gap:6px;margin-top:12px;padding:9px;background:#eff6ff;font-size:9px}.note,.empty{margin-top:10px;padding:8px 10px;background:var(--surface);border-left:3px solid var(--primary);font-size:9px;color:#475569}.empty{text-align:center;border-left:0}footer{position:absolute;left:16mm;right:16mm;bottom:6mm;display:flex;justify-content:space-between;border-top:1px solid var(--line);padding-top:4px;color:#64748b;font-size:8px;text-transform:uppercase}
      @media print{@page{size:A4 landscape;margin:0}body{background:#fff}.toolbar{display:none}.report-page{margin:0;width:297mm;height:210mm;min-height:210mm;box-shadow:none;overflow:hidden}}
      @media(max-width:900px){.report-page{width:100%;min-height:auto;margin:0;padding:20px}.kpi-row{grid-template-columns:repeat(2,1fr)}.two-col,.two-col.wide{grid-template-columns:1fr}footer{position:static;margin-top:20px}}
    </style></head><body><div class="toolbar"><p>${esc(title)} · ${esc(period)}</p><button onclick="window.print()">Print / Save as PDF</button></div>${coverPage(title,period)}${pages}<script>document.querySelectorAll('.report-page').forEach((page,index)=>{const footer=page.querySelector('footer');if(footer)footer.insertAdjacentHTML('beforeend','<b>Page '+(index+1)+'</b>')});<\/script></body></html>`;
  }

  function showPreview(html,type,range){
    document.getElementById('z-report-preview')?.remove();
    const preview=document.createElement('div');
    preview.id='z-report-preview';
    preview.innerHTML=`<div class="z-report-preview-head"><span>${esc(type==='summary'?'Summary Report':'Cost Reduction & PIC KPI')} · ${esc(dateLabel(range.from))} to ${esc(dateLabel(range.to))}</span><button type="button">Close Preview</button></div><iframe title="Printable report preview"></iframe>`;
    function closePreview(){document.removeEventListener('keydown',onKeydown);preview.remove()}
    function onKeydown(event){if(event.key==='Escape')closePreview()}
    preview.querySelector('button').addEventListener('click',closePreview);
    document.addEventListener('keydown',onKeydown);
    preview.querySelector('iframe').srcdoc=html;
    document.body.appendChild(preview);
    preview.querySelector('button').focus();
  }

  async function createReport(dialog){
    const type=dialog.querySelector('#z-report-type').value;
    const range={from:dialog.querySelector('#z-report-from').value,to:dialog.querySelector('#z-report-to').value};
    const error=dialog.querySelector('#z-report-error');
    if(!range.from||!range.to||range.from>range.to){error.textContent='Choose a valid date range.';error.classList.add('show');return}
    const button=dialog.querySelector('#z-report-generate');button.disabled=true;button.textContent='Preparing...';error.classList.remove('show');
    const query={date_from:range.from,date_to:range.to};
    try{
      let html;
      if(type==='summary'){
        const [stats,registered,aging,pending,completed]=await Promise.all([
          getJSON('/api/dashboard/stats',query),getJSON('/api/dashboard/registered-items-summary',query),getJSON('/api/data/aging',query),getJSON('/api/data/pending-settlement',query),getJSON('/api/completed/summary',{...query,mode:'dashboard'})
        ]);
        html=summaryHTML({stats,registered,aging,pending,completed},range);
      }else{
        html=costHTML(await getJSON('/api/cost-reduction/analysis',query),range);
      }
      showPreview(html,type,range);dialog.close();
    }catch(err){
      error.textContent='Report could not be created: '+(err&&err.message?err.message:'Unknown error');error.classList.add('show');
    }finally{button.disabled=false;button.textContent='Create Printable Report'}
  }

  const observer=new MutationObserver(addButton);
  function start(){addButton();observer.observe(document.getElementById('root')||document.body,{childList:true,subtree:true})}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
