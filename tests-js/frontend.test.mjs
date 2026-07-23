import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const read=path=>readFile(new URL(`../${path}`,import.meta.url),'utf8');

test('dashboard exposes one unified scenario control surface with pressed state',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  for(const token of [
    'data-model="robust"','data-model="scaled"','data-model="raw"',
    'data-event-asset="KOSPI"','data-event-asset="226490"','data-event-asset="069500"',
    'data-event-sample="all"','data-event-sample="nonOverlapping20d"',
    'data-backtest-policy="compare"','data-backtest-policy="long_cash"','data-backtest-policy="long_inverse_cash"',
    'data-backtest-pair="1x"','data-backtest-pair="2x"',
    'data-backtest-cost="0"','data-backtest-cost="5"','data-backtest-cost="10"','data-backtest-cost="20"',
    'data-backtest-period="full"','data-backtest-period="common"'
  ]) assert.match(html,new RegExp(token));
  assert.ok((html.match(/aria-pressed=/g)||[]).length>=17);
  assert.match(html,/<details id="analysis-settings" class="analysis-config"/);
  assert.match(html,/id="analysis-config-summary"/);
  assert.ok(html.indexOf('id="conclusion"') < html.indexOf('id="analysis-settings"'),"results must precede detailed settings");
  assert.ok(html.indexOf('aria-label="핵심 차트"') < html.indexOf('id="analysis-settings"'),"core charts must precede detailed settings");
  for(const id of ['signal-settings-form','signal-lookback-input','signal-min-r2-input','signal-tail-input','signal-max-holding-input','signal-settings-status']) assert.match(html,new RegExp(`id="${id}"`));
  assert.match(html,/id="linked-strategy-rule"/);
  assert.match(app,/robust: "scaled_huber"/);
  assert.match(app,/scaled: "scaled_ols"/);
  assert.match(app,/raw: "raw_ols"/);
});

test('first-party context and every public operational artifact are linked',async()=>{
  const html=await read('index.html');
  assert.match(html,/assets\/favicon\.svg/);
  assert.match(html,/https:\/\/sonchanggi\.github\.io\/quant-dashboard\//);
  assert.match(html,/https:\/\/github\.com\/SonChangGi\/fearNgreed\/actions/);
  for(const path of ['data/summary.json','data/dashboard.json','data/history.json','data/strategy-comparison.json','data/automation-status.json']) assert.match(html,new RegExp(path.replace('.','\\.')));
});

test('strategy controls expose a bounded dynamic exit input and a symmetric inverse threshold',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="exit-threshold-form"/);
  assert.match(html,/id="exit-threshold-input"[^>]+type="number"[^>]+min="50"[^>]+max="94"[^>]+step="1"[^>]+value="80"/);
  assert.match(html,/id="exit-threshold-status"[^>]+aria-live="polite"/);
  assert.match(html,/롱은 입력값 이상, 인버스는 대칭값 이하/);
  assert.match(html,/50 미만은 공포에서 중립 이상으로 회복했다는 의미가 사라지고/);
  assert.match(html,/95 이상은 사전 정의한 극단 탐욕·ETF 교체 구간과 겹칩니다/);
  assert.match(app,/DEFAULT_LONG_EXIT_PERCENTILE/);
  assert.match(app,/normalizeLongExitPercentile/);
  assert.match(app,/inverse-exit-threshold-value/);
  assert.match(app,/100 - store\.longExitPercentile/);
  assert.match(app,/runActualEtfPairScenario/);
  assert.match(app,/pairId: proxy/);
});

test('position policy comparison discloses actual inverse execution and side-aware output',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="policy-comparison-card"/);
  assert.match(html,/id="strategy-exposure"/);
  assert.match(html,/id="exit-sensitivity"/);
  assert.match(html,/id="actual-etf-method-note"/);
  assert.match(html,/인버스도 현물 ETF 매수로 계산합니다/);
  assert.match(html,/롱 \/ 인버스 \/ 현금/);
  assert.match(html,/2X는 일간 목표 배율이므로 누적 경로는 1X와 달라질 수 있습니다/);
  for(const ticker of ['069500','114800','122630','252670']) assert.match(html,new RegExp(ticker));
  assert.match(app,/ACTUAL_ETF_PAIRS/);
  assert.match(app,/function heldInstrument/);
  assert.match(app,/longExposure/);
  assert.match(app,/inverseExposure/);
  assert.match(app,/cashExposure/);
  assert.match(app,/trade\.side/);
  assert.match(app,/entry_signal_date/);
  assert.match(app,/exit_signal_date/);
  assert.match(app,/normalizedActionLabel/);
  for(const id of ['open-trade-card','open-trade-title','open-trade-subtitle','open-trades']) assert.match(html,new RegExp(`id="${id}"`));
  for(const field of ['entrySignalDate','entryDate','entryPrice','holdingSessions','unrealizedReturn','pendingAction']) assert.match(app,new RegExp(field));
  for(const label of ['진입 신호일 · 종가','진입 체결일 · 시가','진입 조정시가','보유 거래일','평가 손익 · 미실현','다음 예정 행동']) assert.match(app,new RegExp(label));
});

test('compare mode names both policy results in the key cards and conclusion',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/store\.backtestPolicy === "compare" && longCash\?\.metrics && longInverse\?\.metrics/);
  assert.match(app,/metric\("롱 \/ 현금 총수익률", fmt\.pct\(longCash\.metrics\.totalReturn\)/);
  assert.match(app,/metric\("롱 \/ 인버스 \/ 현금 총수익률", fmt\.pct\(longInverse\.metrics\.totalReturn\)/);
  assert.match(app,/const conclusionLongCash = store\.backtestPolicy === "compare" \? scenarioBundle\.longCash : null/);
  assert.match(app,/const conclusionLongInverse = store\.backtestPolicy === "compare" \? scenarioBundle\.longInverse : null/);
  assert.match(app,/롱\/현금 \$\{fmt\.signedPct\(conclusionLongCash\.metrics\.totalReturn\)\}/);
  assert.match(app,/롱\/인버스\/현금 \$\{fmt\.signedPct\(conclusionLongInverse\.metrics\.totalReturn\)\}/);
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
  assert.match(submit,/const snapshot = captureResearchSnapshot\(\)/);
  assert.match(submit,/restoreResearchSnapshot\(snapshot\)/);
  assert.ok(submit.indexOf('renderAll();') < submit.indexOf('persistControlState();'));
  assert.match(submit,/status\.dataset\.state = "error"/);
});

test('single-policy equity legend has stable ids and policy-aware visibility toggles',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="equity-legend-long-cash"/);
  assert.match(html,/id="equity-legend-long-inverse"/);
  assert.match(app,/\$\("#equity-legend-long-cash"\)\.hidden = store\.backtestPolicy === "long_inverse_cash"/);
  assert.match(app,/\$\("#equity-legend-long-inverse"\)\.hidden = store\.backtestPolicy === "long_cash"/);
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
  for(const field of ['totalReturn','winRate','turnover','averageHoldingSessions','buyAndHoldMaxDrawdown','exposureMatchedReturn','riskMatchedBuyHoldReturn','inverseExposure']) assert.match(app,new RegExp(field));
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

test('shared shell uses the canonical project order and common theme storage contract',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  const nav=html.match(/<div class="quant-nav-scroll"[\s\S]*?<\/div>/)?.[0]||'';
  const labels=[...nav.matchAll(/class="quant-nav-link(?: is-active)?"[^>]*>([^<]+)<\/a>/g)].map((match)=>match[1]);
  assert.deepEqual(labels,['Hub','Fear &amp; Greed','Momentum','DRAM','Best Factor','ETF','SOX','Risk Score','Port','Valuation','Kelly']);
  assert.match(html,/https:\/\/sonchanggi\.github\.io\/kelly\//);
  for(const key of ['quant-research-theme','quant-calm-theme','quant-dashboard-theme','dram-price-theme']) {
    assert.match(html,new RegExp(key));
    assert.match(app,new RegExp(key));
  }
  assert.match(app,/localStorage\.setItem\(THEME_STORAGE_KEY, theme\)/);
  assert.match(app,/LEGACY_THEME_STORAGE_KEYS\.forEach\(\(key\) => localStorage\.removeItem\(key\)\)/);
  assert.match(app,/function saveTheme\(theme\)/);
  assert.match(app,/setTheme\(requested \|\| saved \|\| systemTheme\(\) \|\| "light"\)/);
  assert.doesNotMatch(app,/localStorage\.setItem\("(?:quant-(?:calm|dashboard)-theme|dram-price-theme)"/);
  assert.doesNotMatch(app,/document\.documentElement\.dataset\.theme \|\| preferred/);
});

test('common design controls and decision-critical chart metadata keep practical size floors',async()=>{
  const css=await read('assets/styles.css');
  const touchStart=css.indexOf('/* Common design v1: primary controls');
  const typeStart=css.indexOf('/* Interactive copy and decision-critical');
  assert.ok(touchStart>=0 && typeStart>touchStart);
  const touchBlock=css.slice(touchStart,typeStart);
  for(const selector of ['.quant-nav-link','.local-nav a','.segmented button','.signal-learning-grid input','.history-chart-callout input','.history-custom-range button','.table-toolbar button','.table-sort']) {
    assert.match(touchBlock,new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  }
  assert.match(touchBlock,/min-height:\s*44px/);
  assert.match(touchBlock,/min-width:\s*44px/);
  const typeBlock=css.slice(typeStart,css.indexOf('@media (prefers-reduced-motion',typeStart));
  for(const selector of ['.phase-badge','.signal-settings-status','.history-series-controls button','.chart-meta-strip b','.history-chart-callout dd','thead th']) {
    assert.match(typeBlock,new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  }
  assert.match(typeBlock,/font-size:\s*\.75rem/);
  assert.match(typeBlock,/\.line-end-label,[\s\S]*?font-size:\s*12px/);
});

test('common design v1.2 keeps compact typography and one closed operations surface',async()=>{
  const [html,css,app]=await Promise.all([read('index.html'),read('assets/styles.css'),read('assets/app.js')]);
  assert.match(css,/body\s*\{[\s\S]*?font-size:\s*15px;[\s\S]*?line-height:\s*1\.55;/);
  assert.match(css,/\.hero h1\s*\{[^}]*font-size:\s*clamp\(2rem,\s*4vw,\s*3\.25rem\)/);
  assert.match(css,/\.section-head h2\s*\{[^}]*font-size:\s*clamp\(1\.35rem,\s*2\.3vw,\s*1\.8rem\)/);
  assert.doesNotMatch(css,/font-weight:\s*(?:8\d\d|9\d\d)/);
  assert.match(css,/\.quant-nav-link\.is-active[^}]*color:\s*var\(--primary-strong\)[^}]*background:\s*var\(--primary-soft\)/);
  assert.equal((html.match(/id="status-detail-summary"/g)||[]).length,1);
  assert.match(html,/<details class="card research-details" id="method">[\s\S]*?id="status-detail-summary">데이터 · 출처 · 운영 상세/);
  assert.ok(html.indexOf('id="quality-strip"') > html.indexOf('id="method"'));
  assert.ok(html.indexOf('id="flow-channels"') > html.indexOf('id="method"'));
  for(const phrase of ['전체 분석 다시 계산','차트 선택은 평가 종료일 성과를 바꾸지 않습니다','현재 전략 신호에는 개인 수급 채널이 반영됩니다','정책 외의 모든 입력은 동일하게 유지됩니다']) assert.doesNotMatch(html,new RegExp(phrase));
  for(const phrase of ['브라우저 과거전용 사용자 시나리오','다시 계산하는 중입니다','경량 공개 계약']) assert.doesNotMatch(app,new RegExp(phrase));
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

test('frontend freshness precedence executes server flags and the automation watchdog',async()=>{
  const app=await read('assets/app.js');
  const status=app.slice(app.indexOf('function effectiveStatus'),app.indexOf('function entity'));
  const effectiveStatus=Function(`${status}; return effectiveStatus;`)();
  const recentSuccess=new Date(Date.now()-86_400_000).toISOString();
  const oldSuccess=new Date(Date.now()-5*86_400_000).toISOString();
  const base={
    dataAsOf:'2020-01-02',
    status:{state:'degraded',expectedFreshnessDays:3},
    automation:{lastSuccessAt:recentSuccess}
  };

  assert.equal(effectiveStatus(base),'degraded','lastSuccessAt must protect a legacy payload from a calendar-holiday false stale');
  assert.equal(effectiveStatus({...base,automation:{lastSuccessAt:oldSuccess}}),'stale','stopped automation must become stale even when data and expected dates were once equal');
  assert.equal(effectiveStatus({...base,status:{...base.status,freshnessBasis:'official_krx_latest_completed_session',expectedDataAsOf:'2020-01-02',sourceFreshnessPassed:false}}),'stale');
  assert.equal(effectiveStatus({...base,status:{...base.status,freshnessBasis:'official_krx_latest_completed_session',expectedDataAsOf:'2020-01-03',sourceFreshnessPassed:true}}),'stale');
  assert.equal(effectiveStatus({...base,status:{...base.status,freshnessBasis:'source_alignment_only',expectedDataAsOf:null,sourceFreshnessPassed:false}}),'degraded');
  assert.equal(effectiveStatus({...base,status:{...base.status,state:'unavailable',expectedDataAsOf:'2020-01-03',sourceFreshnessPassed:false}}),'unavailable');
});

test('frontend keeps a compact expected-session warning with operational detail',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/· 기대 \$\{expectedDataAsOf\}/);
  assert.match(app,/공식 최신 완료 세션/);
  assert.match(app,/자동 갱신 마지막 성공/);
});

test('frontend explains split KRX credential failures without exposing values',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/krx_open_api_key_missing: "KRX Open API 키 미설정"/);
  assert.match(app,/krx_login_credentials_missing: "KRX 로그인 인증정보 미설정"/);
});

test('MU Hynix relative spread keeps its published index-point unit',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/상대 스프레드 \(지수포인트\)/);
  assert.match(app,/muHynixRelativeSpread == null \? "—" : `\$\{fmt\.score/);
  assert.doesNotMatch(app,/fmt\.pct\(latest\.muHynixRelativeSpread\)/);
});

test('absolute-flow and scale-adjusted signals are selectable tracks inside one dynamic scenario',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/실전 신호 · 강건 회귀/);
  assert.match(html,/절대수급 OLS · 규모 미보정/);
  for(const id of ['history-model-scope','scatter-model-scope','residual-model-scope','event-model-scope','strategy-model-scope']) assert.match(html,new RegExp(`id="${id}"`));
  assert.match(app,/function primaryModelKind/);
  assert.match(app,/runDynamicEventStudy/);
  assert.match(app,/function eventModelKind\(\)[\s\S]*?return store\.model/);
  assert.match(html,/SELECTED RESEARCH TRACK · EVENT STUDY/);
  assert.match(html,/신호일 종가→h일 종가/);
  assert.match(app,/\$\{esc\(store\.eventAsset\)\} · \$\{esc\(sampleLabel\)\} · \$\{esc\(pairLabel\(store\.backtestProxy, true\)\)\}/);
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
  assert.match(app,/refresh_timeout:\s*"데이터 갱신 제한시간 초과"/);
  assert.match(app,/frozen_history_drift_requires_backfill:\s*"고정 이력 재검증 필요"/);
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

test('uncertainty chart and ETF common-period comparison fail closed without historical source authority UI',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="event-ci-chart"/);
  assert.match(html,/id="proxy-comparison"/);
  assert.match(app,/eventBenchmark/);
  assert.match(app,/renderProxyComparison/);
  assert.doesNotMatch(html,/PDF|원문 비교|주석 사건|pdf-snapshot/i);
  assert.doesNotMatch(app,/pdfReplica|renderPdfSnapshot|원문 근사/i);
});

test('event excess confidence intervals disclose how benchmark uncertainty is treated',async()=>{
  const app=await read('assets/app.js');
  assert.match(app,/meanExcessReturnCi95BenchmarkTreatment/);
  assert.match(app,/fixed_external_mean/);
  assert.match(app,/사건 평균 재표집·비교 평균 고정/);
  assert.match(app,/paired_event_returns/);
  assert.match(app,/사건·벤치마크 함께 재표집/);
});

test('controls persist to URL and localStorage and charts expose an explicit latest action',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="reset-controls"/);
  assert.match(html,/id="share-view"/);
  assert.ok((html.match(/data-chart-latest=/g)||[]).length>=4);
  assert.match(app,/CONTROL_STORAGE_KEY = "fearngreed-controls-v8"/);
  assert.match(app,/"fearngreed-controls-v7"/);
  assert.match(app,/localStorage\.setItem\(CONTROL_STORAGE_KEY/);
  assert.match(app,/history\.replaceState/);
  assert.match(app,/navigator\.clipboard\.writeText/);
  assert.match(app,/longExitPercentile:\s*"exit"/);
  assert.match(app,/backtestProxy:\s*"pair"/);
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

test('optional live signal is isolated from canonical contracts and uses the selected signal inputs',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  for(const id of ['live-signal-strip','live-phase-badge','live-signal-state','live-signal-score','live-signal-time','live-action-note','live-confirmed-anchor']) assert.match(html,new RegExp(`id="${id}"`));
  assert.match(html,/id="live-signal-strip"[^>]+aria-live="polite"[^>]+hidden/);
  assert.match(app,/function loadOptionalJson/);
  assert.match(app,/contract !== "fearngreed-live-signal"/);
  assert.match(app,/loadOptionalJson\("var\/live-signal-local\.json"\)/);
  assert.match(app,/live\.signalDate <= canonicalDate/);
  assert.match(app,/live\.signalDate !== kstToday/);
  assert.match(app,/const historyRows = \[\.\.\.rows, inputRow\]/);
  assert.match(app,/\.\.\.currentSignalConfig\(\)/);
  assert.match(app,/renderLiveSignal\(\)/);
  assert.match(app,/시간외 종가/);
  assert.match(app,/차트·백테스트는/);
  assert.match(css,/\.phase-badge\.provisional/);
  assert.match(css,/\.phase-badge\.confirmed/);
  assert.match(css,/@media \(max-width: 520px\)[\s\S]*?\.live-signal-strip\s*\{[^}]*grid-template-columns:\s*1fr/s);
});

test('KOSPI history supports calendar presets and validated shareable custom dates',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  for(const value of ['1m','3m','6m','ytd','1y','3y','all']) assert.match(html,new RegExp(`data-window="${value}"`));
  for(const id of ['history-range-form','history-start','history-end','history-follow-latest','history-range-status','history-exposure-note']) assert.match(html,new RegExp(`id="${id}"`));
  assert.match(app,/function applyCustomHistoryRange/);
  assert.match(app,/start > end/);
  assert.match(app,/start < firstDate \|\| end > latestDate/);
  assert.match(app,/store\.window = "custom"/);
  assert.match(app,/historyStart:\s*"start"/);
  assert.match(app,/historyEnd:\s*"end"/);
  assert.match(app,/historyEndMode:\s*"endMode"/);
  assert.match(app,/store\.historyEndMode === "latest"/);
  assert.match(app,/최신일 자동 추종/);
  assert.match(app,/종료일 고정/);
  assert.match(app,/url\.searchParams\.delete\(param\)/);
  assert.match(app,/\{ "252": "1y", "756": "3y" \}/);
  assert.match(app,/function renderResidual\(\)[\s\S]*?const rows = selectedHistory\(\)/);
  assert.match(app,/store\.window === "custom"[\s\S]*?startDate: store\.historyStart/);
  assert.match(css,/\.history-custom-range/);
  assert.match(css,/\.history-range-status\[data-state="error"\]/);
});

test('integrated history separates close signals from next-open actual ETF actions and uses scenario exposure',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  assert.match(html,/공포 원과 탐욕 마름모는 종가 상태의 첫 관측입니다/);
  assert.match(html,/같은 시가의 청산과 반대 ETF 매수는 하나의 교체로 표시합니다/);
  assert.match(app,/function extremeSignalMap/);
  assert.match(app,/function scenarioActions/);
  assert.match(app,/class="execution-action (entry|exit|reversal)/);
  assert.match(app,/primary\.metrics/);
  assert.match(app,/m\.grossExposure/);
  assert.match(app,/excludedCarryInClosedTrades/);
  assert.match(css,/\.execution-action path/);
  assert.match(css,/\.holding-zone\.short, \.holding-zone\.inverse/);
});

test('integrated history uses readable axes, explicit policy lanes, and only visible series for its performance domain',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  assert.match(html,/id="history-chart-meta"/);
  for(const series of ['kospi','long_cash','long_inverse_cash','buyhold']) assert.match(html,new RegExp(`data-history-series="${series}"`));
  assert.match(html,/aria-label="신호와 체결 기호"/);
  assert.match(app,/function niceTicks/);
  assert.match(app,/function chartDateAxis/);
  assert.match(app,/const visibleReturnFields = \[[\s\S]*showLongCash[\s\S]*showLongShort[\s\S]*"buyHoldReturn"/);
  assert.match(app,/strategyValues = plotRows\.flatMap\(\(row\) => visibleReturnFields\.map/);
  assert.match(app,/label: "롱 \/ 현금"[\s\S]*label: "롱 \/ 인버스"/);
  assert.match(app,/성과 · 비용 후 누적수익률/);
  assert.match(app,/날짜 \(KRX 거래일 · KST\)/);
  assert.match(app,/includedInWindowMetrics === false \? " boundary-context"/);
  assert.match(css,/\.chart-meta-strip/);
  assert.match(css,/\.date-grid-line/);
  assert.match(css,/\.line-end-label/);
  assert.match(css,/white-space:\s*pre-line/);
  assert.match(css,/\.chart svg \{[^}]*overflow:\s*hidden/s);
});

test('chart selection snapshot is dynamic while period-end cards and tables stay fixed',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  for(const id of ['history-selected-snapshot','history-selected-title','history-selected-content','history-chart-callout','history-chart-date','history-callout-series','history-callout-value','history-data-date','history-evaluation-date']) assert.match(html,new RegExp(`id="${id}"`));
  assert.match(html,/가리켜 미리 보고 클릭·탭·화살표 키로 선택일을 고정합니다/);
  assert.doesNotMatch(html,/차트 선택은 평가 종료일 성과를 바꾸지 않습니다/);
  assert.match(html,/평가 종료일 성과/);
  assert.match(app,/function renderHistorySelectedSnapshot/);
  assert.match(app,/function historySeriesValueText/);
  assert.match(app,/persistSelection: true/);
  assert.match(app,/showTooltip: false/);
  assert.match(app,/typeof geometry\.onSelect === "function"/);
  assert.match(app,/chart\._selectLatest = \(\) => selectIndex\(latestIndex, null, \{ commit: persistSelection, phase: "latest" \}\)/);
  assert.match(app,/chart\._selectLatest\?\.\(\)/);
  assert.match(app,/const scenarioBundle = selectedScenarioBundle\(\);[\s\S]*renderHistory\(scenarioBundle\)[\s\S]*renderBacktests\(scenarioBundle\)/);
  assert.match(css,/\.history-selected-snapshot/);
  assert.match(css,/\.unified-strategy-chart\.has-active-series \.history-series:not\(\.is-active\)/);
  assert.match(css,/@media \(max-width: 520px\)[\s\S]*?\.history-series-controls \{ display: grid/);
  assert.match(css,/@media \(max-width: 520px\)[\s\S]*?\.history-chart-callout \{ grid-template-columns:/);
});

test('scatter refits the selected historical session and renders exact empirical state boundaries',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  assert.match(html,/극단적 공포 영역/);
  assert.match(html,/극단적 탐욕 영역/);
  assert.match(app,/function selectedScatterFit/);
  assert.match(app,/fitDynamicSignalAt/);
  assert.match(app,/fit\?\.residualCuts/);
  assert.match(app,/typeof value !== "number"/);
  for(const field of ['extremeFearUpper','fearUpper','greedLower','extremeGreedLower']) assert.match(app,new RegExp(field));
  assert.match(app,/clipPath id="scatter-plot-clip"/);
  assert.match(app,/선택 종료일의 과거 전용 회귀/);
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
  assert.match(app,/selectedScatterFit\(\)/);
  assert.match(app,/fit\.trainingRows/);
});

test('signal settings atomically refit signals, events and strategy with bounded inputs',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="signal-lookback-input"[^>]+min="60"[^>]+max="756"/);
  assert.match(html,/id="signal-min-r2-input"[^>]+min="0"[^>]+max="0\.8"[^>]+step="0\.05"/);
  assert.match(html,/id="signal-tail-input"[^>]+min="1"[^>]+max="20"/);
  assert.match(html,/id="signal-max-holding-input"[^>]+min="1"[^>]+max="60"/);
  assert.match(app,/function recomputeDynamicResearch/);
  assert.match(app,/computeDynamicSignals/);
  assert.match(app,/recomputeDynamicResearch\(\);[\s\S]*?resultsForPolicySelection/);
  assert.match(app,/maxHoldDays: store\.signalMaxHolding/);
  assert.match(app,/runDynamicEventStudy/);
  assert.match(app,/historyScenario\?\.pastOnly !== true/);
});

test('integrated controls fail closed before load and keep selected-date diagnostics consistent',async()=>{
  const [html,app,css]=await Promise.all([read('index.html'),read('assets/app.js'),read('assets/styles.css')]);
  assert.match(html,/id="signal-min-r2-input"[^>]+required/);
  assert.match(app,/function setResearchControlsEnabled/);
  assert.match(app,/setResearchControlsEnabled\(false\)/);
  assert.match(app,/setResearchControlsEnabled\(true\)[\s\S]*?renderAll\(\)/);
  assert.match(app,/function selectedModelAgreement/);
  assert.match(app,/절대수급·규모보정 방향 혼재/);
  assert.match(app,/const selectedModel = modelPayload\(\)/);
  assert.match(app,/basisDate: selectedDate/);
  assert.match(app,/latestEventError/);
  assert.match(app,/사건 연구 계산 오류로 결과를 표시하지 않습니다/);
  assert.match(app,/const form = event\.currentTarget/);
  assert.doesNotMatch(app,/event\.currentTarget\.setAttribute/);
  assert.match(css,/@media \(max-width: 900px\)[\s\S]*?\.chart-grid \{ grid-template-columns: 1fr; \}/);
});

test('normalized benchmark equity and future flow channels remain explicit and fail closed',async()=>{
  const [html,app]=await Promise.all([read('index.html'),read('assets/app.js')]);
  assert.match(html,/id="flow-channels"/);
  assert.match(app,/commonBenchmarkEquity/);
  assert.match(app,/function hydratedEquityRows/);
  assert.match(app,/flowChannels\?\.channels/);
  assert.match(app,/future_extension/);
  assert.match(app,/primary \? "1차 신호" : "진단 가능"/);
  assert.match(app,/낮음 · 거래 미사용/);
  assert.match(app,/collecting \? "수집 중"/);
  assert.match(app,/collecting \? "표본 부족"/);
  assert.match(app,/선택 조합의 거래 상세가 없습니다/);
});
