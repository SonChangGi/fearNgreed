# Fear & Greed Flow Lab

KOSPI 수익률 대비 개인 순매수의 비정상성을 과거 정보만으로 측정하는 공개 퀀트 리서치다. 실전 후보인 규모보정 강건 회귀, 감사 기준선인 규모보정 OLS, 명목 원화 수급을 보는 절대수급 OLS를 같은 입력과 평가 기간에서 비교한다.

공개 수치는 공급자 품질 게이트를 통과한 파생값만 기록한다. `degraded`는 마지막 정상 수치를 유지하면서 Open API 권한, 교차검증 또는 갱신 문제가 남아 있다는 뜻이며, 임의 fallback 시장 수치를 뜻하지 않는다.

첫 화면은 전체 극단 사건 연구와 실제 상장 ETF로 실행한 롱/현금·롱/인버스/현금 경로를 함께 읽어 근거 우선 결론을 만든다. 기본 1X 페어는 KODEX 200(069500)과 KODEX 인버스(114800), 고위험 비교 2X 페어는 KODEX 레버리지(122630)와 KODEX 200선물인버스2X(252670)다. 서버가 검증해 발행하는 기본 청산선은 롱 80·인버스 20 백분위다. 평가 종료일에 거래가 열려 있으면 ETF·진입 신호일·체결일·진입가·보유일·미실현손익·다음 예정 행동을 완결 거래표와 분리해 표시한다.

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
- `data/live-signal.json`: KST 당일에만 표시되는 15:47 잠정 입력·모형 스냅샷
- `schemas/summary.schema.json`: Quant Dashboard 계약의 엄격한 JSON Schema
- `schemas/live-signal.schema.json`: 잠정 신호의 날짜·수집창·모형 계약

방법론은 [docs/methodology.md](docs/methodology.md), 데이터/비밀정보 경계는 [docs/data-contract.md](docs/data-contract.md)를 참고한다.

## 자동화

공식 데이터 자동화는 평일 18:15·18:45·20:30 KST에 순서대로 시도한다. 18:15·18:45에는 KRX의 당일 공식 세션이 아직 없으면 공개 파일을 전혀 바꾸지 않고 다음 실행으로 넘긴다. 첫 성공 뒤 같은 날짜의 후속 실행은 공급자 호출·파일 변경·커밋·배포가 없는 true no-op이다. 20:30에는 Open API와 인증 KRX 당일 세션을 함께 확인해 휴장일이면 역시 아무 파일도 바꾸지 않고 종료하며, Open API만 늦고 인증 KRX에 당일 세션이 있으면 인증 경로로 확정 분석을 계속한다. 실제 거래일인데 최종 시도까지 실패한 경우에만 마지막 정상 시장 결과를 보존한 채 `stale` 또는 `degraded` 운영 상태를 발행한다. 모든 예약 실행은 KST 당일 날짜를 명시적으로 전달하므로 20:30 이전 실행이 전일을 잘못 선택하지 않는다.

정상 이력이 있으면 최신 5거래일의 KRX 캐시를 다시 검증하고 그 이전 파생 이력은 고정한다. 수정 불가 구간과 겹치는 Yahoo 조정가격 앵커를 별도로 대조한다. 과거 가격·수급·상태·출처 해시는 정확히 같아야 하며, 8자리 공개값을 다시 계산할 때 확인된 `residualZ`·`fitScore` 직렬화 오차만 필드별 작은 허용범위로 비교한다. 이 범위를 벗어나면 새·옛 이력을 섞지 않고 `requires_backfill`로 실패한다. KRX Open API가 공식 최신 완료 세션을 확정하고 KOSPI·개인수급의 최신 공통일이 그 세션과 정확히 같을 때만 신호·사건·백테스트를 갱신한다. 공식일을 확인할 수 없거나 공개 기준일보다 과거로 후퇴하는 실행은 쓰기 전에 중단한다. Actions는 수집 receipt의 공식 기대일로 로컬·배포 JSON을 다시 검증한다. 각 pykrx 요청에는 20초 기본 timeout, 전체 확정 갱신에는 35분 watchdog을 적용한다. timeout이나 공급자 실패 시에도 마지막 정상 시장 결과와 `stale/degraded` 상태 계약을 검증해 배포한다. GitHub Actions에는 사용자가 직접 `KRX_API_KEY`, `KRX_ID`, `KRX_PW` repository 또는 `github-pages` environment secrets를 등록해야 한다. 로컬 Keychain 값은 GitHub로 자동 복사하지 않는다.

Actions의 수동 실행에서 `official_data_date`에 오늘보다 이전인 완료 거래일을 지정하면 18:15 시각 제한 없이 그 날짜를 정확히 재수집할 수 있다. 이 입력은 probe·backfill과 함께 사용할 수 없고, KRX가 해당 날짜를 실제 완료 세션으로 확인하지 않으면 발행하지 않는다.

GitHub Secrets가 아직 비어 있어도 이 Mac에서는 같은 확정 시각에 로컬 Keychain 경로가 동작한다. 로컬 확정 작업은 사용자 작업 폴더를 수정하지 않고 매 실행마다 격리된 임시 clone을 만든 뒤, 전체 테스트·계약·비밀정보 검사를 통과한 `data/` 변경만 원격 `main`에 푸시한다. 원격 HEAD가 계산 중 바뀌면 rebase나 force-push 없이 중단한다.

```bash
scripts/install-official-refresh-launch-agent
```

15:47 KST 잠정 신호는 시간외 종가매매 창 안에서 빠르게 확인할 수 있도록 로그인된 Mac의 `launchd`에서 별도로 계산한다. 공식 JSON이나 Git 이력은 건드리지 않고 `var/live-signal-local.json`만 갱신하며, 공급자 데이터가 늦으면 15:50·15:53·15:56까지 제한적으로 재시도한다. 로컬 페이지는 이 파일과 공개 `data/live-signal.json` 중 더 최신인 관측을 읽는다. GitHub의 `live-signal.yml`도 평일 15:47 KST에 같은 별도 계약을 best-effort로 수집·검증·배포하지만, 예약 시작과 Pages 반영 시각은 보장되지 않으므로 시간 민감 알림은 로컬 경로를 사용한다.

```bash
scripts/install-live-signal-launch-agent
```

Mac이 잠들어 있거나 로그아웃된 경우 15:47 잠정 신호와 로컬 확정 fallback은 보장되지 않는다. 16:00 이후 깨어난 잠정 실행은 만료된 신호를 만들지 않고 종료한다. GitHub Secrets를 직접 등록하면 동일한 공식 18:15·18:45·20:30 워크플로가 GitHub-hosted runner에서도 독립적으로 동작한다. `data/live-signal.json`은 당일 입력과 기본 3개 모형만 담으며 확정 `history.json`, 사건 연구, 차트 성과, 백테스트에는 들어가지 않는다.

```bash
with-krx-keychain uv run --frozen python -m fearngreed.refresh --backfill-start-date 2010-01-04
```
