(async () => {
  'use strict';
  const $ = id => document.getElementById(id);
  const packed = JSON.parse($('bundle').textContent);
  const bytes = Uint8Array.from(atob(packed.gzip), character=>character.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  const bundle = JSON.parse(await new Response(stream).text());
  if(!bundle.seed.selected)bundle.seed.selected=bundle.seed.comparisons[0].result;
  let result = bundle.seed;
  let ready = false;
  let jobId = 0;
  const money = n => Number(n).toLocaleString('zh-TW',{minimumFractionDigits:2,maximumFractionDigits:2});
  const pct = n => n == null ? '—' : (Number(n)*100).toFixed(2)+'%';
  const ratio = n => n == null ? '—' : Number(n).toFixed(2);
  const percentage = n => Number(n).toLocaleString('zh-TW',{maximumFractionDigits:2})+'%';
  const rules = {quarterly:'季度再平衡',threshold:'權重偏離門檻',exposure_threshold:'曝險偏離門檻'};
  const colors = {A:'var(--a)',B:'var(--b)',C:'var(--c)',custom:'var(--custom)'};
  const status = (text,error=false) => {$('status').textContent=text;$('status').classList.toggle('error',error);};

  function syncTheme() {
    const dark = document.documentElement.dataset.theme==='dark';
    $('theme').textContent=dark?'淺色主題':'深色主題';
    $('theme').setAttribute('aria-pressed',String(dark));
  }
  $('theme').addEventListener('click',()=>{
    const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';
    document.documentElement.dataset.theme=theme;
    try{localStorage.setItem('etf-report-theme',theme);}catch(e){}
    syncTheme();
  });
  syncTheme();

  function params() {
    return {exposure:Number($('exposure').value),ordinary:Number($('ordinary').value),
            principal:Number($('principal').value),cash_rate:Number($('cashRate').value),
            rule:$('rule').value,threshold:Number($('threshold').value)};
  }
  function preview() {
    const exposure=Number($('exposure').value);
    const maximum=Math.max(0,Math.min(exposure,200-exposure));
    $('ordinary').max=String(maximum);
    if(Number($('ordinary').value)>maximum)$('ordinary').value=String(maximum);
    const ordinary=Number($('ordinary').value);
    const leveraged=(exposure-ordinary)/2;
    const cash=100-leveraged-ordinary;
    const valid=Number.isFinite(exposure)&&exposure>=0&&exposure<=200&&ordinary>=0&&cash>=-1e-8;
    $('mixHint').textContent='此曝險下可設定0%～'+percentage(maximum)+'。';
    $('wL').textContent=valid?percentage(leveraged):'—';
    $('w50').textContent=valid?percentage(ordinary):'—';
    $('wCash').textContent=valid?percentage(Math.max(0,cash)):'—';
    $('barL').style.width=(valid?leveraged:0)+'%';
    $('bar50').style.width=(valid?ordinary:0)+'%';
    $('barCash').style.width=(valid?Math.max(0,cash):0)+'%';
    document.querySelectorAll('[data-preset]').forEach(button=>{
      const target={A:0,B:40,C:20}[button.dataset.preset];
      button.setAttribute('aria-pressed',String(exposure===160&&ordinary===target));
    });
  }
  ['exposure','ordinary'].forEach(id=>$(id).addEventListener('input',preview));
  document.querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',()=>{
    $('exposure').value='160';$('ordinary').value=String({A:0,B:40,C:20}[button.dataset.preset]);preview();
  }));
  $('rule').addEventListener('change',()=>{
    const exposureRule=$('rule').value==='exposure_threshold';
    $('threshold').disabled=$('rule').value==='quarterly';
    $('threshold').max=exposureRule?'200':'100';
    $('threshold').value=exposureRule?'20':'10';
    $('thresholdLabel').textContent=exposureRule?'曝險偏離門檻':'權重偏離門檻';
  });
  preview();

  function series() {
    const matches=result.comparisons.find(x=>Math.abs(x.result.params.ordinary-result.params.ordinary)<1e-9);
    const list=result.comparisons.map(x=>({id:x.id,name:x.id+(matches?.id===x.id?'（本次設定）':''),
                                         result:x.result,selected:matches?.id===x.id}));
    if(!matches)list.push({id:'custom',name:'自訂（本次設定）',result:result.selected,selected:true});
    return list;
  }

  function renderTables(list) {
    let rows='';
    for(const item of list){const m=item.result.metrics,w=item.result.params.weights;
      rows+=`<tr class="${item.selected?'selected':''}"><th>${item.name}</th><td>${percentage(w['00631L']*100)}／${percentage(w['0050']*100)}／${percentage(w.cash*100)}</td><td>${money(m.initial_capital/10000)}</td><td>${money(m.ending_nav/10000)}</td><td>${pct(m.cagr)}</td><td>${pct(m.max_drawdown)}</td><td>${ratio(m.sharpe)}</td><td>${ratio(m.sortino)}</td><td>${m.longest_recovery?.duration_calendar_days||0}天${m.longest_recovery?.censored?'（未回本）':''}</td><td>${pct(m.mean_nominal_exposure)}</td><td>${m.rebalance_count}</td><td>${money(m.total_trading_cost/10000)}</td></tr>`;
    }
    $('comparisonTable').innerHTML='<table><thead><tr><th>方案</th><th>正二／0050／現金</th><th>本金（萬）</th><th>期末（萬）</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>Sortino</th><th>最長回本</th><th>平均曝險</th><th>再平衡次數</th><th>成本（萬）</th></tr></thead><tbody>'+rows+'</tbody></table>';
    let annual='';
    for(let i=0;i<result.selected.annual.length;i++){
      const base=result.selected.annual[i];
      annual+=`<tr><th>${base.year}${base.partial_year?'＊':''}</th>`+list.map(x=>{
        const stat=x.result.annual[i];return `<td>${pct(stat.period_return)}／${pct(stat.max_drawdown)}</td>`;
      }).join('')+'</tr>';
    }
    $('annualTable').innerHTML='<table><thead><tr><th>年度</th>'+list.map(x=>`<th>${x.name}</th>`).join('')+'</tr></thead><tbody>'+annual+'</tbody></table>';
    let crises='';
    for(const year of [2020,2022])for(const item of list){
      const stat=item.result.crises.find(x=>x.year===year);
      crises+=`<tr><th>${year} · ${item.name}</th><td>${pct(stat.max_drawdown)}</td><td>${stat.peak_date}</td><td>${stat.trough_date}</td><td>${stat.recovery_date||'尚未回本'}</td><td>${stat.peak_to_recovery_days}天${stat.censored?'＋':''}</td></tr>`;
    }
    $('crisisTable').innerHTML='<table><thead><tr><th>事件／方案</th><th>最大回撤</th><th>高點</th><th>谷底</th><th>回到高點</th><th>回本天數</th></tr></thead><tbody>'+crises+'</tbody></table>';
  }

  function niceStep(value){const magnitude=10**Math.floor(Math.log10(value));const norm=value/magnitude;return(norm<=1?1:norm<=2?2:norm<=5?5:10)*magnitude;}
  function chart(container,key,list){
    const width=Math.max(260,container.getBoundingClientRect().width),height=300;
    const left=64,right=16,top=20,bottom=42,plotW=width-left-right,plotH=height-top-bottom;
    const times=list[0].result.daily.map(x=>Date.parse(x.date+'T00:00:00Z'));
    const start=times[0],end=times[times.length-1];
    const value=(row)=>key==='nav'?row.nav/10000:row.nominal_market_exposure*100;
    let min=Infinity,max=-Infinity;
    for(const s of list)for(const row of s.result.daily){const v=value(row);min=Math.min(min,v);max=Math.max(max,v);}
    if(key==='nav')min=0;
    const padding=key==='nav'?max*.06:Math.max(3,(max-min)*.12);
    let lo=key==='nav'?0:Math.max(0,min-padding),hi=max+padding;
    if(hi-lo<5)hi=lo+5;
    const step=niceStep((hi-lo)/4);
    lo=Math.floor(lo/step)*step;hi=Math.ceil(hi/step)*step;
    const x=t=>left+(t-start)/(end-start)*plotW,y=v=>top+(hi-v)/(hi-lo)*plotH;
    let markup=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${key==='nav'?'資產成長，單位萬元':'名目市場曝險，單位百分比'}"><title>${key==='nav'?'同曝險資產成長':'每日名目市場曝險'}</title>`;
    for(let v=lo;v<=hi+step/10;v+=step){
      const label=key==='nav'?Number(v.toFixed(1)).toLocaleString('zh-TW'):v.toFixed(0)+'%';
      markup+=`<line x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${left-10}" y="${y(v)+4}" text-anchor="end" fill="var(--muted)" font-size="12">${label}</text>`;
    }
    const years=width<480?[2015,2019,2023,2026]:[2015,2017,2019,2021,2023,2026];
    for(const year of years){const t=Date.parse(year+'-01-01T00:00:00Z');markup+=`<text x="${x(t)}" y="${height-16}" text-anchor="middle" fill="var(--muted)" font-size="12">${year}</text>`;}
    if(key!=='nav'){const target=result.params.exposure;markup+=`<line x1="${left}" x2="${width-right}" y1="${y(target)}" y2="${y(target)}" stroke="var(--muted)" stroke-dasharray="4 4"/><text x="${width-right-5}" y="${y(target)-7}" text-anchor="end" fill="var(--muted)" font-size="12">目標${percentage(target)}</text>`;}
    for(const s of [...list].sort((a,b)=>Number(a.selected)-Number(b.selected))){
      const points=s.result.daily.map((row,i)=>x(times[i]).toFixed(2)+','+y(value(row)).toFixed(2)).join(' ');
      markup+=`<polyline points="${points}" fill="none" stroke="${colors[s.id]}" stroke-width="${s.selected?2.4:1.5}" opacity=".95"/>`;
    }
    markup+=`<line data-guide x1="0" x2="0" y1="${top}" y2="${height-bottom}" stroke="var(--muted)" hidden/><rect data-hit x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="transparent" tabindex="0" aria-label="使用左右方向鍵查看逐日數值"/></svg>`;
    container.innerHTML=markup;
    const svg=container.querySelector('svg'),hit=svg.querySelector('[data-hit]'),guide=svg.querySelector('[data-guide]');
    let activeIndex=times.length-1;
    function show(index,event){
      activeIndex=Math.max(0,Math.min(times.length-1,index));const d=list[0].result.daily[activeIndex].date;
      guide.hidden=false;guide.setAttribute('x1',x(times[activeIndex]));guide.setAttribute('x2',x(times[activeIndex]));
      const tooltip=$('tooltip');tooltip.innerHTML='<strong>'+d+'</strong>'+list.map(s=>`<div><span>${s.name}</span><b>${key==='nav'?money(value(s.result.daily[activeIndex]))+' 萬元':percentage(value(s.result.daily[activeIndex]))}</b></div>`).join('');
      tooltip.hidden=false;
      const bounds=svg.getBoundingClientRect();const px=event?.clientX||bounds.left+x(times[activeIndex]),py=event?.clientY||bounds.top+35;
      tooltip.style.left=Math.max(8,Math.min(innerWidth-tooltip.offsetWidth-12,px+14))+'px';
      tooltip.style.top=Math.max(8,Math.min(innerHeight-tooltip.offsetHeight-12,py+14))+'px';
    }
    hit.addEventListener('pointermove',event=>{
      const bounds=svg.getBoundingClientRect();const px=(event.clientX-bounds.left)/bounds.width*width;
      const t=start+(px-left)/plotW*(end-start);let low=0,high=times.length-1;
      while(low<high){const mid=Math.floor((low+high)/2);if(times[mid]<t)low=mid+1;else high=mid;}
      const index=low>0&&Math.abs(times[low-1]-t)<Math.abs(times[low]-t)?low-1:low;show(index,event);
    });
    hit.addEventListener('pointerleave',()=>{guide.hidden=true;$('tooltip').hidden=true;});
    hit.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();show(activeIndex+(event.key==='ArrowLeft'?-1:1));}});
    hit.addEventListener('blur',()=>{guide.hidden=true;$('tooltip').hidden=true;});
  }

  function render(){
    const m=result.selected.metrics,p=result.params,w=p.weights,list=series();
    $('ending').textContent=money(m.ending_nav/10000);$('cagr').textContent=pct(m.cagr);$('mdd').textContent=pct(m.max_drawdown);
    const threshold=p.rule==='quarterly'?'':' · '+Number(p.threshold).toLocaleString('zh-TW',{maximumFractionDigits:3})+'個百分點門檻';
    $('resultContext').textContent=`本次結果：本金${money(p.principal)}萬元 · 目標曝險${percentage(p.exposure)} · 正二${percentage(w['00631L']*100)}／0050 ${percentage(w['0050']*100)}／現金${percentage(w.cash*100)} · ${rules[p.rule]}${threshold} · 現金年收益${percentage(p.cash_rate)}`;
    chart($('navChart'),'nav',list);chart($('exposureChart'),'exposure',list);
    $('navLegend').innerHTML=list.map(s=>`<span><i style="background:${colors[s.id]}"></i>${s.name}</span>`).join('');
    renderTables(list);
  }
  let resizeTimer;
  addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(render,100);});
  render();

  const workerURL=URL.createObjectURL(new Blob([$('workerSource').textContent],{type:'text/javascript'}));
  const worker=new Worker(workerURL);
  worker.onmessage=event=>{
    const message=event.data;
    if(message.type==='status')status(message.message);
    else if(message.type==='ready'){ready=true;$('run').disabled=false;status('已準備好，可調整設定後重新計算。');}
    else if(message.type==='result'&&message.id===jobId){result=message.result;render();$('run').disabled=false;$('run').textContent='計算並比較';status('已完成：自訂設定及A／B／C均以相同條件重算。');}
    else if(message.type==='error'){if(!message.init)$('run').disabled=!ready;$('run').textContent='計算並比較';status(message.init?'計算環境無法載入。請確認網路後重新開啟。':message.message,true);}
  };
  worker.onerror=()=>{status('計算環境無法啟動。請確認瀏覽器及網路後重新開啟。',true);$('run').disabled=true;};
  worker.postMessage({type:'init',bundle:{data:bundle.data,modules:bundle.modules}});
  $('form').addEventListener('submit',event=>{
    event.preventDefault();if(!ready||!$('form').reportValidity())return;
    $('run').disabled=true;$('run').textContent='正在計算…';status('正在重算自訂設定與三種同曝險配置…');
    worker.postMessage({type:'calculate',id:++jobId,params:params()});
  });

  function download(text,type,name){const url=URL.createObjectURL(new Blob([text],{type}));const anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  $('downloadDaily').addEventListener('click',()=>{
    const keys=['date','nav','return','cash','receivables','units_00631L','units_0050','weight_00631L','weight_0050','weight_cash','weight_receivables','nominal_market_exposure','actual_internal_exposure','drawdown'];
    const lines=[keys.join(','),...result.selected.daily.map(row=>keys.map(k=>row[k]??'').join(','))];download('\uFEFF'+lines.join('\n'),'text/csv;charset=utf-8','每日資產與名目曝險.csv');
  });
  $('downloadConfig').addEventListener('click',()=>download(JSON.stringify(result.selected,null,2),'application/json','此次回測設定與結果.json'));
})().catch(()=>{const status=document.getElementById('status');status.textContent='頁面無法載入，請以支援現代網頁功能的瀏覽器重新開啟。';status.classList.add('error');});
