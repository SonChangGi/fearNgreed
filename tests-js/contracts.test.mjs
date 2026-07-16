import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const json=async path=>JSON.parse(await readFile(new URL(`../${path}`,import.meta.url),'utf8'));

test('summary exposes the fixed hub contract backed by non-fixture data',async()=>{const [summary,history,dashboard]=await Promise.all([json('data/summary.json'),json('data/history.json'),json('data/dashboard.json')]);assert.equal(summary.schemaVersion,1);assert.equal(summary.contract,'quant-research-summary');assert.equal(summary.projectId,'fearngreed');assert.ok(['ok','degraded','stale','unavailable'].includes(summary.status.state));assert.equal(history.fixture,false);assert.notEqual(dashboard.fixture,true);assert.ok(summary.dataAsOf<=summary.generatedAt.slice(0,10));assert.equal(summary.dataAsOf,history.dataAsOf);assert.equal(summary.dataAsOf,dashboard.dataAsOf);});
test('public json stays within size budgets and contains no secret canaries',async()=>{for(const [file,limit] of [['summary.json',50_000],['dashboard.json',500_000],['history.json',2_000_000],['automation-status.json',50_000]]){const text=await readFile(new URL(`../data/${file}`,import.meta.url),'utf8');assert.ok(Buffer.byteLength(text)<limit);assert.doesNotMatch(text,/KRX_(API_KEY|ID|PW)|gho_|password/i);}});
test('static page labels limitations and research status',async()=>{const html=await readFile(new URL('../index.html',import.meta.url),'utf8');assert.match(html,/사후적·탐색적 연구 신호/);assert.match(html,/사건 검증/);assert.match(html,/투자 권유 아님/);});
