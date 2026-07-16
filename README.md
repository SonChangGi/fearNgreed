# Fear & Greed Flow Lab

KOSPI 수익률 대비 개인 순매수의 비정상성을 과거 정보만으로 측정하는 공개 퀀트 리서치다. 원문 사례를 그대로 복제하지 않고, 규모 보정 수급 회귀와 원문 충실 raw-flow 회귀를 분리한다.

현재 커밋된 데이터는 UI와 계약을 검증하기 위한 **합성 fixture**다. 실제 KRX/pykrx 인증 데이터가 들어오기 전에는 `degraded`이며 투자 신호나 추천으로 사용할 수 없다.

## 로컬 검증

```bash
uv sync
uv run --frozen pytest
uv run --frozen ruff check .
npm test
python3 -m http.server 8000
```

브라우저에서 `http://127.0.0.1:8000/`을 연다.

## 공개 계약

- `data/summary.json`: Quant Dashboard용 경량 계약
- `data/dashboard.json`: 현재 차트·사건·백테스트 요약
- `data/history.json`: 공개 가능한 일별 파생 시계열
- `data/automation-status.json`: 갱신 상태

방법론은 [docs/methodology.md](docs/methodology.md), 데이터/비밀정보 경계는 [docs/data-contract.md](docs/data-contract.md)를 참고한다.
