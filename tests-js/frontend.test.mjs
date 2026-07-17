import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const read=path=>readFile(new URL(`../${path}`,import.meta.url),'utf8');

test('dashboard exposes all precomputed research selectors with pressed state',async()=>{
  const html=await read('index.html');
  for(const token of [
    'data-model="robust"','data-model="scaled"','data-model="raw"',
    'data-event-asset="KOSPI"','data-event-asset="226490"','data-event-asset="069500"',
    'data-event-sample="all"','data-event-sample="nonOverlapping20d"',
    'data-backtest-policy="compare"','data-backtest-policy="long_cash"','data-backtest-policy="long_short_cash"',
    'data-backtest-proxy="226490"','data-backtest-proxy="069500"',
    'data-backtest-variant="scaled_huber"','data-backtest-variant="scaled_ols"','data-backtest-variant="raw_ols"','data-backtest-variant="disparity"',
    'data-backtest-cost="0"','data-backtest-cost="5"','data-backtest-cost="10"','data-backtest-cost="20"',
    'data-backtest-period="full"','data-backtest-period="common"'
  ]) assert.match(html,new RegExp(token));
  assert.ok((html.match(/aria-pressed=/g)||[]).length>=21);
  assert.match(html,/현재값·해석 브리지·산점도/);
});

test('first-party context and every public operational artifact are linked',async()=>{
  const html=await read('index.html');
  assert.match(html,/assets\/favicon\.svg/);
  assert.match(html,/https:\/\/sonchanggi\.github\.io\/quant-dashboard\//);
  assert.match(html,/https:\/\/github\.com\/SonChangGi\/fearNgreed\/actions/);
  for(const path of ['data/summary.json','data/dashboard.json','data/history.json','data/strategy-comparison.json','data/automation-status.json']) assert.match(html,new RegExp(path.replace('.','\\.')));
});

test('strategy controls expose a bounded dynamic exit input and a symmetric short threshold',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="exit-threshold-form"/);
  assert.match(html,/id="exit-threshold-input"[^>]+type="number"[^>]+min="50"[^>]+max="94"[^>]+step="1"[^>]+value="80"/);
  assert.match(html,/id="exit-threshold-status"[^>]+aria-live="polite"/);
  assert.match(html,/롱은 입력값 이상, 숏은 대칭값 이하/);
  assert.match(app,/DEFAULT_LONG_EXIT_PERCENTILE/);
  assert.match(app,/normalizeLongExitPercentile/);
  assert.match(app,/short-exit-threshold-value/);
  assert.match(app,/100 - store\.longExitPercentile/);
  assert.match(app,/runStrategyScenario/);
  assert.match(app,/browser_user_scenario/);
});

test('position policy comparison discloses short-model limitations and side-aware output',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="policy-comparison-card"/);
  assert.match(html,/id="strategy-exposure"/);
  assert.match(html,/id="exit-sensitivity"/);
  assert.match(html,/id="short-method-warning"/);
  assert.match(html,/대차 가능 수량·대차료·리콜·증거금/);
  assert.match(html,/롱 \/ 숏 \/ 현금/);
  assert.match(app,/longExposure/);
  assert.match(app,/shortExposure/);
  assert.match(app,/cashExposure/);
  assert.match(app,/trade\.side/);
  assert.match(app,/이격도 변형에는 숏 규칙이 정의되지 않았습니다/);
});

test('compare mode names both policy results in the key cards and conclusion',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/store\.backtestPolicy === "compare" && longCash\?\.metrics && longShort\?\.metrics/);
  assert.match(app,/metric\("롱 \/ 현금 총수익률", fmt\.pct\(longCash\.metrics\.totalReturn\)/);
  assert.match(app,/metric\("롱 \/ 숏 \/ 현금 총수익률", fmt\.pct\(longShort\.metrics\.totalReturn\)/);
  assert.match(app,/const conclusionLongCash = store\.backtestPolicy === "compare" \? longCashResultFor\(\) : null/);
  assert.match(app,/const conclusionLongShort = store\.backtestPolicy === "compare" \? longShortResultFor\(\) : null/);
  assert.match(app,/롱\/현금 \$\{fmt\.signedPct\(conclusionLongCash\.metrics\.totalReturn\)\}/);
  assert.match(app,/롱\/숏\/현금 \$\{fmt\.signedPct\(conclusionLongShort\.metrics\.totalReturn\)\}/);
});

test('dynamic exit submit validates loaded data and scenario success before announcing apply',async()=>{
  const app=await read('assets/app.js');
  const start=app.indexOf('$("#exit-threshold-form").addEventListener("submit"');
  const end=app.indexOf('$("#reset-controls").addEventListener',start);
  assert.ok(start>=0 && end>start);
  const submit=app.slice(start,end);
  const dataGuard=submit.indexOf('if (!store.dashboard || !store.history || !store.strategyComparison)');
  const errorGuard=submit.indexOf('if (latestScenarioError) throw latestScenarioError');
  const resultGuard=submit.indexOf('if (scenarioResults.length !== expectedCount)');
  const success=submit.indexOf('status.dataset.state = "ok"');
  assert.ok(dataGuard>=0 && errorGuard>dataGuard && resultGuard>errorGuard && success>resultGuard);
  assert.match(submit,/const previous = store\.longExitPercentile/);
  assert.match(submit,/store\.longExitPercentile = previous/);
  assert.match(submit,/status\.dataset\.state = "error"/);
});

test('single-policy equity legend has stable ids and policy-aware visibility toggles',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="equity-legend-long-cash"/);
  assert.match(html,/id="equity-legend-long-short"/);
  assert.match(app,/\$\("#equity-legend-long-cash"\)\.hidden = store\.backtestPolicy === "long_short_cash"/);
  assert.match(app,/\$\("#equity-legend-long-short"\)\.hidden = store\.backtestPolicy === "long_cash"/);
});

test('stylesheet and module cache versions advance together',async()=>{
  const html=await read('index.html');
  const cssVersion=html.match(/assets\/styles\.css\?v=([^"']+)/)?.[1];
  const jsVersion=html.match(/assets\/app\.js\?v=([^"']+)/)?.[1];
  assert.ok(cssVersion);
  assert.equal(jsVersion,cssVersion);
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
  assert.match(app,/metrics\.riskMatchedBuyHoldReturn/);
  assert.match(app,/eventExcess\(fear20\)/);
  assert.doesNotMatch(html,/387\.25|4\.57|1\.61|0\.18/);
});

test('performance lookup includes return, win rate, annualized position changes, holding time and benchmark risk',async()=>{
  const html=await read('index.html');
  for(const label of ['총수익률','승률','연환산 매매측수','평균 보유','BH MDD','동일 타이밍 0bp','위험 일치 BH']) assert.match(html,new RegExp(label));
  const app=await read('assets/app.js');
  for(const field of ['totalReturn','winRate','turnover','averageHoldingSessions','buyAndHoldMaxDrawdown','exposureMatchedReturn','riskMatchedBuyHoldReturn','costBreakEvenBps']) assert.match(app,new RegExp(field));
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
  assert.match(app,/\^fear-flow-v\\d\+\$/);
  assert.match(app,/dashboard\?\.methodologyVersion !== methodology/);
  assert.match(app,/dashboard\?\.dataAsOf !== summary\.dataAsOf/);
  assert.match(app,/!models\.scaled \|\| !models\.raw/);
});

test('MU Hynix relative spread keeps its published index-point unit',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/상대 스프레드 \(지수포인트\)/);
  assert.match(app,/muHynixRelativeSpread == null \? "—" : `\$\{fmt\.score/);
  assert.doesNotMatch(app,/fmt\.pct\(latest\.muHynixRelativeSpread\)/);
});

test('source replica and practical signal are separate, scoped research tracks',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/실전 신호 · 강건 회귀/);
  assert.match(html,/PDF 원문 근사 · 절대 수급/);
  for(const id of ['history-model-scope','scatter-model-scope','residual-model-scope','event-model-scope','strategy-model-scope']) assert.match(html,new RegExp(`id="${id}"`));
  assert.match(app,/function primaryModelKind/);
  assert.match(app,/eventsByModel\?\.\[store\.model\]/);
  assert.match(html,/SELECTED RESEARCH TRACK · EVENT STUDY/);
  assert.match(html,/선택 연구 트랙의 사전 계산 사건 표본/);
  assert.match(app,/사건: \$\{esc\(store\.eventAsset\)\} \$\{esc\(compactModelName\(eventModelKind\(\)\)\)\}/);
});

test('mobile jump buttons leave the fixed overlay layer and light muted text keeps contrast',async()=>{
  const css=await read('assets/styles.css');
  assert.match(css,/--muted:\s*#687482/);
  assert.match(css,/@media \(max-width: 520px\)[\s\S]*?\.page-jump-nav\s*\{[^}]*position:\s*static/s);
});

test('signal bridge exposes actual expected residual percentile and state without hiding the formula',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="signal-bridge"/);
  for(const label of ['실제 수급','회귀 예상','잔차','과거 백분위','연구 상태']) assert.match(app,new RegExp(label));
  assert.match(app,/실제 − 예상/);
});

test('operational and research-signal badges cannot share the same label',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="status-badge"/);
  assert.match(html,/id="signal-badge"/);
  assert.match(app,/badge\.textContent = `데이터/);
  assert.match(app,/signal-badge/);
  assert.doesNotMatch(app,/summary\.status\.label \|\| qualityLabel/);
});

test('public operational reason codes are rendered as readable Korean guidance',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(app,/core_latest_common_date_alignment:\s*"공급자 최신일 차이로 공통 거래일까지 계산"/);
  assert.match(app,/adjusted_history_gap_reconciled_/);
  assert.match(app,/조정가격 누락일을 공식 KRX 세션으로 검증·보정/);
  assert.match(app,/reasons\.map\(degradedReasonLabel\)/);
  assert.doesNotMatch(html,/core_latest_common_date_alignment/);
});

test('ETF adjusted-history reconciliation is visible with provider provenance and counts',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/yfinance_adjusted_plus_scaled_krx_gap_rows/);
  assert.match(app,/yfinance 조정가 \+ KRX 검증 보정행/);
  assert.match(app,/historyReconciliation/);
  assert.match(app,/report\.filledCount/);
  assert.match(app,/report\.unresolvedCount/);
});

test('uncertainty chart, ETF common-period comparison and PDF snapshot fail closed on old payloads',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="event-ci-chart"/);
  assert.match(html,/id="proxy-comparison"/);
  assert.match(html,/id="pdf-snapshot"/);
  assert.match(app,/eventBenchmark/);
  assert.match(app,/renderProxyComparison/);
  assert.match(app,/PDF 주석 사건 파생값이 아직 공개 계약에 없습니다/);
});

test('event excess confidence intervals disclose how benchmark uncertainty is treated',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/meanExcessReturnCi95BenchmarkTreatment/);
  assert.match(app,/fixed_external_mean/);
  assert.match(app,/벤치마크 평균의 추정오차는 포함하지 않습니다/);
  assert.match(app,/paired_event_returns/);
});

test('controls persist to URL and localStorage and charts expose an explicit latest action',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="reset-controls"/);
  assert.match(html,/id="share-view"/);
  assert.ok((html.match(/data-chart-latest=/g)||[]).length>=4);
  assert.match(app,/localStorage\.setItem\("fearngreed-controls-v3"/);
  assert.match(app,/getItem\("fearngreed-controls-v3"\) \|\| localStorage\.getItem\("fearngreed-controls-v2"\)/);
  assert.match(app,/history\.replaceState/);
  assert.match(app,/navigator\.clipboard\.writeText/);
  assert.match(app,/longExitPercentile:\s*"exit"/);
  assert.match(app,/backtestPolicy:\s*"policy"/);
  assert.match(app,/url\.searchParams\.set\(param, store\[key\]\)/);
});

test('frontend validates the separate strategy-comparison contract before rendering',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/validateContracts\(summary, dashboard, history, strategyComparison\)/);
  assert.match(app,/strategyComparison\?\.contract !== "fearngreed-strategy-comparison"/);
  assert.match(app,/strategyComparison\?\.dataAsOf !== summary\.dataAsOf/);
  assert.match(app,/summary\?\.payload\?\.strategyComparisonUrl !== "\.\/strategy-comparison\.json"/);
  assert.match(app,/loadJson\("data\/strategy-comparison\.json"\)/);
});

test('KOSPI history supports calendar presets and validated shareable custom dates',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  for(const value of ['1m','3m','6m','ytd','1y','3y','all']) assert.match(html,new RegExp(`data-window="${value}"`));
  for(const id of ['history-range-form','history-start','history-end','history-range-status','history-exposure-note']) assert.match(html,new RegExp(`id="${id}"`));
  assert.match(app,/function applyCustomHistoryRange/);
  assert.match(app,/start > end/);
  assert.match(app,/start < firstDate \|\| end > latestDate/);
  assert.match(app,/store\.window = "custom"/);
  assert.match(app,/historyStart:\s*"start"/);
  assert.match(app,/historyEnd:\s*"end"/);
  assert.match(app,/url\.searchParams\.delete\(param\)/);
  assert.match(app,/\{ "252": "1y", "756": "3y" \}/);
  assert.match(app,/\(store\.history\?\.series \|\| \[\]\)\.slice\(-756\)/);
  assert.match(css,/\.history-custom-range/);
  assert.match(css,/\.history-range-status\[data-state="error"\]/);
});

test('history distinguishes state observations from actual entries and computes exposure on available positions only',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  assert.match(html,/공포 원은 상태 관측이지 모두 매수 주문은 아닙니다/);
  assert.match(html,/실제 롱 진입/);
  assert.match(app,/\["long", "cash"\]\.includes\(row\.position\)/);
  assert.match(app,/포지션 산출 전\/불가/);
  assert.match(app,/class="entry-long"/);
  assert.match(css,/\.entry-long/);
});

test('scatter renders only published latest-fit empirical state boundaries and fails closed without them',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  assert.match(html,/극단적 공포 영역/);
  assert.match(html,/극단적 탐욕 영역/);
  assert.match(app,/scatterMetaByModel\?\.\[store\.model\]\?\.stateBoundaries/);
  assert.match(app,/empirical_cdf_transition_order_statistic/);
  assert.match(app,/current_fit_on_prior_window/);
  assert.match(app,/typeof value !== "number"/);
  for(const field of ['extremeFearUpper','fearUpper','greedLower','extremeGreedLower']) assert.match(app,new RegExp(field));
  assert.match(app,/clipPath id="scatter-plot-clip"/);
  assert.match(app,/브라우저 재추정 없음/);
  assert.doesNotMatch(app,/function empiricalExtremeResidualCutoffs/);
  assert.match(app,/당시 롤링 상태/);
  assert.match(css,/\.scatter-zone-extreme-fear/);
  assert.match(css,/\.scatter-zone-extreme-greed/);
});

test('frontend decodes compact columnar history and scatter pointer uses nearest XY distance',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/function decodeHistory/);
  assert.match(app,/seriesColumns/);
  assert.match(app,/seriesRows/);
  assert.match(app,/function attachScatterNavigation/);
  assert.match(app,/\(item\.plotX - pointerX\) \*\* 2 \+ \(item\.plotY - pointerY\) \*\* 2/);
  assert.match(app,/scatterByModel\?\.\[store\.model\]/);
});

test('normalized benchmark equity and future flow channels remain explicit and fail closed',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="flow-channels"/);
  assert.match(html,/외국인·기관 수급/);
  assert.match(app,/commonBenchmarkEquity/);
  assert.match(app,/function hydratedEquityRows/);
  assert.match(app,/flowChannels\?\.channels/);
  assert.match(app,/future_extension/);
  assert.match(app,/primary \? "1차 신호" : "진단 가능"/);
  assert.match(app,/낮음 · 거래 미사용/);
  assert.match(app,/collecting \? "수집 중"/);
  assert.match(app,/collecting \? "표본 부족"/);
  assert.match(html,/외국인·기관 카드에 수치가 보여도 진단 결과/);
  assert.match(app,/거래 상세 행은 경량 공개 계약에서 생략되었습니다/);
});
