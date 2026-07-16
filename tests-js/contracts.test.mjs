import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const json=async path=>JSON.parse(await readFile(new URL(`../${path}`,import.meta.url),'utf8'));

test('summary exposes the fixed hub contract and remains degraded for fixture data',async()=>{const data=await json('data/summary.json');assert.equal(data.schemaVersion,1);assert.equal(data.contract,'quant-research-summary');assert.equal(data.projectId,'fearngreed');assert.equal(data.status.state,'degraded');assert.ok(data.status.degradedReasons.includes('synthetic_fixture'));assert.ok(data.dataAsOf<=data.generatedAt.slice(0,10));});
test('public json stays within size budgets and contains no secret canaries',async()=>{for(const [file,limit] of [['summary.json',50_000],['dashboard.json',500_000],['history.json',2_000_000],['automation-status.json',50_000]]){const text=await readFile(new URL(`../data/${file}`,import.meta.url),'utf8');assert.ok(Buffer.byteLength(text)<limit);assert.doesNotMatch(text,/KRX_(API_KEY|ID|PW)|gho_|password/i);}});
test('static page labels fixture limitations and research status',async()=>{const html=await readFile(new URL('../index.html',import.meta.url),'utf8');assert.match(html,/탐색적 연구 신호/);assert.match(html,/실제 데이터 백필 전/);assert.match(html,/투자 권유 아님/);});
