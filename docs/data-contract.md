# 데이터 및 공개 계약

KRX Open API 가격·거래대금은 1차 소스, 인증된 pykrx의 KOSPI 개인 순매수는 보완 소스, yfinance 조정가격은 연구용 2차 소스다. Open API 서비스 활용권한이 없거나 인증에 실패하면 인증된 pykrx KRX 가격 경로로 fail-closed 전환하되 공개 상태를 `degraded`로 두고 이유를 남긴다. 이를 Open API와 동등한 정상 상태로 표시하지 않는다. ETF 공식 일별값은 최근 교차검증창만 조회하고, 전체 백테스트는 배당·분할 반영 조정가격을 사용한다. ETF/ETN/ELW 수급은 제외한다. 미국 종가는 해당 KRX 거래일 전에 이용 가능했던 마지막 세션으로만 정렬한다.

`KRX_API_KEY`, `KRX_ID`, `KRX_PW`는 GitHub Actions Secrets 또는 일회성 환경변수로만 주입한다. 인증 구간의 stdout/stderr는 캡처하며 요청 헤더, 쿠키, 원응답과 예외 객체를 공개하지 않는다. `references/private/`, `.env*`, 세션 파일과 원시 인증 응답은 Git 및 Pages 산출물에서 제외한다.

`summary.json`은 `schemaVersion=1`, `contract=quant-research-summary`, `projectId=fearngreed`를 고정한다. 최신성은 `generatedAt`이 아니라 `dataAsOf`로 판단한다. 실제 공급자 검증 전 fixture는 반드시 `degraded`로 공개한다.

갱신은 임시파일을 완전히 쓰고 `fsync`한 뒤 원자적으로 교체한다. 실패하면 기존 신호·사건·백테스트를 유지하고 `summary.status`와 `automation-status.json`에 승인된 짧은 사유만 기록한다. Pages 산출물은 `index.html`, `assets/`, `data/`, `docs/`, `schemas/`만 허용하며 `references/`는 복사하지 않는다.
