# Fear & Greed Flow Lab

KOSPI 수익률 대비 개인 순매수의 비정상성을 과거 정보만으로 측정하는 공개 퀀트 리서치다. 원문 사례를 그대로 복제하지 않고, 실전 후보인 규모보정 강건 회귀, 감사 기준선인 규모보정 OLS, PDF 원문 근사인 절대수급 OLS를 분리한다.

공개 수치는 공급자 품질 게이트를 통과한 파생값만 기록한다. `degraded`는 마지막 정상 수치를 유지하면서 Open API 권한, 교차검증 또는 갱신 문제가 남아 있다는 뜻이며, 임의 fallback 시장 수치를 뜻하지 않는다.

첫 화면은 20일 비중첩 사건 연구와 실제 상장 ETF로 실행한 롱/현금·롱/인버스/현금 경로를 함께 읽어 근거 우선 결론을 만든다. 기본 1X 페어는 KODEX 200(069500)과 KODEX 인버스(114800), 고위험 비교 2X 페어는 KODEX 레버리지(122630)와 KODEX 200선물인버스2X(252670)다. 서버가 검증해 발행하는 기본 청산선은 롱 80·인버스 20 백분위다.

사용자가 화면에서 회귀창·최소 R²·극단 꼬리·최대 보유기간·청산선·평가기간·ETF 배율을 바꾸면 브라우저는 공개된 일별 수급과 각 ETF 조정시가·종가로 신호와 실제 ETF 거래 경로를 과거 전용으로 다시 계산한다. 청산선 50~94는 임의의 자유도 제한이 아니다. 50 미만은 공포에서 중립으로 돌아왔다는 회복 규칙의 의미가 약해지고, 95 이상은 사전 정의된 극단적 탐욕·인버스 전환 영역과 겹치므로 분리한다.

인버스 포지션은 기초 ETF의 합성 공매도가 아니라 114800 또는 252670을 실제 가격으로 매수한다. 따라서 대차·리콜 가정은 필요 없지만, 일간 목표배율 재조정, 선물 롤, 보수와 추적차이에서 생기는 실제 가격 경로를 그대로 부담한다. 특히 2X는 누적기간 수익률의 정확한 ±2배가 아니라 일간 목표 ±2배 상품이므로 기본값으로 승격하지 않고 1X와 같은 신호의 민감도 비교로 둔다.

화면 셸은 Quant Research Hub와 연결 프로젝트의 light-first 파란색 체계, 공통 프로젝트 메뉴, 테마 저장 키와 위·아래 빠른 이동 방식을 독립 코드로 구현한다. 차트는 포인터 crosshair와 키보드 탐색을 함께 제공하고, 핵심 표는 열 정렬·거래 검색·현재 선택 결과 CSV 저장을 지원한다.

## 로컬 검증

```bash
uv sync
uv run --frozen pytest
uv run --frozen ruff check .
npm test
uv run --frozen python -m fearngreed.verify
with-krx-keychain --check
with-krx-keychain uv run --frozen python -m fearngreed.refresh --probe
with-krx-keychain uv run --frozen python -m fearngreed.refresh
uv run --frozen python -m fearngreed.site --output dist
python3 -m http.server 8000
```

브라우저에서 `http://127.0.0.1:8000/`을 연다.

## 공개 계약

- `data/summary.json`: Quant Dashboard용 경량 계약
- `data/dashboard.json`: 현재 차트·사건·백테스트 요약
- `data/history.json`: 공개 가능한 일별 파생 시계열
- `data/strategy-comparison.json`: 실제 1X·2X ETF 페어, 기본 80/20 정책, 청산선 민감도와 동적 시나리오 계약
- `data/automation-status.json`: 갱신 상태
- `schemas/summary.schema.json`: Quant Dashboard 계약의 엄격한 JSON Schema

방법론은 [docs/methodology.md](docs/methodology.md), 데이터/비밀정보 경계는 [docs/data-contract.md](docs/data-contract.md)를 참고한다.

## 자동화

평일 20:30 KST에 갱신한다. 정상 이력이 있으면 최신 5거래일의 KRX 캐시를 다시 검증하고 그 이전 파생 이력은 고정한다. 수정 불가 구간과 겹치는 Yahoo 조정가격 앵커를 별도로 대조하고, 과거 가격 스케일 또는 공개 파생 행의 어느 값이라도 달라지면 새·옛 이력을 섞지 않고 `requires_backfill`로 실패한다. 공급자별 최신일이 다르면 더 늦은 행을 억지로 결합하지 않고 최신 공통 거래일까지 계산하며 `degraded` 사유를 남긴다. 같은 기준일과 파생값이면 `noOp`으로 끝나며, 더 오래된 구간을 고치려면 경계를 명시한 수동 백필이 필요하다. 공급자 실패 시 마지막 정상 시장 산출물을 보존하고 `summary`·`automation-status`만 원자적으로 degraded로 발행한 뒤 Actions 실행은 실패로 표시한다. GitHub Actions에는 사용자가 직접 `KRX_API_KEY`, `KRX_ID`, `KRX_PW` repository secrets를 등록해야 한다. 로컬 Keychain 값은 GitHub로 자동 복사하지 않는다.

```bash
with-krx-keychain uv run --frozen python -m fearngreed.refresh --backfill-start-date 2010-01-04
```
