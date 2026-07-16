# Fear & Greed Flow Lab

KOSPI 수익률 대비 개인 순매수의 비정상성을 과거 정보만으로 측정하는 공개 퀀트 리서치다. 원문 사례를 그대로 복제하지 않고, 규모 보정 수급 회귀와 원문 충실 raw-flow 회귀를 분리한다.

공개 수치는 공급자 품질 게이트를 통과한 파생값만 기록한다. `degraded`는 마지막 정상 수치를 유지하면서 Open API 권한, 교차검증 또는 갱신 문제가 남아 있다는 뜻이며, 임의 fallback 시장 수치를 뜻하지 않는다.

첫 화면은 20일 비중첩 사건 연구와 기본 226490 롱/현금 모형을 함께 읽어 근거 우선 결론을 만든다. 모델·사건 자산·표본·ETF·거래비용·진입 변형·기간 컨트롤은 `dashboard.json`에 이미 발행된 결과만 선택하며 브라우저에서 회귀나 백테스트를 다시 계산하지 않는다.

화면 셸은 Quant Research Hub와 연결 프로젝트의 light-first 파란색 체계, 공통 프로젝트 메뉴, 테마 저장 키와 위·아래 빠른 이동 방식을 독립 코드로 구현한다. 차트는 포인터 crosshair와 키보드 탐색을 함께 제공하고, 핵심 표는 열 정렬·거래 검색·현재 선택 결과 CSV 저장을 지원한다.

## 로컬 검증

```bash
uv sync
uv run --frozen pytest
uv run --frozen ruff check .
npm test
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
- `data/automation-status.json`: 갱신 상태
- `schemas/summary.schema.json`: Quant Dashboard 계약의 엄격한 JSON Schema

방법론은 [docs/methodology.md](docs/methodology.md), 데이터/비밀정보 경계는 [docs/data-contract.md](docs/data-contract.md)를 참고한다.

## 자동화

평일 20:30 KST에 갱신한다. 정상 이력이 있으면 최신 5거래일의 KRX 캐시를 다시 검증하고 그 이전 파생 이력은 고정한다. 같은 기준일과 파생값이면 `noOp`으로 끝나며, 더 오래된 구간을 고치려면 경계를 명시한 수동 백필이 필요하다. 공급자 실패 시 마지막 정상 시장 산출물을 보존하고 `summary`·`automation-status`만 원자적으로 degraded로 발행한 뒤 Actions 실행은 실패로 표시한다. GitHub Actions에는 사용자가 직접 `KRX_API_KEY`, `KRX_ID`, `KRX_PW` repository secrets를 등록해야 한다. 로컬 Keychain 값은 GitHub로 자동 복사하지 않는다.

```bash
with-krx-keychain uv run --frozen python -m fearngreed.refresh --backfill-start-date 2010-01-04
```
