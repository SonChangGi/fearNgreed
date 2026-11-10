/* Quant 0.4.1 chart grammar. Calculations remain in the signal/strategy engines. */
const COLORS={price:'#b1bac0',long:'#60c8d2',inverse:'#e8bb62',benchmark:'#a89be8',fear:'#86bbf0',greed:'#e8bb62',current:'#ed9aa7'};
const finite=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
const pct=v=>finite(v)==null?'—':`${(v*100).toLocaleString('ko-KR',{maximumFractionDigits:2})}%`;
const number=v=>finite(v)==null?'—':v.toLocaleString('ko-KR',{maximumFractionDigits:2});
export function lineSeries(rows,key,name,color,axis=0,percent=false,extra={}){
  return {name,type:'line',xAxisIndex:axis,yAxisIndex:axis,data:rows.map(r=>finite(r[key])),showSymbol:false,connectNulls:false,symbolSize:7,lineStyle:{width:2.2,color},itemStyle:{color},emphasis:{focus:'none',lineStyle:{width:3.4}},tooltip:{valueFormatter:percent?pct:number},...extra};
}
function palette(){const light=document.documentElement.dataset.theme==='light';return {text:light?'#26383e':'#edf3f2',muted:light?'#52676e':'#a6b4b6',grid:light?'#dce3e5':'#2b363c',surface:light?'#ffffff':'#20282c'};}
export function trendOption(id,rows,context={}){
 const t=palette(), history=id==='history-chart',equity=id==='equity-chart',panels=history?3:equity?2:1;
 const heights=history?[[48,220],[310,72],[440,205]]:equity?[[46,225],[330,190]]:[[44,245]];
 const grid=heights.map(([top,height])=>({left:60,right:24,top,height,containLabel:false}));
 const xAxis=grid.map((_,i)=>({type:'category',gridIndex:i,data:rows.map(r=>r.date),boundaryGap:false,axisLine:{lineStyle:{color:t.grid}},axisTick:{show:false},axisLabel:{color:t.muted,hideOverlap:true,fontSize:11,formatter:v=>v.slice(2),show:i===panels-1},axisPointer:{show:true}}));
 const yAxis=grid.map((_,i)=>({type:'value',gridIndex:i,scale:true,axisLabel:{color:t.muted,fontSize:11,formatter:history?i===0?number:i===1?number:pct:equity?pct:number},splitNumber:4,splitLine:{lineStyle:{color:t.grid,type:'dashed'}}}));
 let series=[],title=[];
 if(history){
  const priceRows=rows.map(r=>({...r,kospi:finite(r.kospiClose)??finite(r.kospi)}));
  series.push(lineSeries(priceRows,'kospi','KOSPI',COLORS.price));
  for(const [state,label,color,symbol]of [['extreme_fear','공포',COLORS.fear,'circle'],['extreme_greed','탐욕',COLORS.greed,'diamond']])series.push({name:label,type:'scatter',xAxisIndex:0,yAxisIndex:0,symbol,symbolSize:7,itemStyle:{color},data:priceRows.flatMap((r,i)=>r.signal?.state===state?[[i,r.kospi]]:[])});
  const showCash=rows.some(r=>finite(r.longCashValue)!=null),showInverse=rows.some(r=>finite(r.longShortValue)!=null);
  yAxis[1]={type:'category',gridIndex:1,data:['롱/현금','롱/인버스'],axisLabel:{color:t.muted,fontSize:10},axisTick:{show:false},axisLine:{show:false}};
  for(const [field,lane]of [['longCashPosition',0],['longShortPosition',1]]){
   for(const [position,color]of [['long',COLORS.long],['inverse',COLORS.inverse]])series.push({name:position==='long'?'롱 보유':'인버스 보유',type:'scatter',xAxisIndex:1,yAxisIndex:1,symbol:'rect',symbolSize:[4,14],itemStyle:{color},data:rows.flatMap((r,i)=>r[field]===position?[[i,lane]]:[])});
  }
  for(const [policy,lane]of [['long_cash',0],['long_inverse_cash',1]])for(const [kind,label,symbol,color]of [['entry','진입','triangle',COLORS.long],['exit','청산','path://M-5,-3 L-3,-5 L0,-2 L3,-5 L5,-3 L2,0 L5,3 L3,5 L0,2 L-3,5 L-5,3 L-2,0 Z',COLORS.price],['switch','교체','diamond',COLORS.inverse]]){
   const data=rows.flatMap((r,i)=>(r.actions||[]).filter(a=>a.policy===policy&&((kind==='exit'&&/exit|close|liquid/i.test(a.action||a.type||''))||(kind==='switch'&&/switch|revers/i.test(a.action||a.type||''))||(kind==='entry'&&!/exit|close|liquid|switch|revers/i.test(a.action||a.type||'')))).map(a=>({value:[i,lane],action:a,date:r.date,symbolRotate:kind==='entry'&&(a.toPosition||a.to||a.positionAfterOpen)==='inverse'?180:0,itemStyle:{color:kind==='entry'?((a.toPosition||a.to||a.positionAfterOpen)==='inverse'?COLORS.inverse:COLORS.long):color,opacity:a.includedInWindowMetrics===false?.45:1},label:kind==='switch'?{show:true,formatter:'↔',color:t.surface,fontSize:8}:undefined})));
   if(data.length)series.push({name:label,type:'scatter',xAxisIndex:1,yAxisIndex:1,symbol,symbolSize:kind==='switch'?13:10,itemStyle:{color,borderColor:t.text,borderWidth:1},tooltip:{trigger:'item',formatter:p=>`${p.data.date} · ${label}<br>${p.data.action.signalDate||p.data.action.sourceSignalDate||'—'} 종가 → ${p.data.date} 시가<br>${p.data.action.toTicker||p.data.action.fromTicker||''}`},data});
  }
  if(showCash)series.push(lineSeries(rows,'longCashReturn','롱/현금',COLORS.long,2,true));
  if(showInverse)series.push(lineSeries(rows,'longShortReturn','롱/인버스',COLORS.inverse,2,true,{lineStyle:{color:COLORS.inverse,width:2.2,type:'dashed'}}));
  series.push(lineSeries(rows,'buyHoldReturn','ETF B&H',COLORS.benchmark,2,true));
  title=[{text:'KOSPI · 종가',top:12},{text:'체결 후 보유',top:282},{text:'비용 후 누적수익률',top:408}];
 }else if(equity){
  const hasComparison=rows.some(r=>finite(r.longShortValue)!=null);
  for(const [key,name,color]of [['strategyReturn',context.primaryPolicy==='compare'?'롱/현금':context.primaryLabel||'전략',context.primaryPolicy==='long_inverse_cash'?COLORS.inverse:COLORS.long],...(hasComparison?[['longShortReturn','롱/인버스',COLORS.inverse]]:[]),['buyHoldReturn','ETF B&H',COLORS.benchmark]])series.push(lineSeries(rows,key,name,color,0,true));
  for(const [key,name,color]of [['drawdown',context.primaryPolicy==='compare'?'롱/현금':context.primaryLabel||'전략',context.primaryPolicy==='long_inverse_cash'?COLORS.inverse:COLORS.long],...(hasComparison?[['longShortDrawdown','롱/인버스',COLORS.inverse]]:[]),['buyHoldDrawdown','ETF B&H',COLORS.benchmark]])series.push(lineSeries(rows,key,name,color,1,true));
  yAxis[1].max=0;title=[{text:'누적수익률',top:10},{text:'고점 대비 낙폭',top:296}];
 }else if(id==='residual-chart'){
  yAxis[0].min=0;yAxis[0].max=100; yAxis[0].interval=20;
  const cut=context.cut||2;
  series=[lineSeries(rows,'selectedPercentile','잔차 백분위',COLORS.long,0,false,{markArea:{silent:true,label:{show:false},data:[[{yAxis:0,itemStyle:{color:'#86bbf022'}},{yAxis:cut}],[{yAxis:100-cut,itemStyle:{color:'#e8bb6222'}},{yAxis:100}]]},markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:t.muted,type:'dashed'},data:[{yAxis:20},{yAxis:80} ]}})];
  series.push({name:'거래 차단',type:'scatter',data:rows.flatMap((r,i)=>r.selectedEligible!==true&&finite(r.selectedPercentile)!=null?[[i,r.selectedPercentile]]:[]),symbol:'circle',symbolSize:5,itemStyle:{color:t.surface,borderColor:t.muted,borderWidth:1}});
 }else{const key=rows.some(r=>finite(r.muHynixRelativeSpread)!=null)?'muHynixRelativeSpread':'muHynixRatioIndexed';series=[lineSeries(rows,key,key==='muHynixRelativeSpread'?'상대 스프레드':'비율지수',COLORS.long)];}
 return {animation:false,backgroundColor:'transparent',textStyle:{fontFamily:'Pretendard, sans-serif',color:t.text},grid,xAxis,yAxis,title:title.map(v=>({...v,left:12,textStyle:{color:t.text,fontSize:13,fontWeight:600}})),axisPointer:{link:[{xAxisIndex:'all'}]},tooltip:{trigger:'axis',confine:true,backgroundColor:t.surface,borderColor:t.grid,textStyle:{color:t.text,fontSize:12},axisPointer:{type:'line'},extraCssText:'max-width:calc(100vw - 32px);white-space:normal'},legend:{show:!history,type:'scroll',top:equity?535:0,textStyle:{color:t.muted},pageTextStyle:{color:t.muted}},dataZoom:[{type:'inside',xAxisIndex:grid.map((_,i)=>i),filterMode:'none',preventDefaultMouseMove:false,zoomOnMouseWheel:true,moveOnMouseMove:true}],series};
}
export function mountTrend(chart,rows,formatter,geometry={},context={}){
 if(!globalThis.echarts||!rows.length)return false;
 return mount(chart,()=>trendOption(chart.id,rows,context),rows,formatter,geometry);
}
export function mountScatter(chart,rows,formatter,context={}){
 if(!globalThis.echarts||!rows.length)return false;
 return mount(chart,()=>{
  const t=palette(),event=chart.id==='event-ci-chart';
  const base={animation:false,textStyle:{fontFamily:'Pretendard, sans-serif',color:t.text},grid:{left:12,right:18,top:35,bottom:32,containLabel:true},tooltip:{trigger:'item',confine:true,backgroundColor:t.surface,borderColor:t.grid,textStyle:{color:t.text,fontSize:12},formatter:p=>formatter(rows[p.data?.rowIndex??p.dataIndex])},series:[]};
  const axis={type:'value',axisLabel:{color:t.muted,fontSize:11},splitLine:{lineStyle:{color:t.grid,type:'dashed'}},splitNumber:4};
  if(event){
   base.xAxis={...axis,axisLabel:{...axis.axisLabel,formatter:pct}};
   base.yAxis={type:'category',inverse:true,data:rows.map(r=>`${r.state.includes('greed')?'탐욕':'공포'} · ${r.horizon}일\nn=${r.eventCount}`),axisLabel:{color:t.muted,fontSize:11,lineHeight:16},axisTick:{show:false},axisLine:{show:false}};
   base.series=[{name:'95% CI',type:'custom',data:rows.map((r,i)=>({value:[r.meanCi95?.[0],r.meanCi95?.[1],i],rowIndex:i})),encode:{x:[0,1],y:2},renderItem:(_,api)=>{const a=api.coord([api.value(0),api.value(2)]),b=api.coord([api.value(1),api.value(2)]);if(!a.every(Number.isFinite)||!b.every(Number.isFinite))return null;return {type:'group',children:[{type:'line',shape:{x1:a[0],y1:a[1],x2:b[0],y2:b[1]},style:{stroke:t.muted,lineWidth:1.5}},...[a,b].map(p=>({type:'line',shape:{x1:p[0],y1:p[1]-5,x2:p[0],y2:p[1]+5},style:{stroke:t.muted,lineWidth:1.5}}))]};}}, {name:'평균',type:'scatter',symbolSize:10,data:rows.map((r,i)=>({value:[r.mean,i],rowIndex:i,symbol:r.state.includes('greed')?'diamond':'circle',itemStyle:{color:r.state.includes('greed')?COLORS.greed:COLORS.fear}})),markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:t.muted,type:'dashed'},data:[{xAxis:0}]}}];
   for(const [key,name,color]of [['benchmarkMean','벤치마크',COLORS.benchmark],['meanExcessReturn','초과수익',COLORS.long]])if(rows.some(r=>finite(r[key])!=null))base.series.push({name,type:'scatter',symbol:'rect',symbolSize:7,itemStyle:{color},data:rows.flatMap((r,i)=>finite(r[key])!=null?[{value:[r[key],i],rowIndex:i}]:[])});
  }else{
   const raw=context.raw,ys=rows.map(r=>r.y),xs=rows.map(r=>r.return1d),x0=Math.min(...xs),x1=Math.max(...xs),pred=x=>context.alpha+context.beta*x;
   base.grid.bottom=48;base.xAxis={...axis,name:'KOSPI 1일 수익률',nameLocation:'middle',nameGap:30,nameTextStyle:{color:t.muted},axisLabel:{...axis.axisLabel,formatter:pct}};base.yAxis={...axis,name:raw?'개인 순매수 (조원)':'수급 / 거래대금',nameTextStyle:{color:t.muted},axisLabel:{...axis.axisLabel,formatter:raw?number:pct}};
   base.series=[{name:'관측',type:'scatter',symbolSize:6,itemStyle:{color:COLORS.long,opacity:.6},data:rows.map((r,i)=>({value:[r.return1d,r.y],rowIndex:i,symbolSize:r.role==='current'?12:6,itemStyle:r.role==='current'?{color:COLORS.current,borderWidth:2,borderColor:t.text,opacity:1}:undefined}))}];
   if(Number.isFinite(context.alpha)&&Number.isFinite(context.beta))base.series.push({name:'회귀선',type:'line',showSymbol:false,silent:true,lineStyle:{color:t.muted,type:'dashed',width:1.5},data:[[x0,pred(x0)],[x1,pred(x1)]]});
   if(context.bounds){const bounds=context.bounds;
    const lo=Math.min(...ys,pred(x0)+bounds.extremeFearUpper,pred(x1)+bounds.extremeFearUpper),hi=Math.max(...ys,pred(x0)+bounds.extremeGreedLower,pred(x1)+bounds.extremeGreedLower),padding=Math.max((hi-lo)*.06,1e-8),bottom=lo-padding,top=hi+padding;
    base.yAxis.min=bottom;base.yAxis.max=top;
    for(const [key,name,color,edge]of [['extremeFearUpper','극단 공포 영역',COLORS.fear,bottom],['extremeGreedLower','극단 탐욕 영역',COLORS.greed,top]]){
     const vertices=[[x0,edge],[x0,pred(x0)+bounds[key]],[x1,pred(x1)+bounds[key]],[x1,edge]];
     base.series.unshift({name,type:'custom',silent:true,z:0,data:[0],renderItem:(_,api)=>({type:'polygon',shape:{points:vertices.map(p=>api.coord(p))},style:{fill:color,opacity:.12}})});
     base.series.push({name:name+' 경계',type:'line',showSymbol:false,silent:true,lineStyle:{color,type:'dashed',width:1},data:[[x0,pred(x0)+bounds[key]],[x1,pred(x1)+bounds[key]]]});
    }
   }
  }
  return base;
 },rows,formatter);
}
function mount(chart,build,rows,formatter,geometry={}){
 const previousDate=chart._chartItems?.[chart._chartIndex]?.date;
 chart._quantEngine?.dispose();chart._resizeObserver?.disconnect();chart.replaceChildren();chart.classList.add('qe-live-chart');chart.scrollLeft=0;
 const oldTools=chart.parentElement.querySelectorAll(':scope > .qe-live-tools');oldTools.forEach(e=>e.remove());
 const plot=document.createElement('div');plot.className='qe-plot';chart.append(plot);
 const height=chart.id==='history-chart'?680:chart.id==='equity-chart'?570:chart.id==='event-ci-chart'?Math.max(370,rows.length*44+70):330;
 plot.style.height=height+'px';
 const instance=globalThis.echarts.init(plot,null,{renderer:'svg'});chart._quantEngine=instance;instance.setOption(build());
 const tools=document.createElement('div');tools.className='qe-live-tools';
 const reset=document.createElement('button');reset.type='button';reset.textContent='전체 보기';reset.hidden=true;reset.onclick=()=>{instance.dispatchAction({type:'dataZoom',start:0,end:100});reset.hidden=true;};tools.append(reset);
 chart.after(tools);instance.on('datazoom',()=>{const z=instance.getOption().dataZoom?.[0];reset.hidden=!(z&&(z.start>0||z.end<100));});
 const prior=previousDate?rows.findIndex(r=>r.date===previousDate):-1;
 let selected=prior>=0?prior:Math.min(rows.length-1,geometry.initialIndex??rows.length-1),anchor=null;
 const select=(index)=>{selected=Math.max(0,Math.min(rows.length-1,index));chart._chartIndex=selected;chart.setAttribute('aria-valuetext',formatter(rows[selected],selected));geometry.onSelect?.(rows[selected],selected,{committed:true,phase:'select'});instance.dispatchAction({type:'showTip',seriesIndex:0,dataIndex:selected});};
 chart._chartItems=rows;chart._chartIndex=selected;chart._selectIndex=select;chart._clearRange=()=>{anchor=null;geometry.onRangeClear?.();};
 instance.on('click',p=>{if(Number.isInteger(p.dataIndex))select(p.dataIndex);});
 instance.getZr().on('click',e=>{if(chart.id==='scatter-chart'||chart.id==='event-ci-chart')return;const p=instance.convertFromPixel({xAxisIndex:0},e.offsetX);if(Number.isFinite(p))select(Math.round(p));});
 chart.onkeydown=e=>{let n=selected;if(e.key==='ArrowLeft')n--;else if(e.key==='ArrowRight')n++;else if(e.key==='Home')n=0;else if(e.key==='End')n=rows.length-1;else if(e.key==='Escape'){chart._clearRange();instance.dispatchAction({type:'hideTip'});return;}else return;e.preventDefault();if(e.shiftKey&&geometry.onRangeSelect){anchor??=selected;select(n);geometry.onRangeSelect(Math.min(anchor,selected),Math.max(anchor,selected));}else{anchor=null;select(n);}};
 if(geometry.onRangeSelect){const option=document.createElement('details');const summary=document.createElement('summary');summary.textContent='구간 분석';option.append(summary);const from=document.createElement('input'),to=document.createElement('input'),apply=document.createElement('button'),clear=document.createElement('button');from.type=to.type='date';from.setAttribute('aria-label','구간 시작');to.setAttribute('aria-label','구간 종료');from.min=to.min=rows[0].date;from.max=to.max=rows.at(-1).date;from.value=rows[0].date;to.value=rows.at(-1).date;apply.textContent='적용';clear.textContent='해제';apply.onclick=()=>{const a=rows.findIndex(r=>r.date>=from.value),b=rows.findLastIndex(r=>r.date<=to.value);if(a>=0&&b>a)geometry.onRangeSelect(a,b);};clear.onclick=()=>chart._clearRange();option.append(from,to,apply,clear);tools.append(option);}
 const resize=new ResizeObserver(()=>{if(plot.clientWidth>0)instance.resize();});resize.observe(chart);chart._resizeObserver=resize;
 const theme=new MutationObserver(()=>{if(!instance.isDisposed()){const current=instance.getOption(),option=build();if(option.legend&&current.legend?.[0]?.selected)option.legend.selected=current.legend[0].selected;instance.setOption({...option,dataZoom:current.dataZoom},true);}});theme.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
 const dispose=instance.dispose.bind(instance);instance.dispose=()=>{resize.disconnect();theme.disconnect();dispose();};
 chart.dataset.renderer='echarts';geometry.onSelect?.(rows[selected],selected,{committed:true,phase:'initial'});return true;
}
