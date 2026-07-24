# Fear & Greed 계산 경계와 Control API 준비 상태

감사 기준 리비전은 `e1e1e72`, 공개 방법론은 `fear-flow-v5`다. 이 변경은
기존 Python 분석식, 공개 JSON, 데이터 이력과 화면 구조를 바꾸지 않는다.
브라우저와 Python이 서로 다른 계산을 같은 결과처럼 취급하지 않도록 실행
권위를 한 곳으로 고정한다.

기계 판독 원본은
[`contracts/fear-parity-matrix.v1.json`](../contracts/fear-parity-matrix.v1.json),
교차 런타임 fixture는
[`tests/fixtures/fear-parity-v1.json`](../tests/fixtures/fear-parity-v1.json)이다.

## 결론

Backend 연결을 위한 parity gate는 **준비 완료**다. 준비 완료의 의미는
JavaScript와 Python에 같은 통계 알고리즘을 두 벌 유지한다는 뜻이 아니다.
분석 결과의 유일한 권위를 Python으로 정하고, 브라우저의 현재 계산은 연결
완료 전까지 `preview_only_not_result_authority`로 한정한다.

- 신호 회귀, 실제 ETF 전략, 사건 선택의 공통 fixture는 기존처럼 수치
  parity를 검증한다.
- 가변 극단 꼬리는 새 Python control 계약이 1~20% 입력을 명시적으로 받아
  브라우저와 같은 상태 경계를 만든다. 기존 `model.classify_percentile`과
  공개 `fear-flow-v5` 기본 5/95는 변경하지 않는다.
- 사건 신뢰구간과 초과수익은 브라우저에서 정식 결과로 승격하지 않는다.
  Python의 NumPy PCG64 이동블록 bootstrap과 무조건부 선행수익률 benchmark만
  서버 결과 권위로 사용한다.
- 화면의 16개 입력은 `fear-greed/control-inputs-v1`에서 `requested`,
  `normalized`, `effective`, `configHash`, `inputSchemaHash`로 결합된다.

즉, 차이는 숫자를 억지로 맞춰 없앤 것이 아니라 이중 권위 자체를 제거해
해결했다.

## 보이는 입력 계약

| 화면 입력 | 기본값 | 연결 전 | 연결 후 결과 권위 |
| --- | --- | --- | --- |
| 평가 기간 | YTD, 최신 추종 | 브라우저 미리보기 | Python control result |
| 연구 트랙 | raw | 브라우저 미리보기 | Python control result |
| 사건 자산·표본 | KOSPI · 전체 | 브라우저 미리보기 | Python moving-block summary |
| ETF 페어·정책 | 1X · 비교 | 브라우저 미리보기 | Python 실제 ETF backtest |
| 비용·청산선 | 10bp · 80/20 | 브라우저 미리보기 | Python 실제 ETF backtest |
| 학습창·최소 R² | 196 · 0.40 | 브라우저 미리보기 | Python rolling fit |
| 극단 꼬리 | 2/98 | 브라우저 미리보기 | Python control classification |
| 최대 보유 | 20거래일 | 브라우저 미리보기 | Python 실제 ETF backtest |

테마, 차트 선 표시, 차트 선택일, 최신일 이동과 표 검색은 `display`다.
CSV 저장, 초기화와 링크 복사는 `operation`이다. 이 조작은 분석 적용으로
표시하지 않는다.

## 기본값 세 층

- 화면 기본: 사용자가 처음 여는 `raw / 196 / 0.40 / 2` 시나리오
- Python control 기본: 화면 기본과 정확히 같은 완전 입력 객체
- Python 공개 기본: 기존 `fear-flow-v5`의 `robust / 252 / 0.20 / 5`

공개 기본은 변경하지 않는다. Control API는 화면 입력을 받는 별도 실행
계약이며, 응답에 어떤 값이 실제 적용됐는지 식별자를 함께 돌려준다.

## 결과 parity

### 롤링 신호

`alpha`, `beta`, `rollingR2`, `fitScore`, `expected`, `residual`,
`residualZ`, `percentile`, `quality`, `trainingCount`, `tradeEligible`를
절대오차 `1e-10`으로 비교한다. 상태는 control 입력의 가변 꼬리로 Python과
브라우저가 exact 일치한다.

### 실제 ETF 전략

1X·2X와 롱/현금·롱/인버스/현금 네 경로의 포지션, 대기 행동, 성과 지표,
거래, 행동 로그와 일별 자산 경로를 비교한다. 날짜·상태·티커·행동은 exact,
숫자는 `1e-10` 이내여야 한다.

### 사건 연구

사건일, 상태, 백분위와 선행수익률은 공통 fixture로 비교한다. 신뢰구간,
benchmark 평균과 초과수익은 Python 결과만 정식 응답에 포함한다. 브라우저
iid 요약은 연결 전 미리보기이며 서버 결과와 병합하지 않는다.

## 실행

```bash
UV_CACHE_DIR=/private/tmp/fear-parity-uv npm run parity:require-ready
```

이 명령은 다음을 함께 확인한다.

1. parity matrix의 모든 필수 경로가 `verified`인지
2. Python reference와 JavaScript 공통 수치 경로가 일치하는지
3. 가변 꼬리 상태가 일치하는지
4. 사건 정식 요약이 Python moving-block 권위인지
5. 화면 기본 16개 입력의 요청·정규화·적용 값과 hash가 결합되는지

하나라도 어긋나면 종료 코드가 0이 아니므로 backend 연결과 배포를 중단한다.

## Backend 연결 시 반드시 지킬 조건

1. 완전한 `fear-greed/control-inputs-v1` 요청만 받는다.
2. 신호·사건·전략을 하나의 Python-bound 결과로 원자적으로 교체한다.
3. 응답에 `configHash`, `effectiveConfigHash`, `inputSchemaHash`,
   `codeIdentity`, `dataIdentity`, artifact SHA-256을 포함한다.
4. 실패하면 현재 검증 결과를 유지하고 새 입력이 적용됐다고 표시하지 않는다.
5. GitHub Pages의 기존 Python 정식 JSON 경로를 fallback으로 보존한다.
