import test from 'node:test';
import assert from 'node:assert/strict';
import {trendOption} from '../assets/chart-engine.js';
globalThis.document={documentElement:{dataset:{theme:'dark'}}};
test('history uses one date domain with price, actual holdings and unchanged net return values',()=>{
 const rows=[{date:'2026-01-02',kospiClose:100,longCashValue:1,longShortValue:1,longCashReturn:0,longShortReturn:0,buyHoldReturn:0,longCashPosition:'long',longShortPosition:'inverse'}, {date:'2026-01-05',kospiClose:105,longCashValue:1.02,longShortValue:.98,longCashReturn:.02,longShortReturn:-.02,buyHoldReturn:.05,longCashPosition:'long',longShortPosition:'cash'}];
 const before=JSON.stringify(rows),option=trendOption('history-chart',rows);
 assert.equal(option.grid.length,3);assert.ok(option.xAxis.every(axis=>JSON.stringify(axis.data)===JSON.stringify(rows.map(r=>r.date))));
 assert.deepEqual(option.series.find(s=>s.name==='롱/현금').data,[0,.02]);
 assert.equal(option.dataZoom[0].filterMode,'none');assert.equal(JSON.stringify(rows),before);
});
test('residual scale is fixed at 0–100 and unavailable observations stay gaps',()=>{
 const rows=[{date:'2026-01-02',selectedPercentile:20,selectedEligible:true},{date:'2026-01-05',selectedPercentile:null,selectedEligible:false}];
 const option=trendOption('residual-chart',rows,{cut:2});
 assert.equal(option.yAxis[0].min,0);assert.equal(option.yAxis[0].max,100);assert.deepEqual(option.series[0].data,[20,null]);assert.equal(option.series[0].connectNulls,false);
});
test('return and drawdown use distinct panels with the same strategy and benchmark colours',()=>{
 const rows=[{date:'2026-01-02',strategyReturn:0,buyHoldReturn:0,drawdown:0,buyHoldDrawdown:0},{date:'2026-01-05',strategyReturn:.1,buyHoldReturn:.2,drawdown:-.1,buyHoldDrawdown:-.2}];
 const option=trendOption('equity-chart',rows,{primaryLabel:'롱/현금'});
 assert.equal(option.grid.length,2);assert.equal(option.yAxis[1].max,0);
 const strategy=option.series.filter(s=>s.name==='롱/현금');assert.equal(strategy[0].lineStyle.color,strategy[1].lineStyle.color);assert.deepEqual(strategy[1].data,[0,-.1]);
});
