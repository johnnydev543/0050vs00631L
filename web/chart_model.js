(function(root){
  'use strict';
  function niceStep(value){
    const magnitude=10**Math.floor(Math.log10(value)),norm=value/magnitude;
    return (norm<=1?1:norm<=2?2:norm<=5?5:10)*magnitude;
  }
  function create(list,{key='nav',width=800,scale='linear',principal=100,target=160}={}){
    if(!list.length||!list[0].result.daily.length)throw Error('線圖沒有逐日資料。');
    const rows=list[0].result.daily,dates=rows.map(r=>r.date);
    const times=dates.map(d=>Date.parse(d+'T00:00:00Z'));
    if(times.some((t,i)=>!Number.isFinite(t)||(i>0&&t<=times[i-1])))throw Error('線圖日期必須依序且不重複。');
    const value=row=>key==='nav'?row.nav/10000:row.nominal_market_exposure*100;
    const values=list.map(s=>{
      if(s.result.daily.length!==dates.length||s.result.daily.some((r,i)=>r.date!==dates[i]))throw Error('各配置日期不一致，無法繪圖。');
      const v=s.result.daily.map(value);
      if(v.some(n=>!Number.isFinite(n)||(key==='nav'&&n<=0)))throw Error('線圖數值無效。');
      return v;
    });
    let min=Infinity,max=-Infinity;
    for(const series of values)for(const v of series){min=Math.min(min,v);max=Math.max(max,v);}
    if(key==='nav'){min=Math.min(min,principal);max=Math.max(max,principal);}
    else{min=Math.min(min,target);max=Math.max(max,target);}
    const logarithmic=key==='nav'&&scale==='log';
    let lo,hi,ticks=[];
    if(logarithmic){
      const span=Math.max(.1,Math.log10(max/min));
      lo=10**(Math.log10(min)-span*.06);hi=10**(Math.log10(max)+span*.06);
      for(let p=Math.floor(Math.log10(lo));p<=Math.ceil(Math.log10(hi));p++)for(const m of [1,2,5]){
        const v=m*10**p;if(v>=lo&&v<=hi)ticks.push(v);
      }
      if(ticks.length<2)ticks=[min,max===min?hi:max];
    }else{
      const padding=key==='nav'?max*.06:Math.max(3,(max-min)*.12);
      lo=key==='nav'?0:Math.max(0,min-padding);hi=max+padding;
      if(hi-lo<5)hi=lo+5;
      const step=niceStep((hi-lo)/4);lo=Math.floor(lo/step)*step;hi=Math.ceil(hi/step)*step;
      for(let v=lo;v<=hi+step/10;v+=step)ticks.push(v);
    }
    const height=320,left=70,right=24,top=22,bottom=50,plotW=width-left-right,plotH=height-top-bottom;
    const start=times[0],end=times[times.length-1];
    const x=t=>left+(t-start)/Math.max(1,end-start)*plotW;
    const transform=v=>logarithmic?Math.log10(v):v;
    const y=v=>top+(transform(hi)-transform(v))/(transform(hi)-transform(lo))*plotH;
    const inverseY=py=>{const v=transform(hi)-(py-top)/plotH*(transform(hi)-transform(lo));return logarithmic?10**v:v;};
    const nearest=px=>{
      const t=start+(px-left)/plotW*(end-start);let low=0,high=times.length-1;
      while(low<high){const mid=Math.floor((low+high)/2);if(times[mid]<t)low=mid+1;else high=mid;}
      return low>0&&Math.abs(times[low-1]-t)<Math.abs(times[low]-t)?low-1:low;
    };
    return {width,height,left,right,top,bottom,plotW,plotH,dates,times,start,end,lo,hi,ticks,values,value,x,y,inverseY,nearest,
      points:values.map(series=>series.map((v,i)=>[x(times[i]),y(v)]))};
  }
  const api={create};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.ETFCharts=api;
})(typeof globalThis==='object'?globalThis:this);
