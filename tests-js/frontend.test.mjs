import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const read=path=>readFile(new URL(`../${path}`,import.meta.url),'utf8');

test('dashboard exposes all precomputed research selectors with pressed state',async()=>{
  const html=await read('index.html');
  for(const token of [
    'data-model="scaled"','data-model="raw"',
    'data-event-asset="KOSPI"','data-event-asset="226490"','data-event-asset="069500"',
    'data-event-sample="all"','data-event-sample="nonOverlapping20d"',
    'data-backtest-proxy="226490"','data-backtest-proxy="069500"',
    'data-backtest-variant="base"','data-backtest-variant="disparity"',
    'data-backtest-cost="5"','data-backtest-cost="10"','data-backtest-cost="20"',
    'data-backtest-period="full"','data-backtest-period="common"'
  ]) assert.match(html,new RegExp(token));
  assert.ok((html.match(/aria-pressed=/g)||[]).length>=18);
  assert.match(html,/브라우저에서 재계산하지 않습니다/);
});

test('first-party context and every public operational artifact are linked',async()=>{
  const html=await read('index.html');
  assert.match(html,/assets\/favicon\.svg/);
  assert.match(html,/https:\/\/sonchanggi\.github\.io\/quant-dashboard\//);
  assert.match(html,/https:\/\/github\.com\/SonChangGi\/fearNgreed\/actions/);
  for(const path of ['data/summary.json','data/dashboard.json','data/history.json','data/automation-status.json']) assert.match(html,new RegExp(path.replace('.','\\.')));
});

test('charts provide keyboard instructions and adjacent exact-value alternatives',async()=>{
  const html=await read('index.html');
  const app=await read('assets/app.js');
  for(const id of ['history','scatter','residual','equity','diagnostic']) {
    assert.match(html,new RegExp(`id="${id}-chart"[^>]+tabindex="0"`));
    assert.match(html,new RegExp(`id="${id}-data-table"`));
  }
  assert.match(app,/\["ArrowLeft", "ArrowRight", "Home", "End"\]/);
  assert.match(app,/aria-valuetext/);
  assert.match(app,/attachChartNavigation/);
});

test('evidence conclusion is computed from published confidence intervals and benchmark returns',async()=>{
  const html=await read('index.html');
  const app=await read('assets/app.js');
  assert.match(html,/id="research-conclusion"/);
  assert.match(app,/fear20\.meanCi95/);
  assert.match(app,/metrics\.totalReturn/);
  assert.match(app,/metrics\.buyAndHoldReturn/);
  assert.doesNotMatch(html,/387\.25|4\.57|1\.61|0\.18/);
});

test('performance lookup includes return, win rate, turnover, holding time and benchmark risk',async()=>{
  const html=await read('index.html');
  for(const label of ['총수익률','승률','회전율','평균 보유','BH MDD']) assert.match(html,new RegExp(label));
  const app=await read('assets/app.js');
  for(const field of ['totalReturn','winRate','turnover','averageHoldingSessions','buyAndHoldMaxDrawdown']) assert.match(app,new RegExp(field));
});

test('mobile navigation remains available and chart encodings are not color-only',async()=>{
  const [html,css]=await Promise.all([read('index.html'),read('assets/styles.css')]);
  assert.match(html,/class="quant-nav-link is-active"[^>]+aria-current="page"/);
  assert.match(css,/\.quant-nav-scroll[^}]*overflow-x:\s*auto/s);
  assert.match(css,/\.legend-dot\.greed[^}]*rotate\(45deg\)/s);
  assert.match(css,/\.line-buyhold[^}]*stroke-dasharray/s);
  assert.match(html,/aria-roledescription="대화형/);
});

test('shared shell, jump navigation, pointer charts and table tools match the quant family',async()=>{
  const [html,css,app]=await Promise.all([read('index.html'),read('assets/styles.css'),read('assets/app.js')]);
  assert.match(html,/class="quant-common-nav"/);
  assert.match(html,/class="page-jump-nav"/);
  assert.match(html,/href="#top" aria-label="맨 위로 이동"/);
  assert.match(html,/href="#page-bottom" aria-label="맨 아래로 이동"/);
  assert.match(css,/--primary:\s*#3182f6/);
  assert.match(app,/chart\.onpointermove/);
  assert.match(app,/chart-crosshair/);
  assert.match(app,/function sortTable/);
  assert.match(app,/setAttribute\("aria-sort"/);
  assert.match(app,/function applyTableFilter/);
  assert.match(app,/function exportTable/);
});

test('initial theme and every segmented control are synchronized through aria-pressed',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/function setTheme/);
  assert.match(app,/setAttribute\("aria-pressed", String\(value === "dark"\)\)/);
  assert.match(app,/function updatePressed/);
  assert.match(app,/button\.setAttribute\("aria-pressed", String\(active\)\)/);
});

test('frontend rejects mismatched methodology and data dates instead of rendering a proxy',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/methodologyVersion !== "fear-flow-v1"/);
  assert.match(app,/dashboard\?\.dataAsOf !== summary\.dataAsOf/);
  assert.match(app,/summary\.primaryEntities\[0\]\?\.models\?\.scaled/);
});

test('MU Hynix relative spread keeps its published index-point unit',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/상대 스프레드 \(지수포인트\)/);
  assert.match(app,/muHynixRelativeSpread == null \? "—" : `\$\{fmt\.score/);
  assert.doesNotMatch(app,/fmt\.pct\(latest\.muHynixRelativeSpread\)/);
});
