# 데이터 및 공개 계약

KRX Open API의 KOSPI·ETF·유가증권 가격·거래대금은 1차 소스, 인증된 pykrx의 KOSPI 개인 순매수는 보완 소스, yfinance 조정가격은 연구용 2차 소스다. Open API 서비스 활용권한이 없거나 인증에 실패하면 인증된 pykrx KRX 가격 경로로 fail-closed 전환하되 공개 상태를 `degraded`로 두고 이유를 남긴다. 이를 Open API와 동등한 정상 상태로 표시하지 않는다. ETF 공식 일별값과 삼성전자·SK하이닉스 공식 종가는 최근 교차검증창만 조회하며, `dataAsOf`가 양쪽 공급자의 공통 최신일이고 종가 차이가 0.5% 이내일 때만 관련 백테스트·반도체 진단을 공개한다. 전체 백테스트는 배당·분할 반영 조정가격을 사용한다. ETF/ETN/ELW 수급은 제외한다. 미국 종가는 해당 KRX 거래일 전에 이용 가능했던 마지막 세션으로만 정렬한다.

`KRX_API_KEY`, `KRX_ID`, `KRX_PW`는 GitHub Actions Secrets 또는 일회성 환경변수로만 주입한다. 인증 구간의 stdout/stderr는 캡처하며 요청 헤더, 쿠키, 원응답과 예외 객체를 공개하지 않는다. `references/private/`, `.env*`, 세션 파일과 원시 인증 응답은 Git 및 Pages 산출물에서 제외한다.

`summary.json`은 `schemaVersion=1`, `contract=quant-research-summary`, `projectId=fearngreed`, `methodologyVersion=fear-flow-v1`을 고정한다. 엄격한 JSON Schema가 상태·범위·엔티티·scaled/raw 최신 모형·포지션·대기 주문·출처·자동화·payload의 필드, 범위, enum과 추가 속성을 검증한다. 최신성은 `generatedAt`이 아니라 `dataAsOf`로 판단한다. 화면은 세 공개 JSON의 방법론 버전과 기준일이 모두 일치하지 않으면 `unavailable`로 닫히며 임의 수치나 브라우저 재계산으로 대체하지 않는다.

`dashboard.json`은 252개 학습 관측과 현재 관측 1개의 역할, 사건 자산·표본별 요약, ETF·비용·변형·기간별 사전 계산 백테스트, 공식 종가 교차검증과 반도체 상대 진단을 둔다. `history.json`은 일별 파생 시계열과 최신 scaled/raw 모형 스냅샷만 공개한다. 목표 크기는 각각 500KB와 2MB 미만이며 테스트에서 강제한다.

갱신은 최신 5거래일을 수정 가능 구간으로 삼아 캐시를 강제 재검증하고, 임시파일을 완전히 쓰고 `fsync`한 뒤 원자적으로 교체한다. 실패하면 기존 신호·사건·백테스트를 유지하고 `summary.status`와 `automation-status.json`에 승인된 짧은 사유만 원자적으로 기록한다. 실패 실행도 이 두 파일만 검증·커밋해 공개 상태를 알린 뒤 Actions 자체는 실패로 남긴다. Pages 산출물은 `index.html`, `assets/`, `data/`, `docs/`, `schemas/`만 허용하며 `references/`는 복사하지 않는다.
